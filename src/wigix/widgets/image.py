# SPDX-License-Identifier: GPL-3.0-only
"""The ``image`` widget: a static picture or animated GIF from a file or an http(s) URL.

Config: ``source`` (path or URL), ``fit`` (cover | contain | stretch), ``refresh`` (seconds between re-downloads of
a URL; local files are reloaded whenever they change), ``rounded`` (round the corners, default true), ``name``
(optional label drawn over the picture).

Loading happens in ``update()`` on the worker thread; ``draw()`` only scales an already decoded frame (cached per
size), so it never blocks. The helpers at the top are shared with the ``video`` and ``camera`` widgets.
"""
from __future__ import annotations

import io
import os
import pathlib
import time
from functools import lru_cache

from PIL import Image, ImageDraw, ImageOps, ImageSequence

from . import Widget, widget
from ..integrations.net import describe_error, fetch_limited
from ..render import THEME, font

FITS = ("cover", "contain", "stretch")
RADIUS = 14                                   # matches the corner radius of cards
MAX_FRAMES = 300                              # animated GIFs longer than this are cut off (memory)
MAX_BYTES = 30 * 1024 * 1024                  # a download is cut off (not read to the end) beyond this

FIT = {"name": "fit", "type": "string", "default": "cover", "help": "cover (crop to fill) | contain (letterbox) | stretch"}
ROUNDED = {"name": "rounded", "type": "bool", "default": True, "help": "round the corners like a card"}
NAME = {"name": "name", "type": "string", "default": "", "help": "label drawn over the picture"}


# --- helpers shared by the media widgets ------------------------------------------------------------
def fit_tile(im: Image.Image, size: tuple[int, int], fit: str, bg) -> Image.Image:
    """Scale ``im`` to exactly ``size`` as an RGB tile. Transparent areas and letterbox bars show ``bg``."""
    size = w, h = max(1, size[0]), max(1, size[1])
    if fit == "stretch":
        scaled = im.resize(size, Image.BILINEAR)
    elif fit == "contain":
        scaled = im.copy()
        scaled.thumbnail(size, Image.BILINEAR)          # keeps the aspect ratio; may be smaller than ``size``
    else:                                               # cover: the default, also for unknown values
        scaled = ImageOps.fit(im, size, Image.BILINEAR)
    tile = Image.new("RGB", size, bg)
    mask = scaled.getchannel("A") if scaled.mode == "RGBA" else None
    tile.paste(scaled, ((w - scaled.width) // 2, (h - scaled.height) // 2), mask)
    return tile


@lru_cache(maxsize=16)
def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    """An anti-aliased rounded-rectangle alpha mask (drawn 4x larger, then shrunk)."""
    big = Image.new("L", (size[0] * 4, size[1] * 4), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, big.width - 1, big.height - 1), radius=radius * 4, fill=255)
    return big.resize(size, Image.LANCZOS)


def paste_tile(cv, tile: Image.Image, box, rounded: bool = True) -> None:
    """Put an RGB tile onto the canvas at ``box`` (its top-left corner), optionally with rounded corners."""
    x0, y0, x1, y1 = box
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    if tile.size != (x1 - x0, y1 - y0):                  # e.g. a video frame from before a resize
        tile = tile.resize((x1 - x0, y1 - y0), Image.BILINEAR)
    cv.image.paste(tile, (x0, y0), rounded_mask(tile.size, RADIUS) if rounded else None)


def placeholder(cv, box, text: str, color=None, title: str | None = None) -> None:
    """A card with one centered status line (loading, missing file, ffmpeg missing, ...)."""
    x0, y0, x1, y1 = cv.card(box, title)
    cv.text(((x0 + x1) // 2, (y0 + y1) // 2), text, size=20, color=color or THEME["dim"], anchor="mm", max_width=x1 - x0)


def pill(cv, xy, text: str, color=None, fill=(0, 0, 0), alpha: int = 150, size: int = 16, anchor: str = "la") -> int:
    """A small translucent label for overlays. ``xy`` is the corner named by ``anchor`` (la = top-left, ra = top-right,
    lb = bottom-left); returns the pill's width so callers can line several up."""
    f = font(size)
    tw = int(f.getlength(text))
    w, h = tw + 18, size + 12
    x, y = xy
    x -= w if anchor[0] == "r" else 0
    y -= h if anchor[1] == "b" else 0
    layer = Image.new("RGB", (w, h), fill)
    cv.image.paste(layer, (x, y), rounded_mask((w, h), h // 2).point(lambda v: v * alpha // 255))
    cv.d.text((x + 9, y + h // 2), text, font=f, fill=color or THEME["text"], anchor="lm")
    return w


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


# --- the widget ---------------------------------------------------------------------------------------
class _Loaded:
    """One decoded image: RGBA frames plus how long each is shown (seconds). Replaced as a whole, never mutated."""

    def __init__(self, frames, durations, generation):
        self.frames, self.durations, self.generation = frames, durations, generation
        self.total = sum(durations)

    def index_at(self, t: float) -> int:
        """Which frame is showing ``t`` seconds after the animation started (it loops)."""
        if len(self.frames) == 1 or self.total <= 0:
            return 0
        t %= self.total
        for i, d in enumerate(self.durations):
            if t < d:
                return i
            t -= d
        return len(self.frames) - 1


def decode(data: bytes | pathlib.Path) -> tuple[list, list]:
    """Decode a still image or animated GIF to ([RGBA frames], [seconds per frame])."""
    im = Image.open(io.BytesIO(data) if isinstance(data, bytes) else data)
    frames, durations = [], []
    for frame in ImageSequence.Iterator(im):
        f = frame.convert("RGBA")
        f.thumbnail((1016, 592), Image.BILINEAR)         # never bigger than the panel: bounds memory and scaling cost
        frames.append(f)
        ms = frame.info.get("duration", 100)
        durations.append((ms if ms > 10 else 100) / 1000)  # browsers treat 0-10 ms as "default"
        if len(frames) >= MAX_FRAMES:
            break
    return frames, durations


@widget("image")
class ImageWidget(Widget):
    """A picture or animated GIF from a file or URL, scaled to the widget (cover, contain or stretch)."""
    interval = 1.0                     # cheap: file mtime check, or "is the URL due for a refresh?"
    fields = [{"name": "source", "type": "string", "default": "", "help": "file path or http(s) URL (PNG, JPEG, GIF, ...)"},
              FIT,
              {"name": "refresh", "type": "int", "default": 300, "help": "seconds between re-downloads of a URL"},
              ROUNDED, NAME]

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self._loaded: _Loaded | None = None
        self._message = "loading…"       # shown while there is no picture
        self._stamp = None               # what the current picture came from: file mtime, or download time
        self._next_try = 0.0
        self._generation = 0
        self._t0 = time.monotonic()      # animation clock
        self._tiles: dict[int, Image.Image] = {}
        self._tiles_key = None

    # worker thread ---------------------------------------------------------------------------------
    def update(self):
        source = str(self.cfg.get("source") or "").strip()
        if not source:
            self._message = "no source"
            return
        now = time.time()
        try:
            if is_url(source):
                refresh = max(1.0, float(self.cfg.get("refresh") or 300))
                if self._loaded is not None and now - (self._stamp or 0) < refresh:
                    return
                if now < self._next_try:
                    return
                self._next_try = now + min(refresh, 30.0)            # a failing URL is retried, not hammered
                self._install(*decode(fetch_limited(source, MAX_BYTES, timeout=10)), stamp=now)
            else:
                path = pathlib.Path(os.path.expanduser(source))
                mtime = path.stat().st_mtime
                if self._loaded is not None and mtime == self._stamp:
                    return
                self._install(*decode(path), stamp=mtime)
        except FileNotFoundError:
            self._loaded, self._message = None, "file not found"
        except Exception as e:                                       # bad data, HTTP error, timeout ...
            if self._loaded is None:                                 # keep showing the old picture if we have one
                self._message = "can't load image"
            self.error = describe_error(e)                           # never the URL's query string

    def _install(self, frames, durations, stamp):
        self._generation += 1
        self._stamp, self._message, self.error = stamp, "", None
        self._loaded = _Loaded(frames, durations, self._generation)
        self.fps = 10 if len(frames) > 1 else None                   # only animated pictures need fast redraws

    # render thread ---------------------------------------------------------------------------------
    def draw(self, cv, box):
        loaded = self._loaded
        if loaded is None:
            placeholder(cv, box, self._message, color=THEME["crit"] if "can't" in self._message else None)
            return
        size = (box[2] - box[0], box[3] - box[1])
        fit = self.cfg.get("fit", "cover")
        bg = THEME["card"]
        key = (loaded.generation, size, fit, bg)
        if key != self._tiles_key:                                   # new picture, size or theme: drop scaled copies
            self._tiles, self._tiles_key = {}, key
        i = loaded.index_at(time.monotonic() - self._t0)
        if i not in self._tiles:
            self._tiles[i] = fit_tile(loaded.frames[i], size, fit, bg)
        paste_tile(cv, self._tiles[i], box, self.cfg.get("rounded", True))
        if self.cfg.get("name"):
            pill(cv, (box[0] + 10, box[1] + 10), str(self.cfg["name"]))
