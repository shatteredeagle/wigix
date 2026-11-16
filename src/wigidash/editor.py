"""Local web editor: drag widgets around a live preview, edit their settings, save to the YAML.

Serves a small single-page app plus a JSON API on 127.0.0.1. The running daemon hot-reloads the
config file, so Save updates the panel. No auth: keep it bound to localhost unless you know why not.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw

from . import widgets
from .device import H, W
from .engine import Dashboard, Page, load_config, save_config
from .render import THEME, Canvas

log = logging.getLogger("wigidash.editor")
UI = pathlib.Path(__file__).with_name("editor_ui")


class PreviewCache:
    """Keeps widget instances (and their data) alive across edits so moving a widget is instant."""

    TTL = 300

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.lock = threading.Lock()

    def widget(self, cls, wcfg, ctx):
        base = {k: v for k, v in wcfg.items() if k not in ("at", "on_tap")}
        key = hashlib.sha1(json.dumps([cls.type_name, base], sort_keys=True, default=str).encode()).hexdigest()
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
    def __init__(self, path, dashboard: Dashboard | None = None):
        self.path = path
        self.dash = dashboard
        self.cache = PreviewCache()
        widgets.load_plugins(load_config(path).get("plugin_dirs", []))

    # --- API ------------------------------------------------------------------------------
    def widget_types(self):
        return [{"type": k, "doc": (v.__doc__ or "").strip(), "fields": v.fields}
                for k, v in sorted(widgets.REGISTRY.items())]

    def validate(self, cfg):
        Dashboard._build_pages(cfg, dict(cfg))   # raises on unknown widget types / bad config
        for p in cfg["pages"]:
            g = p.get("grid", {})
            for w in p.get("widgets", []):
                c, r, cs, rs = w.get("at", [0, 0, 1, 1])
                if c < 0 or r < 0 or c + cs > g.get("cols", 4) or r + rs > g.get("rows", 3):
                    raise ValueError(f"widget {w['type']} on page {p.get('name')} is outside the grid")

    def save(self, cfg):
        self.validate(cfg)
        save_config(self.path, cfg)

    def render(self, cfg, page_idx, wait=0.0):
        pages = cfg.get("pages", [])
        if not pages:
            return Image.new("RGB", (W, H), THEME["bg"])
        ctx = dict(cfg)
        page = Page(pages[min(page_idx, len(pages) - 1)], ctx,
                    factory=lambda cls, wc, x: self.cache.widget(cls, wc, x))
        if wait:
            time.sleep(wait)
        return page.render()


def make_handler(ed: Editor):
    class H_(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                self._send(200, (UI / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            elif u.path == "/api/config":
                self._send(200, load_config(ed.path))
            elif u.path == "/api/widgets":
                self._send(200, ed.widget_types())
            else:
                self._send(404, {"error": "not found"})

        def do_PUT(self):
            if urlparse(self.path).path != "/api/config":
                return self._send(404, {"error": "not found"})
            try:
                ed.save(self._json())
                self._send(200, {"ok": True})
            except Exception as e:
                self._send(400, {"ok": False, "error": str(e)})

        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/api/preview":
                return self._send(404, {"error": "not found"})
            try:
                q = parse_qs(u.query)
                cfg = self._json()
                buf = io.BytesIO()
                img = ed.render(cfg, int(q.get("page", ["0"])[0]), wait=float(q.get("wait", ["0"])[0]))
                img.save(buf, "PNG")
                self._send(200, buf.getvalue(), "image/png")
            except Exception as e:
                log.exception("preview failed")
                self._send(400, {"error": str(e)})
    return H_


def serve(path, host="127.0.0.1", port=8765, dashboard=None, block=True):
    ed = Editor(path, dashboard)
    srv = ThreadingHTTPServer((host, port), make_handler(ed))
    log.info("editor at http://%s:%d", host, port)
    if block:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    else:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
