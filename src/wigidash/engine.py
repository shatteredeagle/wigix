"""Config loading, layout, widget scheduling and the render loop."""
from __future__ import annotations

import hashlib
import logging
import threading
import time

import yaml
from PIL import Image, ImageDraw

from . import widgets
from .device import H, W, WigiDash, rgb565
from .render import THEME, Canvas

log = logging.getLogger("wigidash")


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


class Page:
    def __init__(self, cfg, ctx):
        self.name = cfg.get("name", "page")
        g = cfg.get("grid", {})
        self.cols, self.rows, self.gap = g.get("cols", 4), g.get("rows", 3), g.get("gap", 10)
        self.items = []
        for w in cfg.get("widgets", []):
            cls = widgets.REGISTRY.get(w["type"])
            if cls is None:
                raise SystemExit(f"unknown widget type {w['type']!r}; known: {sorted(widgets.REGISTRY)}")
            self.items.append((cls(w, ctx), w.get("at", [0, 0, 1, 1])))

    def box(self, at):
        col, row, cs, rs = at
        gap = self.gap
        cw = (W - gap * (self.cols + 1)) / self.cols
        ch = (H - gap * (self.rows + 1)) / self.rows
        x0 = gap + col * (cw + gap)
        y0 = gap + row * (ch + gap)
        return (int(x0), int(y0), int(x0 + cs * cw + (cs - 1) * gap), int(y0 + rs * ch + (rs - 1) * gap))

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
    def __init__(self, cfg: dict):
        widgets.load_plugins(cfg.get("plugin_dirs", []))
        self.cfg = cfg
        self.ctx = dict(cfg)
        self.pages = [Page(p, self.ctx) for p in cfg.get("pages", [])]
        if not self.pages:
            raise SystemExit("config has no pages")
        self.page_idx = 0
        self._stop = threading.Event()

    def _worker(self, w):
        while not self._stop.is_set():
            try:
                w.update()
                w.error = None
            except Exception as e:
                w.error = str(e)
                log.debug("update failed: %s: %s", w.type_name, e)
            self._stop.wait(w.interval)

    def start_workers(self):
        for page in self.pages:
            for w, _ in page.items:
                threading.Thread(target=self._worker, args=(w,), daemon=True).start()

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
        fps = float(self.cfg.get("fps", 2))
        rotate = float(self.cfg.get("page_seconds", 0))
        last_flip = time.time()
        dev, last_hash, last_sent = None, None, 0.0
        while True:
            t0 = time.time()
            if rotate and len(self.pages) > 1 and t0 - last_flip > rotate:
                self.page_idx += 1
                last_flip = t0
            frame = rgb565(self.current().render())
            h = hashlib.blake2b(frame, digest_size=8).digest()
            if h != last_hash or t0 - last_sent > 5:
                try:
                    if dev is None:
                        dev = WigiDash()
                        log.info("WigiDash connected")
                    dev.show(frame)
                    last_hash, last_sent = h, t0
                except Exception as e:
                    if dev is not None:
                        log.warning("device error (%s); will reconnect", e)
                    dev = None
                    time.sleep(2)
            time.sleep(max(0.0, 1.0 / fps - (time.time() - t0)))
