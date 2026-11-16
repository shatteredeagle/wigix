# SPDX-License-Identifier: GPL-3.0-only
"""A touch sketch pad: draw with a finger, pick colors and brush sizes, save the result as a PNG."""
from __future__ import annotations

import datetime as dt
import logging
import pathlib
import time

from PIL import Image, ImageDraw

from . import Widget, widget
from ..render import THEME, parse_color

log = logging.getLogger("wigix")

PALETTE = ["#f5f5f5", "#f05454", "#f5be3c", "#4ad682", "#569cff", "#b07cff", "#ff8fc8", "#8a6a4a"]
BRUSHES = (3, 8, 16)              # pixel diameters of the three brush sizes
SAVED_SECS = 1.5                  # how long the "saved" notice stays up
TAP_PX = 12                       # a touch that moves less than this counts as a tap (to open fullscreen)
STROKE_FPS = 15


@widget("draw_pad")
class DrawPad(Widget):
    """Sketch pad with a toolbar (colors, three brush sizes, eraser, clear, save).

    Strokes go into an off-screen image the size of the widget, so they survive redraws and are rescaled
    when the widget changes size (e.g. in fullscreen). Config: ``save_dir`` (default ~/Pictures/wigix).
    With ``on_tap: fullscreen`` a short tap on the small widget opens it fullscreen; drag to draw.
    """
    interval = 3600.0
    wants_touch = True
    fields = [{"name": "save_dir", "type": "string", "default": "~/Pictures/wigix",
               "help": "folder for saved PNGs"}]

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.image: Image.Image | None = None   # the drawing; same size as the widget box
        self.bg = THEME["card"]
        self.color = parse_color(PALETTE[0])
        self.brush = 1                          # index into BRUSHES
        self.eraser = False
        self._prev = None                       # last stroke point, None between strokes
        self._mode = None                       # "draw" | "toolbar" | None for the touch in progress
        self._start = (0, 0)
        self._moved = False
        self._notice = ("", 0.0)

    @property
    def fps(self):                              # animate only while a finger is on the pad
        return STROKE_FPS if self._mode == "draw" else None

    def _now(self):
        return time.time()

    # --- canvas ------------------------------------------------------------------------------
    def _ensure_image(self, w, h):
        """Create the drawing, or rescale it when the widget size changed."""
        w, h = max(1, w), max(1, h)
        if self.image is None:
            self.image = Image.new("RGB", (w, h), self.bg)
        elif self.image.size != (w, h):
            self.image = self.image.resize((w, h), Image.LANCZOS)
            self._prev = None

    def _toolbar(self, w, h):
        """Return (toolbar top y, [(kind, value, rect)]) in widget-relative coordinates."""
        items = [("color", c) for c in PALETTE] + [("brush", i) for i in range(len(BRUSHES))] + \
                [("eraser", None), ("clear", None), ("save", None)]
        rows = 1 if w >= 640 else 2
        per_row = -(-len(items) // rows)
        bh = max(1, min(max(22, min(52, h // 9)), h // (2 * rows)))      # never more than half the pad
        top = h - rows * bh
        bw = w / per_row
        out = []
        for i, (kind, val) in enumerate(items):
            r, c = divmod(i, per_row)
            x0, x1 = int(c * bw) + 2, int((c + 1) * bw) - 2
            out.append((kind, val, (x0, top + r * bh + 2, max(x0, x1), max(top + r * bh + 2, top + (r + 1) * bh - 2))))
        return top, out

    # --- touch -------------------------------------------------------------------------------
    def on_touch(self, x, y, down):
        w, h = self.box[2] - self.box[0], self.box[3] - self.box[1]
        if w <= 0 or h <= 0:
            return
        self._ensure_image(w, h)
        if down and self._mode is None:                       # a new touch: toolbar buttons act on touch-down
            self._start, self._moved = (x, y), False
            if y >= self._toolbar(w, h)[0]:
                self._mode = "toolbar"
                self._press_toolbar(x, y, w, h)
            else:
                self._mode, self._prev = "draw", (x, y)
        elif down and self._mode == "draw":
            # Ink starts once the finger leaves the tap radius, so a plain tap can open fullscreen instead.
            self._moved = self._moved or max(abs(x - self._start[0]), abs(y - self._start[1])) > TAP_PX
            if self._moved:
                self._stamp(*self._prev, x, y)
                self._prev = (x, y)
        if not down:
            if self._mode == "draw" and not self._moved:
                if self._full or not self.cfg.get("on_tap"):
                    self._stamp(x, y, x, y)               # a tap leaves a dot
                else:                                     # tap on the small pad: run its on_tap (e.g. fullscreen)
                    run = self.ctx.get("run_action")
                    if run:
                        run(self.cfg["on_tap"], self)
            self._mode, self._prev = None, None

    _full = False

    def _stamp(self, x0, y0, x1, y1):
        """Draw a round-capped line from (x0, y0) to (x1, y1) in the current color (or eraser)."""
        d = ImageDraw.Draw(self.image)
        fill = self.bg if self.eraser else self.color
        r = BRUSHES[self.brush] / 2 * (2 if self.eraser else 1)
        d.line((x0, y0, x1, y1), fill=fill, width=max(1, int(r * 2)))
        for px, py in ((x0, y0), (x1, y1)):                 # round caps keep thick strokes smooth
            d.ellipse((px - r, py - r, px + r, py + r), fill=fill)

    def _press_toolbar(self, x, y, w, h):
        for kind, val, (rx0, ry0, rx1, ry1) in self._toolbar(w, h)[1]:
            if rx0 <= x <= rx1 and ry0 <= y <= ry1:
                if kind == "color":
                    self.color, self.eraser = parse_color(val), False
                elif kind == "brush":
                    self.brush = val
                elif kind == "eraser":
                    self.eraser = not self.eraser
                elif kind == "clear":
                    self.image = Image.new("RGB", self.image.size, self.bg)
                elif kind == "save":
                    self.save()
                return

    def save(self):
        """Write the drawing to ``save_dir`` as a timestamped PNG; returns the path (or None on failure)."""
        folder = pathlib.Path(self.cfg.get("save_dir") or "~/Pictures/wigix").expanduser()
        path = folder / f"wigix-{dt.datetime.now():%Y%m%d-%H%M%S}.png"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.image.save(path)
        except OSError as e:
            log.warning("could not save drawing: %s", e)
            self._notice = ("save failed", self._now())
            return None
        self._notice = ("saved", self._now())
        return path

    # --- drawing -----------------------------------------------------------------------------
    def draw(self, cv, box):
        self._full = False
        self._render(cv, box)

    def draw_full(self, cv, box):
        self._full = True
        self._render(cv, box)

    def _render(self, cv, box):
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return
        self._ensure_image(w, h)
        cv.image.paste(self.image, (x0, y0))
        top, items = self._toolbar(w, h)
        cv.d.rectangle((x0, y0 + top, x1, y1), fill=THEME["bg"])
        for kind, val, (rx0, ry0, rx1, ry1) in items:
            r = (x0 + rx0, y0 + ry0, x0 + rx1, y0 + ry1)
            cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
            active = False
            if kind == "color":
                cv.d.rounded_rectangle(r, radius=6, fill=parse_color(val))
                active = not self.eraser and parse_color(val) == self.color
            elif kind == "brush":
                rad = max(2, min(BRUSHES[val] // 2 + 1, (r[3] - r[1]) // 2 - 3))
                cv.d.rounded_rectangle(r, radius=6, fill=THEME["card"])
                cv.d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=THEME["text"])
                active = val == self.brush
            else:
                cv.d.rounded_rectangle(r, radius=6, fill=THEME["card"])
                cv.text((cx, cy), {"eraser": "erase", "clear": "clear", "save": "save"}[kind],
                        size=16, anchor="mm", max_width=r[2] - r[0] - 4)
                active = kind == "eraser" and self.eraser
            if active:
                cv.d.rounded_rectangle(r, radius=6, outline=THEME["accent"], width=3)
        text, t = self._notice
        if text and self._now() - t < SAVED_SECS:
            color = THEME["ok"] if text == "saved" else THEME["crit"]
            cv.d.rounded_rectangle((x0 + 10, y0 + 10, x0 + 110, y0 + 38), radius=8, fill=(0, 0, 0), outline=color)
            cv.text((x0 + 60, y0 + 24), text, size=18, color=color, anchor="mm", max_width=90)
