"""Drawing helpers shared by widgets: theme, fonts, cards, bars, sparklines."""
from __future__ import annotations

from functools import lru_cache

from PIL import ImageDraw, ImageFont

THEME = {
    "bg": (10, 12, 16),
    "card": (28, 32, 40),
    "text": (232, 236, 242),
    "dim": (128, 136, 150),
    "ok": (74, 214, 130),
    "warn": (245, 190, 60),
    "crit": (240, 84, 84),
    "accent": (86, 156, 255),
}

_FONT_PATHS = [
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]
_BOLD_PATHS = [p.replace("Mono.ttf", "Mono-Bold.ttf") for p in _FONT_PATHS[:3]]


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in (_BOLD_PATHS if bold else []) + _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def level_color(frac: float, warn: float = 0.7, crit: float = 0.9):
    return THEME["crit"] if frac >= crit else THEME["warn"] if frac >= warn else THEME["ok"]


class Canvas:
    """A PIL ImageDraw plus the small set of primitives widgets need."""

    def __init__(self, draw: ImageDraw.ImageDraw):
        self.d = draw

    def card(self, box, title: str | None = None):
        x0, y0, x1, y1 = box
        self.d.rounded_rectangle(box, radius=14, fill=THEME["card"])
        if title:
            self.text((x0 + 14, y0 + 8), title, size=18, color=THEME["dim"])
        return x0 + 14, y0 + (34 if title else 14), x1 - 14, y1 - 12  # inner box

    def text(self, xy, s, size=20, color=None, bold=False, anchor="la", max_width=None):
        if max_width:  # shrink to fit
            while size > 10 and font(size, bold).getlength(str(s)) > max_width:
                size -= 2
        self.d.text(xy, str(s), font=font(size, bold), fill=color or THEME["text"], anchor=anchor)

    def bar(self, box, frac: float, color=None):
        x0, y0, x1, y1 = box
        frac = max(0.0, min(1.0, frac))
        self.d.rounded_rectangle(box, radius=(y1 - y0) // 2, fill=(14, 16, 22))
        if frac > 0:
            w = max(y1 - y0, int((x1 - x0) * frac))
            self.d.rounded_rectangle((x0, y0, x0 + w, y1), radius=(y1 - y0) // 2,
                                     fill=color or level_color(frac))

    def spark(self, box, values, vmax=None, color=None):
        x0, y0, x1, y1 = box
        vals = list(values)
        if len(vals) < 2:
            return
        top = vmax if vmax else (max(vals) or 1)
        pts = [(x0 + (x1 - x0) * i / (len(vals) - 1),
                y1 - (y1 - y0) * min(v, top) / top) for i, v in enumerate(vals)]
        self.d.line(pts, fill=color or THEME["accent"], width=2)


def read_secret(value=None, file=None, env=None):
    """Resolve a secret from an inline value, a file (stripped), or an environment variable."""
    import os
    if value:
        return value
    if file:
        return open(os.path.expanduser(file)).read().strip()
    return os.environ.get(env or "", "")
