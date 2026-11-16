"""Config loading, layout, widget scheduling and the render loop."""
from __future__ import annotations

import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import threading
import time

import yaml
from PIL import Image, ImageDraw

from . import widgets
from .integrations import Registry
from .device import H, W, WigiDash, rgb565
from .render import THEME, Canvas, set_theme, theme_override

log = logging.getLogger("wigix")


def load_config(path):
    with open(os.path.expanduser(str(path))) as f:
        return yaml.safe_load(f) or {}


def save_config(path, cfg):
    """Write the config as YAML, keeping the previous version as <path>.bak."""
    path = os.path.expanduser(str(path))
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=None, width=100)
    os.replace(tmp, path)


class Page:
    def __init__(self, cfg, ctx, factory=None):
        self.name = cfg.get("name", "page")
        g = cfg.get("grid", {})
        self.cols, self.rows, self.gap = g.get("cols", 4), g.get("rows", 3), g.get("gap", 10)
        self.items = []
        for w in cfg.get("widgets", []):
            cls = widgets.REGISTRY.get(w["type"])
            if cls is None:
                raise ValueError(f"unknown widget type {w['type']!r}; known: {sorted(widgets.REGISTRY)}")
            make = factory or (lambda c, wc, x: c(wc, x))
            self.items.append((make(cls, w, ctx), w.get("at", [0, 0, 1, 1])))

    def box(self, at):
        col, row, cs, rs = at
        gap = self.gap
        cw = (W - gap * (self.cols + 1)) / self.cols
        ch = (H - gap * (self.rows + 1)) / self.rows
        x0 = gap + col * (cw + gap)
        y0 = gap + row * (ch + gap)
        return (int(x0), int(y0), int(x0 + cs * cw + (cs - 1) * gap), int(y0 + rs * ch + (rs - 1) * gap))

    def hit(self, x, y):
        for w, at in self.items:
            x0, y0, x1, y1 = self.box(at)
            if x0 <= x <= x1 and y0 <= y <= y1:
                return w
        return None

    def render(self, only=None) -> Image.Image:
        """Draw the page, or just ``only`` (a widget) filling the screen when it is in fullscreen."""
        img = Image.new("RGB", (W, H), THEME["bg"])
        cv = Canvas(ImageDraw.Draw(img), img)
        for w, at in self.items:
            if only is not None and w is not only:
                continue
            box = (10, 10, W - 10, H - 10) if only is not None else self.box(at)
            w.box = box
            try:
                with theme_override(w.cfg.get("theme")):
                    (w.draw_full if only is not None else w.draw)(cv, box)
            except Exception:
                log.exception("draw failed: %s", w.type_name)
            if only is not None:                      # the close target in the top-right corner
                cx, cy, r = W - 40, 40, 22
                cv.d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(0, 0, 0), outline=THEME["dim"], width=2)
                cv.d.line((cx - 8, cy - 8, cx + 8, cy + 8), fill=THEME["text"], width=3)
                cv.d.line((cx - 8, cy + 8, cx + 8, cy - 8), fill=THEME["text"], width=3)
        return img


class Dashboard:
    def __init__(self, cfg: dict, path=None):
        widgets.load_plugins(cfg.get("plugin_dirs", []))
        self.path = path
        self._mtime = os.path.getmtime(os.path.expanduser(str(path))) if path else 0
        self.cfg = cfg
        set_theme(cfg.get("theme"))
        self.integrations = Registry(cfg)
        self.ctx = dict(cfg, integrations=self.integrations, run_action=self.run_action)
        self.pages = self._build_pages(cfg, self.ctx)
        self.page_idx = 0
        self.dev = None
        self.full = None            # widget shown fullscreen, if any
        self._wake = threading.Event()   # set to redraw immediately
        self._last_action = 0.0
        self._stop = threading.Event()

    @staticmethod
    def _build_pages(cfg, ctx):
        pages = [Page(p, ctx) for p in cfg.get("pages", [])]
        if not pages:
            raise ValueError("config has no pages")
        return pages

    def reload(self, cfg: dict):
        """Swap in a new config without touching the device. Raises (and keeps the old one) on errors."""
        widgets.load_plugins(cfg.get("plugin_dirs", []))
        integrations = Registry(cfg, reuse=self.integrations)
        ctx = dict(cfg, integrations=integrations, run_action=self.run_action)
        pages = self._build_pages(cfg, ctx)
        set_theme(cfg.get("theme"))
        old, old_integrations = self.pages, self.integrations
        self.cfg, self.ctx, self.pages, self.integrations = cfg, ctx, pages, integrations
        integrations.start()
        old_integrations.stop(keep=integrations)
        self.page_idx = min(self.page_idx, len(pages) - 1)
        for page in old:
            for w, _ in page.items:
                w._halt = True
        self.start_workers()
        self._wake.set()

    def _check_reload(self):
        if not self.path:
            return
        try:
            m = os.path.getmtime(os.path.expanduser(str(self.path)))
            if m != self._mtime:
                self._mtime = m
                self.reload(load_config(self.path))
                log.info("config reloaded")
        except Exception:
            log.exception("config reload failed; keeping previous config")

    def _worker(self, w):
        while not self._stop.is_set() and not getattr(w, "_halt", False):
            try:
                w.update()
                w.error = None
            except Exception as e:
                w.error = str(e)
                log.debug("update failed: %s: %s", w.type_name, e)
            self._stop.wait(min(w.interval, 1.0) if getattr(w, "_halt", False) else w.interval)

    def start_workers(self):
        self.integrations.start()
        for page in self.pages:
            for w, _ in page.items:
                threading.Thread(target=self._worker, args=(w,), daemon=True).start()

    # --- touch -------------------------------------------------------------------------------
    SWIPE_PX, TAP_PX, TAP_SECS = 90, 30, 1.0

    def goto(self, target):
        self._wake.set()
        if target == "next_page":
            self.page_idx += 1
        elif target == "prev_page":
            self.page_idx -= 1
        else:
            for i, p in enumerate(self.pages):
                if p.name == target:
                    self.page_idx = i

    def run_action(self, act, source=None):
        """Run a widget action: "next_page" | "prev_page" | "fullscreen" | "close" | {page: name} |
        {command: "..."} | {url: ...} | {ha_service: domain.service, entity: ..., data: {...}} | [list of those]."""
        if act is None:
            return
        if isinstance(act, list):
            for a in act:
                self.run_action(a, source)
            return
        if isinstance(act, str):
            if act == "fullscreen":
                self.full = source
                self._wake.set()
            elif act == "close":
                self.full = None
                self._wake.set()
            else:
                self.goto(act)
        elif "page" in act:
            self.goto(act["page"])
        elif "command" in act:
            if not (self.cfg.get("security") or {}).get("allow_commands"):
                log.warning("command action ignored: set `security: {allow_commands: true}` to enable it")
                return
            cmd = act["command"]
            subprocess.Popen(cmd if isinstance(cmd, list) else shlex.split(cmd), start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif "key" in act or "type" in act:
            if not (self.cfg.get("security") or {}).get("allow_commands"):
                log.warning("key/type action ignored: set `security: {allow_commands: true}` to enable it")
                return
            self._send_keys(act)
        elif "url" in act:
            subprocess.Popen(["xdg-open", act["url"]], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif "ha_service" in act:
            domain, service = act["ha_service"].split(".", 1)
            data = dict(act.get("data") or {})
            if act.get("entity"):
                data["entity_id"] = act["entity"]
            self.integrations.get(act.get("integration"), "home_assistant").call_service(domain, service, data)
        else:
            log.warning("unknown action: %r", act)

    @staticmethod
    def _send_keys(act):
        """Press a key combo ({key: "ctrl+shift+m"}) or type text ({type: "hello"}) with whichever tool exists:
        wtype/ydotool (Wayland) or xdotool (X11 / XWayland windows only)."""
        key, text = act.get("key"), act.get("type")
        if shutil.which("wtype"):
            if key:
                parts = key.lower().split("+")
                mods = [m for m in parts[:-1]]
                cmd = ["wtype"] + [a for m in mods for a in ("-M", m)] + ["-k", parts[-1]] + [a for m in reversed(mods) for a in ("-m", m)]
            else:
                cmd = ["wtype", text]
        elif shutil.which("ydotool"):
            cmd = ["ydotool", "type", text] if text else ["ydotool", "key", key]   # ydotool wants key codes: see its docs
        elif shutil.which("xdotool"):
            cmd = ["xdotool", "key", key] if key else ["xdotool", "type", text]
        else:
            log.warning("no key-sending tool found (install wtype, ydotool or xdotool)")
            return
        subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def handle_tap(self, x, y):
        now = time.time()
        if now - self._last_action < 0.35:   # debounce contact bounce
            return
        self._last_action = now
        if self.full is not None:            # any tap leaves fullscreen (raw-touch widgets: only the corner)
            if not self.full.wants_touch or self.in_close_corner(x, y):
                self.run_action("close")
            return
        w = self.current().hit(x, y)
        if w is None:
            return
        log.debug("tap on %s at %d,%d", w.type_name, x, y)
        try:
            if not w.on_tap(x, y):
                self.run_action(w.cfg.get("on_tap"), w)
        except Exception:
            log.exception("tap action failed")

    def touch_target(self, x, y):
        """The widget under a screen point: the fullscreen one, else the one on the current page."""
        if self.full is not None:
            return self.full
        return self.current().hit(x, y)

    @staticmethod
    def in_close_corner(x, y):
        return x > W - 80 and y < 80

    def _touch_loop(self):
        start, swiped, raw = None, False, None
        while not self._stop.is_set():
            dev = self.dev
            if dev is None:
                time.sleep(0.2)
                continue
            try:
                t = dev.touch()
            except Exception:
                time.sleep(0.2)
                continue
            if t is None:
                time.sleep(0.016)
                continue
            if t.down and start is None:              # a new touch begins: raw-touch widgets take it over
                w = self.touch_target(t.x, t.y)
                raw = w if (w is not None and w.wants_touch and not self.in_close_corner(t.x, t.y)) else None
            if raw is not None:
                try:
                    raw.on_touch(t.x - raw.box[0], t.y - raw.box[1], t.down)
                except Exception:
                    log.exception("on_touch failed")
                self._wake.set()
                if not t.down:
                    raw, start = None, None
                else:
                    start = start or (t.x, t.y, time.time())
                time.sleep(0.008)
                continue
            if t.down:
                if start is None:
                    start, swiped = (t.x, t.y, time.time()), False
                last = (t.x, t.y)
                dx, dy = t.x - start[0], t.y - start[1]
                # fire as soon as the drag is clearly horizontal and long enough
                if (not swiped and self.cfg.get("swipe", True) and abs(dx) > self.SWIPE_PX
                        and abs(dx) > 1.5 * abs(dy)):
                    swiped = True
                    self._last_action = time.time()
                    if self.full is not None:
                        self.run_action("close")
                    else:
                        self.goto("next_page" if dx < 0 else "prev_page")
            elif start is not None:
                dx, dy, dt = last[0] - start[0], last[1] - start[1], time.time() - start[2]
                if (not swiped and abs(dx) < self.TAP_PX and abs(dy) < self.TAP_PX
                        and dt < self.TAP_SECS):
                    self.handle_tap(*start[:2])
                start = None
            time.sleep(0.008)

    def _apply_brightness(self, dev):
        """Push the configured ``brightness:`` (0-100) when the device (re)connects or the value changes."""
        want = self.cfg.get("brightness")
        if dev is None or want is None:
            return
        if getattr(dev, "_wanted_brightness", None) != want:
            try:
                dev.brightness = want
                dev._wanted_brightness = want
            except Exception as e:
                log.debug("brightness failed: %s", e)

    MAX_FPS = 17.0        # the USB link tops out around here (see docs/protocol.md)

    def target_fps(self):
        """Config ``fps`` (default 2), raised to what visible animated/video widgets ask for."""
        want = float(self.cfg.get("fps", 2))
        page = self.current()
        for w, _ in page.items:
            if (self.full is None or w is self.full) and w.fps:
                want = max(want, float(w.fps))
        return min(want, self.MAX_FPS)

    def render_current(self):
        page = self.current()
        full = self.full if self.full is not None and any(w is self.full for w, _ in page.items) else None
        if full is None:
            self.full = None
        return page.render(only=full)

    def current(self) -> Page:
        return self.pages[self.page_idx % len(self.pages)]

    def preview(self, out, settle=2.5):
        """Render the first page to a PNG without hardware."""
        self.start_workers()
        time.sleep(settle)
        self.current().render().save(out)
        self._stop.set()

    def run(self):
        self.start_workers()
        threading.Thread(target=self._touch_loop, daemon=True).start()
        ecfg = self.cfg.get("editor")
        if ecfg and self.path:
            from .editor import serve
            serve(self.path, ecfg.get("host", "127.0.0.1"), int(ecfg.get("port", 8765)), self, block=False,
                  allowed_hosts=ecfg.get("allowed_hosts", ()))
        rotate = float(self.cfg.get("page_seconds", 0))
        last_flip = time.time()
        dev, last_hash, last_sent = None, None, 0.0
        while True:
            t0 = time.time()
            fps = self.target_fps()
            self._check_reload()
            if rotate and len(self.pages) > 1 and t0 - last_flip > rotate:
                self.page_idx += 1
                last_flip = t0
            self._apply_brightness(dev)
            frame = rgb565(self.render_current())
            h = hashlib.blake2b(frame, digest_size=8).digest()
            if h != last_hash or t0 - last_sent > 5:
                try:
                    if dev is None:
                        dev = self.dev = WigiDash()
                        log.info("WigiDash connected: %s", dev.info())
                    dev.show(frame)
                    last_hash, last_sent = h, t0
                except Exception as e:
                    if dev is not None:
                        log.warning("device error (%s); will reconnect", e)
                    dev = self.dev = None
                    time.sleep(2)
            self._wake.wait(max(0.0, 1.0 / fps - (time.time() - t0)))
            self._wake.clear()
