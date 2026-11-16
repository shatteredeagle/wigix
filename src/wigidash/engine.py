"""Config loading, layout, widget scheduling and the render loop."""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time

import yaml
from PIL import Image, ImageDraw

from . import widgets
from .integrations import Registry
from .device import H, W, WigiDash, rgb565
from .render import THEME, Canvas

log = logging.getLogger("wigidash")


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
                raise SystemExit(f"unknown widget type {w['type']!r}; known: {sorted(widgets.REGISTRY)}")
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

    def render(self) -> Image.Image:
        img = Image.new("RGB", (W, H), THEME["bg"])
        cv = Canvas(ImageDraw.Draw(img))
        for w, at in self.items:
            try:
                w.draw(cv, self.box(at))
            except Exception:
                log.exception("draw failed: %s", w.type_name)
        return img


class Dashboard:
    def __init__(self, cfg: dict, path=None):
        widgets.load_plugins(cfg.get("plugin_dirs", []))
        self.path = path
        self._mtime = os.path.getmtime(os.path.expanduser(str(path))) if path else 0
        self.cfg = cfg
        self.integrations = Registry(cfg)
        self.ctx = dict(cfg, integrations=self.integrations)
        self.pages = self._build_pages(cfg, self.ctx)
        self.page_idx = 0
        self.dev = None
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
        ctx = dict(cfg, integrations=integrations)
        pages = self._build_pages(cfg, ctx)
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

    def handle_tap(self, x, y):
        now = time.time()
        if now - self._last_action < 0.35:   # debounce contact bounce
            return
        self._last_action = now
        w = self.current().hit(x, y)
        if w is None:
            return
        log.debug("tap on %s at %d,%d", w.type_name, x, y)
        try:
            if w.on_tap(x, y):
                return
            act = w.cfg.get("on_tap")
            if isinstance(act, str):
                self.goto(act)
            elif isinstance(act, dict) and "page" in act:
                self.goto(act["page"])
            elif isinstance(act, dict) and "ha_service" in act:
                domain, service = act["ha_service"].split(".", 1)
                self.integrations.get(act.get("integration"), "home_assistant").call_service(
                    domain, service, {"entity_id": act["entity"]})
        except Exception:
            log.exception("tap action failed")

    def _touch_loop(self):
        start, swiped = None, False
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
                    self.goto("next_page" if dx < 0 else "prev_page")
            elif start is not None:
                dx, dy, dt = last[0] - start[0], last[1] - start[1], time.time() - start[2]
                if (not swiped and abs(dx) < self.TAP_PX and abs(dy) < self.TAP_PX
                        and dt < self.TAP_SECS):
                    self.handle_tap(*start[:2])
                start = None
            time.sleep(0.008)

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
            serve(self.path, ecfg.get("host", "127.0.0.1"), int(ecfg.get("port", 8765)), self, block=False)
        fps = float(self.cfg.get("fps", 2))
        rotate = float(self.cfg.get("page_seconds", 0))
        last_flip = time.time()
        dev, last_hash, last_sent = None, None, 0.0
        while True:
            t0 = time.time()
            self._check_reload()
            if rotate and len(self.pages) > 1 and t0 - last_flip > rotate:
                self.page_idx += 1
                last_flip = t0
            frame = rgb565(self.current().render())
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
