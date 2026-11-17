# SPDX-License-Identifier: GPL-3.0-only
"""Drawing helpers shared by widgets: theme, fonts, cards, bars, sparklines."""
from __future__ import annotations

import contextlib
import threading
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFont

from .settings import resolve_theme

_BASE_THEME = {
    "bg": (10, 12, 16),
    "card": (28, 32, 40),
    "text": (232, 236, 242),
    "dim": (128, 136, 150),
    "ok": (74, 214, 130),
    "warn": (245, 190, 60),
    "crit": (240, 84, 84),
    "accent": (86, 156, 255),
}
_local = threading.local()


class _Theme:
    """Colors, readable as THEME["card"]. A per-thread override (see theme_override) wins over the
    global theme, so the editor preview and the live daemon can render different themes at once."""

    def __getitem__(self, key):
        return (getattr(_local, "override", None) or {}).get(key) or _BASE_THEME[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


THEME = _Theme()


def _parse_theme(d):
    out = {}
    for k, v in resolve_theme(d).items():
        c = parse_color(v)
        if k in _BASE_THEME and c:
            out[k] = c
    return out


def set_theme(d):
    """Apply a ``theme:`` config block (hex colors) globally; unspecified keys reset to defaults."""
    _BASE_THEME.update({"bg": (10, 12, 16), "card": (28, 32, 40), "text": (232, 236, 242), "dim": (128, 136, 150),
                        "ok": (74, 214, 130), "warn": (245, 190, 60), "crit": (240, 84, 84), "accent": (86, 156, 255)})
    _BASE_THEME.update(_parse_theme(d))


@contextlib.contextmanager
def theme_override(d):
    """Apply a widget's ``theme:`` overrides for the duration of its draw() on this thread."""
    prev = getattr(_local, "override", None)
    _local.override = {**(prev or {}), **_parse_theme(d)} if d else prev
    try:
        yield
    finally:
        _local.override = prev


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


def mix(a, b, t: float):
    """Blend colour a toward b by t (0..1)."""
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def level_color(frac: float, warn: float = 0.7, crit: float = 0.9):
    return THEME["crit"] if frac >= crit else THEME["warn"] if frac >= warn else THEME["ok"]


class Canvas:
    """A PIL ImageDraw plus the small set of primitives widgets need."""

    def __init__(self, draw: ImageDraw.ImageDraw, image=None):
        self.d = draw
        self.image = image if image is not None else getattr(draw, "_image", None)

    def card(self, box, title: str | None = None):
        x0, y0, x1, y1 = box
        self.d.rounded_rectangle(box, radius=14, fill=THEME["card"], outline=mix(THEME["card"], THEME["text"], 0.09))
        if title:
            self.text((x0 + 14, y0 + 8), title, size=18, color=THEME["dim"])
        return x0 + 14, y0 + (34 if title else 14), x1 - 14, y1 - 12  # inner box

    def frame(self, box, title=None, right=None, style="btop", color=None, right_color=None):
        """A panel for a machine/tile; returns the inner box. ``style="btop"`` is a thin rounded outline with the
        title set into the top border (like btop's boxes), ``"card"`` is the filled card. ``right`` is a short
        status text set into the border's right end."""
        if style == "card":
            inner = self.card(box, title)
            if right:
                self.text((box[2] - 14, box[1] + 8), right, size=18, color=right_color or THEME["dim"], anchor="ra")
            return inner
        x0, y0, x1, y1 = (int(v) for v in box)
        line = mix(THEME["bg"], THEME["dim"], 0.6)
        self.d.rounded_rectangle((x0, y0 + 9, x1, y1), radius=10, outline=line, width=2)
        for text, anchor, x, col, bold in ((title, "la", x0 + 18, color or THEME["accent"], True),
                                           (right, "ra", x1 - 18, right_color or THEME["dim"], False)):
            if not text:
                continue
            tw = font(17, bold).getlength(str(text))
            gx = x if anchor == "la" else x - tw
            self.d.rectangle((gx - 6, y0 + 4, gx + tw + 6, y0 + 16), fill=THEME["bg"])
            self.text((x, y0), text, size=17, color=col, bold=bold, anchor=anchor)
        return x0 + 16, y0 + 30, x1 - 16, y1 - 14

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

    def area(self, box, series, window, vmax=None, color=None, now=None, autorange=False, lines=()):
        """A filled line chart of ``series`` ([(unix_time, value)]) over the last ``window`` seconds.

        Drawn on a recessed plate with a faint midline; the fill fades toward the bottom and the newest point
        gets a dot. ``vmax`` fixes the top of the scale (e.g. 100 for percentages), else it autoscales."""
        import time as _time
        x0, y0, x1, y1 = (int(v) for v in box)
        w, h = x1 - x0, y1 - y0
        if w < 8 or h < 8:
            return
        color = color or THEME["accent"]
        self.d.rounded_rectangle((x0, y0, x1, y1), radius=7, fill=mix(THEME["card"], THEME["bg"], 0.55))
        self.d.line((x0 + 6, y0 + h // 2, x1 - 6, y0 + h // 2), fill=mix(THEME["card"], THEME["text"], 0.06), width=1)
        pts = [(t, v) for t, v in series if v is not None]
        if len(pts) < 2 or self.image is None:
            return
        now = now or _time.time()
        lo, hi = min(v for _, v in pts), max(v for _, v in pts)
        if autorange:                       # zoom to the data (temperature, pressure): a flat line would be unreadable
            span = max(hi - lo, abs(hi) * 0.01, 1e-9)
            floor, top = lo - span * 0.25, hi + span * 0.25
        else:
            floor, top = 0.0, vmax or max(hi * 1.15, 1e-9)
        pad = 4
        yof = lambda v: h - pad - (h - 2 * pad) * max(0.0, min(1.0, (v - floor) / (top - floor)))
        xy = [(pad + (w - 2 * pad) * max(0.0, min(1.0, 1 - (now - t) / window)), yof(v)) for t, v in pts]
        for value, lcolor in lines:         # dashed reference lines (thresholds, averages)
            if floor <= value <= top:
                ly = y0 + yof(value)
                for dx in range(x0 + 6, x1 - 6, 10):
                    self.d.line((dx, ly, min(dx + 5, x1 - 6), ly), fill=lcolor, width=1)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).polygon(xy + [(xy[-1][0], h), (xy[0][0], h)], fill=255)
        fade = Image.linear_gradient("L").rotate(180).resize((w, h))           # strong at the top, gone at the bottom
        mask = ImageChops.multiply(mask, fade.point(lambda p: int(p * 0.42)))
        corners = Image.new("L", (w, h), 0)
        ImageDraw.Draw(corners).rounded_rectangle((0, 0, w - 1, h - 1), radius=7, fill=255)
        self.image.paste(Image.new("RGB", (w, h), color), (x0, y0), ImageChops.multiply(mask, corners))
        self.d.line([(x0 + x, y0 + y) for x, y in xy], fill=color, width=2, joint="curve")
        lx, ly = x0 + xy[-1][0], y0 + xy[-1][1]
        self.d.ellipse((lx - 3, ly - 3, lx + 3, ly + 3), fill=color, outline=THEME["card"])

    def lines(self, box, series_list, window, now=None, fmt=None):
        """Several time series on one plot: ``series_list`` is [(points, (r, g, b), dashed)]. The scale covers all of them
        (zoomed to the data); the top and bottom values are printed in the corners of the plot."""
        import time as _time
        x0, y0, x1, y1 = (int(v) for v in box)
        w, h = x1 - x0, y1 - y0
        if w < 30 or h < 16:
            return
        self.d.rounded_rectangle((x0, y0, x1, y1), radius=7, fill=mix(THEME["card"], THEME["bg"], 0.55))
        vals = [v for pts, _, _ in series_list for _, v in pts]
        if not vals:
            return
        now = now or _time.time()
        lo, hi = min(vals), max(vals)
        span = max(hi - lo, abs(hi) * 0.02, 1e-9)
        floor, top = lo - span * 0.12, hi + span * 0.12
        padx, pady = 30, 6                                   # room on the left for the scale labels
        yof = lambda v: y0 + pady + (h - 2 * pady) * (1 - (v - floor) / (top - floor))
        for frac in (0.25, 0.5, 0.75):
            gy = y0 + pady + (h - 2 * pady) * frac
            self.d.line((x0 + padx, gy, x1 - 6, gy), fill=mix(THEME["card"], THEME["text"], 0.06), width=1)
        fmt = fmt or (lambda v: fmt_number(v, 0) if abs(top - floor) > 8 else fmt_number(v, 1))
        self.text((x0 + 4, y0 + 3), fmt(top - span * 0.12), size=11, color=THEME["dim"])
        self.text((x0 + 4, y1 - 3), fmt(floor + span * 0.12), size=11, color=THEME["dim"], anchor="lb")
        for pts, color, dashed in series_list:
            if len(pts) < 2:
                continue
            xy = [(x0 + padx + (w - padx - 6) * max(0.0, min(1.0, 1 - (now - t) / window)), yof(v)) for t, v in pts]
            if dashed:
                for (ax, ay), (bx, by) in zip(xy, xy[1:]):
                    if int(ax) // 6 % 2 == 0:
                        self.d.line((ax, ay, bx, by), fill=color, width=2)
            else:
                self.d.line(xy, fill=color, width=2, joint="curve")
            lx, ly = xy[-1]
            self.d.ellipse((lx - 3, ly - 3, lx + 3, ly + 3), fill=color, outline=THEME["card"])

    def metric(self, box, label, value_text, series, window, frac=None, vmax=None, color=None, **area_kw):
        """One graph row: label left, value right (coloured by ``frac`` load), area chart filling the rest."""
        x0, y0, x1, y1 = box
        col = color or (metric_color(frac) if frac is not None else THEME["accent"])
        self.text((x0, y0), label, size=16, color=THEME["dim"])
        room = x1 - x0 - font(16).getlength(str(label)) - 10         # the value shrinks before it can touch the label
        self.text((x1, y0 - 2), value_text, size=20, bold=True, color=col if frac is not None else THEME["text"], anchor="ra",
                  max_width=room)
        self.area((x0, y0 + 24, x1, y1), series, window, vmax=vmax, color=col, **area_kw)

    def meter(self, box, label, value_text, frac):
        """A slim labelled bar for slow-moving values such as disk usage."""
        x0, y0, x1, y1 = box
        self.text((x0, y0), label, size=16, color=THEME["dim"])
        self.text((x1, y0), value_text, size=16, anchor="ra")
        self.bar((x0, y0 + 22, x1, y0 + 30), frac)

    def spark(self, box, values, vmax=None, color=None):
        x0, y0, x1, y1 = box
        vals = list(values)
        if len(vals) < 2:
            return
        top = vmax if vmax else (max(vals) or 1)
        pts = [(x0 + (x1 - x0) * i / (len(vals) - 1),
                y1 - (y1 - y0) * min(v, top) / top) for i, v in enumerate(vals)]
        self.d.line(pts, fill=color or THEME["accent"], width=2)


def metric_color(frac: float):
    """Colour for a 0..1 utilisation: ok below 70 %, warn below 90 %, crit above."""
    return level_color(frac)


def read_secret(value=None, file=None, env=None):
    """Resolve a secret from an inline value, a file (stripped), or an environment variable."""
    import os
    if value:
        return value
    if file:
        return open(os.path.expanduser(file)).read().strip()
    return os.environ.get(env or "", "")


def parse_color(c, default=None):
    """'#rrggbb', 'rrggbb', or an (r,g,b) sequence -> (r, g, b). Falls back to default."""
    try:
        if isinstance(c, (list, tuple)):
            return tuple(int(v) for v in c[:3])
        c = str(c).lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, TypeError, IndexError):
        return default


def fmt_number(value, precision=None):
    """Format numeric strings tidily: 21.4999 -> 21.5, 1000.0 -> 1000. Non-numbers pass through."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if precision is not None:
        return f"{f:.{int(precision)}f}"
    return f"{f:.1f}".rstrip("0").rstrip(".") if abs(f) < 1000 and f != int(f) else f"{f:.0f}"


def rel_time(iso: str) -> str:
    """'2026-11-16T12:00:00+00:00' -> '3m ago'."""
    import datetime as dt
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        s = (dt.datetime.now(dt.timezone.utc) - t).total_seconds()
    except (ValueError, AttributeError):
        return ""
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{int(s // n)}{unit} ago"
    return "just now"
