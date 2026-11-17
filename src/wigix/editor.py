# SPDX-License-Identifier: GPL-3.0-only
"""Local web editor: drag widgets around a live preview, edit their settings, save to the YAML.

Serves a small single-page app plus a JSON API on 127.0.0.1. The running daemon hot-reloads the
config file, so Save updates the panel.

Access control: every request needs the editor's secret token (kept in a 0600 file next to the config; `wigix url`
prints the link). The first visit to /?token=... stores it in an HttpOnly SameSite=Strict cookie. On top of that: a
Host-header allow-list (DNS rebinding), a custom header on writes (cross-site requests), and the config keys that
grant code execution (`plugin_dirs`, `security`, `editor`) cannot be changed through the API at all.
Editor access is still equivalent to control over what this daemon connects to: keep it on localhost.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import math
import os
import pathlib
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import yaml
from PIL import Image

from . import widgets
from .integrations import REGISTRY as INTEGRATIONS, Registry, load_builtin, normalize
from .integrations.schemas import fields_for, inline_secret_keys
from .device import H, W
from .engine import Dashboard, Page, load_config, save_config
from .render import THEME, parse_color, theme_override
from .settings import PRESETS

log = logging.getLogger("wigix.editor")
UI = pathlib.Path(__file__).with_name("editor_ui")

# The theme keys and their built-in values (mirrors render._BASE_THEME, which set_theme() mutates at runtime).
DEFAULT_THEME = {"bg": "#0a0c10", "card": "#1c2028", "text": "#e8ecf2", "dim": "#808896",
                 "ok": "#4ad682", "warn": "#f5be3c", "crit": "#f05454", "accent": "#569cff"}


def _path_like(v: str) -> bool:
    return v[0] in "~.$/" or "/" in v or "\\" in v


def build_registry(cfg, reuse=None) -> Registry:
    """Registry(cfg), with a missing required setting reported by integration name instead of a bare KeyError."""
    names = list(normalize(cfg))
    try:
        return Registry(cfg, reuse=reuse)
    except KeyError as e:
        raise ValueError(f"an integration is missing the setting {e.args[0]!r} (integrations: {', '.join(names)}); "
                         "fill it in on the Integrations panel") from None


PROTECTED_KEYS = ("plugin_dirs", "security", "editor")   # code-execution settings: hand-edit the file only
MAX_BODY = 1_000_000


def token_path(config_path) -> pathlib.Path:
    return pathlib.Path(os.path.expanduser(str(config_path))).with_name("editor_token")


def load_token(config_path, create=True) -> str | None:
    """The editor's secret token, from <config dir>/editor_token (created 0600 on first use)."""
    p = token_path(config_path)
    try:
        t = p.read_text().strip()
        if t:
            return t
    except FileNotFoundError:
        pass
    if not create:
        return None
    t = secrets.token_urlsafe(24)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t + "\n")
    return t


class PreviewCache:
    """Keeps widget instances (and their data) alive across edits so moving a widget is instant."""

    TTL = 300
    MAX_ITEMS = 64          # each entry runs a worker thread

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.lock = threading.Lock()

    def widget(self, cls, wcfg, ctx):
        base = {k: v for k, v in wcfg.items() if k not in ("at", "on_tap")}
        key = hashlib.sha1(json.dumps([cls.type_name, base, id(ctx.get("integrations"))], sort_keys=True,
                                      default=str).encode()).hexdigest()
        with self.lock:
            e = self.items.get(key)
            if e is None:
                w = cls(base, ctx)
                e = self.items[key] = {"w": w, "t": time.time(), "fresh": True}
                threading.Thread(target=self._run, args=(w,), daemon=True).start()
            e["t"] = time.time()
            for k, v in list(self.items.items()):
                if time.time() - v["t"] > self.TTL:
                    v["w"]._halt = True
                    del self.items[k]
            while len(self.items) > self.MAX_ITEMS:                   # evict the least recently used
                oldest = min(self.items, key=lambda k: self.items[k]["t"])
                self.items[oldest]["w"]._halt = True
                del self.items[oldest]
            return e["w"]

    @staticmethod
    def _run(w):
        while not getattr(w, "_halt", False):
            try:
                w.update()
                w.error = None
            except Exception as ex:
                w.error = str(ex)
            time.sleep(min(w.interval, 5.0))


class Editor:
    def __init__(self, path, dashboard: Dashboard | None = None, token: str | None = None):
        self.path = path
        self.token = token if token is not None else load_token(path)
        self.dash = dashboard
        self.cache = PreviewCache()
        self._reg: Registry | None = None
        self._reg_key = None
        widgets.load_plugins(load_config(path).get("plugin_dirs", []))

    def registry(self, cfg) -> Registry:
        """Integrations for previews/pickers, rebuilt only when the integrations section changes."""
        from .integrations import normalize
        key = json.dumps(normalize(cfg), sort_keys=True, default=str)
        if key != self._reg_key:
            old = self._reg
            self._reg = build_registry(cfg, old)
            self._reg.start()
            if old:
                old.stop(keep=self._reg)
            self._reg_key = key
        return self._reg

    def entities(self, cfg, integration=None):
        ha = self.registry(cfg).get(integration or None, "home_assistant")
        return ha.entities()

    # --- API ------------------------------------------------------------------------------
    def widget_types(self):
        return [{"type": k, "doc": (v.__doc__ or "").strip(), "fields": v.fields}
                for k, v in sorted(widgets.REGISTRY.items())]

    def integration_types(self):
        load_builtin()
        def doc(cls):   # most integrations document themselves in the module docstring
            return (cls.__doc__ or sys.modules[cls.__module__].__doc__ or "").strip()
        return [{"type": k, "doc": doc(v), "fields": fields_for(k)} for k, v in sorted(INTEGRATIONS.items())]

    def meta(self, cfg):
        """Static facts the UI needs: theme defaults and the read-only editor/host settings."""
        return {"theme": DEFAULT_THEME, "editor": cfg.get("editor") or {}, "path": str(self.path), "presets": PRESETS}

    def status(self):
        """Live integration state when the editor runs inside the daemon; otherwise ``running: false``."""
        d = self.dash
        if d is None:
            return {"running": False, "message": "not running (standalone editor)", "integrations": {}}
        out = {}
        for name, i in dict(d.integrations.items).items():
            try:
                st = i.status() or {}
            except Exception as e:
                st = {"connected": False, "error": str(e)}
            st = {k: v for k, v in st.items() if isinstance(v, (bool, int, float, str, type(None)))}
            if isinstance(st.get("error"), str):
                st["error"] = st["error"][:200]
            out[name] = {"type": getattr(i, "type_name", ""), **st}
        return {**d.control_state(), "integrations": out}

    def check(self, cfg):
        """Raise ValueError when the config cannot be used; return a list of non-fatal warnings."""
        if not isinstance(cfg, dict):
            raise ValueError("the config must be a mapping")
        load_builtin()
        warnings = []
        integrations = cfg.get("integrations") or {}
        if not isinstance(integrations, dict):
            raise ValueError("`integrations:` must be a mapping of name -> settings")
        for name, ic in integrations.items():
            if not isinstance(ic, dict):
                raise ValueError(f"integration {name!r} must be a mapping with a `type:`")
            t = ic.get("type")
            if t not in INTEGRATIONS:
                raise ValueError(f"integration {name!r}: unknown type {t!r}; known: {sorted(INTEGRATIONS)}")
            for f in fields_for(t):
                if f.get("required") and ic.get(f["name"]) in (None, "") and not (
                        f.get("secret") and ic.get(f["name"][:-5])):
                    warnings.append(f"integration {name!r}: {f['name']} is empty")
            for f in fields_for(t):
                v = ic.get(f["name"])
                if f.get("secret") and isinstance(v, str) and v and not _path_like(v):
                    raise ValueError(f"integration {name!r}: {f['name']} must be the path of a file that holds the secret "
                                     f"(for example ~/.config/wigix/{name}_secret), not the secret itself")
            for inline, file_key in inline_secret_keys(t).items():
                if ic.get(inline):
                    warnings.append(f"integration {name!r}: inline `{inline}` stores a secret in the config file; "
                                    f"use `{file_key}` (a path to a 0600 file) instead")
        names = sorted(normalize(cfg))
        try:
            build_registry(cfg)          # nothing is started: this only catches incomplete settings
        except ValueError:
            raise
        except Exception:
            pass                         # e.g. a secret file that only exists on the panel's machine
        theme = cfg.get("theme") or {}
        if isinstance(theme, str):
            theme = {"preset": theme}
        if not isinstance(theme, dict):
            raise ValueError("`theme:` must be a preset name or a mapping of colors")
        if "preset" in theme and str(theme["preset"]).lower() not in PRESETS:
            raise ValueError(f"theme.preset: unknown preset {theme['preset']!r}; known: {sorted(PRESETS)}")
        for k, v in theme.items():
            if k == "preset":
                continue
            if k not in DEFAULT_THEME:
                raise ValueError(f"theme: unknown color {k!r}; known: {sorted(DEFAULT_THEME)}")
            if parse_color(v) is None:
                raise ValueError(f"theme.{k}: {v!r} is not a color (use #rrggbb)")
        if "fps" in cfg and not (isinstance(cfg["fps"], (int, float)) and not isinstance(cfg["fps"], bool)
                                 and 0 < cfg["fps"] <= Dashboard.MAX_FPS):
            raise ValueError(f"fps must be a number between 0 and {Dashboard.MAX_FPS:g}")
        if "brightness" in cfg and not (isinstance(cfg["brightness"], (int, float)) and 0 <= cfg["brightness"] <= 100):
            raise ValueError("brightness must be a number from 0 to 100")
        self._check_display_settings(cfg)
        if not isinstance(cfg.get("security") or {}, dict):
            raise ValueError("`security:` must be a mapping")
        Dashboard._build_pages(cfg, dict(cfg))   # raises on unknown widget types / bad config
        for p in cfg["pages"]:
            g = p.get("grid", {})
            for w in p.get("widgets", []):
                c, r, cs, rs = w.get("at", [0, 0, 1, 1])
                if c < 0 or r < 0 or c + cs > g.get("cols", 4) or r + rs > g.get("rows", 3):
                    raise ValueError(f"widget {w['type']} on page {p.get('name')} is outside the grid")
                ref = w.get("integration")
                if ref and ref not in names:
                    raise ValueError(
                        f"widget {w['type']} on page {p.get('name')} uses integration {ref!r}, which is not defined; "
                        f"{'defined: ' + ', '.join(names) if names else 'there are none yet'} (add it in the Integrations panel)")
        return warnings

    @staticmethod
    def _check_display_settings(cfg):
        def num(where, v, lo, hi):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                raise ValueError(f"{where} must be a number from {lo:g} to {hi:g}")
        for key in ("rotate", "idle", "splash"):
            v = cfg.get(key)
            if v is not None and not isinstance(v, (bool, dict)):
                raise ValueError(f"`{key}:` must be true, false or a mapping")
        if cfg.get("page_seconds") is not None:
            num("page_seconds", cfg["page_seconds"], 0, 86400)
        rot = cfg.get("rotate") if isinstance(cfg.get("rotate"), dict) else {}
        for k, hi in (("seconds", 86400), ("pause_after_touch", 3600)):
            if rot.get(k) is not None:
                num(f"rotate.{k}", rot[k], 0, hi)
        idle = cfg.get("idle") if isinstance(cfg.get("idle"), dict) else {}
        for k, lo, hi in (("after", 1, 86400), ("dim_to", 0, 100), ("off_after", 1, 604800)):
            if idle.get(k) is not None:
                num(f"idle.{k}", idle[k], lo, hi)
        if isinstance(cfg.get("splash"), dict) and cfg["splash"].get("seconds") is not None:
            num("splash.seconds", cfg["splash"]["seconds"], 0, 60)
        for i, p in enumerate(cfg.get("pages") or []):
            if isinstance(p, dict) and p.get("seconds") is not None:
                num(f"pages[{i}].seconds", p["seconds"], 1, 86400)

    def validate(self, cfg):
        self.check(cfg)

    def check_protected(self, cfg):
        """The keys that grant code execution may not be changed through the editor."""
        try:
            on_disk = load_config(self.path)
        except FileNotFoundError:
            on_disk = {}
        for k in PROTECTED_KEYS:
            if cfg.get(k) != on_disk.get(k):
                raise ValueError(f"`{k}` can only be changed by editing the config file by hand "
                                 "(it can make the daemon run code)")

    def validate_yaml(self, text):
        """Parse raw YAML and check it. Returns {ok, error, line, warnings, config}; never echoes file content."""
        try:
            cfg = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            msg = " ".join(x for x in (getattr(e, "context", None), getattr(e, "problem", None)) if x) or "invalid YAML"
            return {"ok": False, "error": msg, "line": mark.line + 1 if mark else None, "column": mark.column + 1 if mark else None}
        try:
            return {"ok": True, "warnings": self.check(cfg), "config": cfg}
        except Exception as e:
            return {"ok": False, "error": str(e), "line": None, "config": cfg if isinstance(cfg, dict) else None}

    def to_yaml(self, cfg):
        buf = io.StringIO()
        yaml.safe_dump(cfg, buf, sort_keys=False, default_flow_style=None, width=100)
        return buf.getvalue()

    def save(self, cfg):
        warnings = self.check(cfg)
        self.check_protected(cfg)
        save_config(self.path, cfg)
        return warnings

    def render(self, cfg, page_idx, wait=0.0):
        pages = cfg.get("pages", [])
        if not pages:
            return Image.new("RGB", (W, H), THEME["bg"])
        ctx = dict(cfg, integrations=self.registry(cfg))
        page = Page(pages[min(page_idx, len(pages) - 1)], ctx,
                    factory=lambda cls, wc, x: self.cache.widget(cls, wc, x))
        if wait:
            time.sleep(wait)
        with theme_override(cfg.get("theme")):     # the preview shows the edited theme, not the daemon's
            return page.render()


ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
HOST_RE = re.compile(r"^(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+)(:\d{1,5})?$")
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
NEEDS_TOKEN = """<!doctype html><meta charset=utf-8><title>wigix editor</title>
<body style="font:16px system-ui;max-width:34em;margin:4em auto;padding:0 1em">
<h1>wigix editor</h1><p>This editor needs its access link. Run <code>wigix url</code> on the machine running wigix and open
the link it prints (it contains a secret token, so treat it like a password).</p></body>"""


def make_handler(ed: Editor, extra_hosts=()):
    allowed = {h.lower() for h in ALLOWED_HOSTS | set(extra_hosts)}

    class H_(BaseHTTPRequestHandler):
        timeout = 10            # drop slow or stalled clients

        def log_message(self, *a):
            pass

        def _host_ok(self) -> bool:
            m = HOST_RE.match(self.headers.get("Host") or "")
            return bool(m) and m.group(1).lower() in allowed

        def _cookie_token(self):
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "wigix_token":
                    return v
            return None

        def _authed(self) -> bool:
            sent = self._cookie_token() or self.headers.get("X-Wigix-Token") or ""
            return bool(ed.token) and hmac.compare_digest(sent.encode(), ed.token.encode())

        def _guard(self, write: bool) -> bool:
            """Host allow-list (DNS rebinding), secret token (anything else on this machine/LAN) and, for writes,
            a custom header (cross-site requests)."""
            if not self._host_ok():
                self._send(403, {"error": "forbidden host"})
                return False
            if not self._authed():
                self._send(401, {"error": "missing or wrong editor token: open the link printed by `wigix url`"})
                return False
            if write and self.headers.get("X-Requested-With") != "wigix":
                self._send(403, {"error": "missing X-Requested-With header"})
                return False
            return True

        def _send(self, code, body, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("bad Content-Length")
            if n < 0 or n > MAX_BODY:
                raise ValueError(f"request body must be 0..{MAX_BODY} bytes")
            return json.loads(self.rfile.read(n) or b"{}")

        def _login(self, u) -> bool:
            """GET /?token=...: set the cookie and redirect to a clean URL. True if the request was handled."""
            sent = (parse_qs(u.query).get("token") or [""])[0]
            if not (ed.token and sent and hmac.compare_digest(sent.encode(), ed.token.encode())):
                return False
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"wigix_token={ed.token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/" and self._host_ok() and self._login(u):
                return
            if u.path == "/" and self._host_ok() and not self._authed():
                return self._send(401, NEEDS_TOKEN, "text/html; charset=utf-8")
            if u.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if not self._guard(False):
                return
            if u.path == "/":
                self._send(200, (UI / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/api/config":
                self._send(200, load_config(ed.path))
            elif u.path == "/api/widgets":
                self._send(200, ed.widget_types())
            elif u.path == "/api/integration-types":
                self._send(200, ed.integration_types())
            elif u.path == "/api/meta":
                self._send(200, ed.meta(load_config(ed.path)))
            elif u.path == "/api/status":
                self._send(200, ed.status())
            elif u.path == "/api/entities":
                try:
                    q = parse_qs(u.query)
                    self._send(200, ed.entities(load_config(ed.path), q.get("integration", [None])[0]))
                except Exception as e:
                    self._send(200, {"error": str(e), "entities": []})
            else:
                self._send(404, {"error": "not found"})

        def _validate(self):
            """{"yaml": text} -> parsed+checked config; {"config": {...}} -> checked config plus its YAML text."""
            try:
                body = self._json()
                if "yaml" in body:
                    return self._send(200, ed.validate_yaml(str(body["yaml"])))
                cfg = body.get("config")
                try:
                    self._send(200, {"ok": True, "warnings": ed.check(cfg), "yaml": ed.to_yaml(cfg)})
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e), "line": None,
                                     "yaml": ed.to_yaml(cfg) if isinstance(cfg, dict) else ""})
            except Exception as e:
                self._send(400, {"ok": False, "error": str(e)})

        def do_PUT(self):
            if not self._guard(True):
                return
            if urlparse(self.path).path != "/api/config":
                return self._send(404, {"error": "not found"})
            try:
                self._send(200, {"ok": True, "warnings": ed.save(self._json())})
            except Exception as e:
                self._send(400, {"ok": False, "error": str(e)})

        def do_POST(self):
            if not self._guard(True):
                return
            u = urlparse(self.path)
            if u.path == "/api/control":
                if ed.dash is None:
                    return self._send(409, {"error": "the daemon is not running in this process"})
                try:
                    return self._send(200, ed.dash.control(self._json()))
                except Exception as e:
                    return self._send(400, {"ok": False, "error": str(e)})
            if u.path == "/api/validate":
                return self._validate()
            if u.path != "/api/preview":
                return self._send(404, {"error": "not found"})
            try:
                q = parse_qs(u.query)
                cfg = self._json()
                buf = io.BytesIO()
                wait = float(q.get("wait", ["0"])[0])
                wait = min(max(wait, 0.0), 5.0) if math.isfinite(wait) else 0.0
                img = ed.render(cfg, int(q.get("page", ["0"])[0]), wait=wait)
                img.save(buf, "PNG")
                self._send(200, buf.getvalue(), "image/png")
            except Exception as e:
                log.exception("preview failed")
                self._send(400, {"error": str(e)})
    return H_


def editor_url(path, host="127.0.0.1", port=8765) -> str:
    """The link that opens the editor (contains the secret token: do not share or log it)."""
    host = "localhost" if host in ("0.0.0.0", "::") else host
    return f"http://{host}:{port}/?token={load_token(path)}"


def serve(path, host="127.0.0.1", port=8765, dashboard=None, block=True, allowed_hosts=(), announce=False):
    ed = Editor(path, dashboard)
    srv = ThreadingHTTPServer((host, port), make_handler(ed, allowed_hosts))
    log.info("editor listening on %s:%d (run `wigix url` for the access link)", host, port)
    if announce:                                 # interactive `wigix editor`: print the link on the terminal
        print("Open:", editor_url(path, host, port), flush=True)
    if block:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    else:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
