# SPDX-License-Identifier: GPL-3.0-only
"""The ``camera`` widget: a security or pet camera, from one of four sources (configure exactly one):

- ``snapshot_url``: a JPEG polled every ``refresh`` seconds (optional ``headers``, or a bearer ``token`` / ``token_file``)
- ``stream_url``: an RTSP or MJPEG stream decoded by ffmpeg (see widgets/video.py), live while the page is visible
- ``ha_camera``: a Home Assistant ``camera.*`` entity, fetched through the ``home_assistant`` integration
- ``frigate`` + ``camera``: a Frigate NVR (``frigate`` is its base URL, or ``true`` together with ``url``). Frames come
  from ``<url>/api/<camera>/latest.jpg``; a person/car/... detected in the last minute shows as a red label.

The camera name and a LIVE / stale badge are drawn over the picture. A tap opens it fullscreen unless the widget has
its own ``on_tap``. Network I/O happens in ``update()`` on the worker thread; ``draw()`` only scales what has arrived.
"""
from __future__ import annotations

import io
import json
import time

import requests
from PIL import Image

from . import Widget, widget
from ..integrations.net import describe_error, fetch_limited, tls_verify
from .image import FIT, RADIUS, ROUNDED, fit_tile, paste_tile, pill, placeholder
from .video import BAD_SOURCE, FfmpegStream, clamp_fps
from ..render import THEME, parse_color, read_secret

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024   # a snapshot or a Frigate frame is cut off beyond this
MAX_EVENT_BYTES = 1024 * 1024
EVENT_WINDOW = 60.0          # seconds a Frigate detection stays on screen
STREAM_FPS, FULL_FPS = 10, 15
SOURCES = ("snapshot_url", "stream_url", "ha_camera", "frigate")


@widget("camera")
class CameraWidget(Widget):
    """A camera picture (snapshot, stream, Home Assistant or Frigate) with name and LIVE/stale badge."""
    fields = [{"name": "name", "type": "string", "default": "", "help": "label drawn over the picture"},
              {"name": "snapshot_url", "type": "string", "default": "", "help": "URL of a JPEG still"},
              {"name": "stream_url", "type": "string", "default": "", "help": "RTSP or MJPEG stream URL"},
              {"name": "ha_camera", "type": "entity", "default": "", "help": "Home Assistant camera entity, e.g. camera.porch"},
              {"name": "integration", "type": "string", "default": "", "help": "Home Assistant integration (optional if only one)"},
              {"name": "frigate", "type": "string", "default": "", "help": "Frigate base URL (or true, with url:)"},
              {"name": "url", "type": "string", "default": "", "help": "Frigate base URL when frigate is true"},
              {"name": "camera", "type": "string", "default": "", "help": "Frigate camera name"},
              {"name": "refresh", "type": "float", "default": 2, "help": "seconds between snapshots"},
              {"name": "token", "type": "string", "default": "", "help": "bearer token for the snapshot/Frigate server"},
              {"name": "token_file", "type": "string", "default": "", "help": "file holding the bearer token"},
              {"name": "headers", "type": "yaml", "default": {}, "help": "extra HTTP headers for snapshots"},
              {"name": "verify", "type": "bool", "default": True, "help": "verify TLS certificates: true, false, or a CA bundle path (YAML)"},
              {"name": "border", "type": "color", "default": "", "help": "coloured outline (match it to a graph line)"},
              FIT, ROUNDED]

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self._image: Image.Image | None = None           # newest decoded snapshot
        self._seq = 0
        self._image_time = 0.0                           # time.monotonic() when it arrived
        self._event: tuple[str, float] | None = None     # Frigate: (label, start time as epoch seconds)
        self._last_draw = 0.0
        self._full_seen = 0.0
        self._stream: FfmpegStream | None = None
        self._tile_key = None
        self._tile: Image.Image | None = None

    # configuration -----------------------------------------------------------------------------------
    @property
    def source(self) -> str | None:
        """Which of the four sources is configured (None when none, "multiple" when more than one)."""
        used = [s for s in SOURCES if self.cfg.get(s)]
        return used[0] if len(used) == 1 else ("multiple" if used else None)

    @property
    def refresh(self) -> float:
        try:
            return max(0.5, float(self.cfg.get("refresh") or 2))
        except (TypeError, ValueError):
            return 2.0

    @property
    def fullscreen(self) -> bool:
        """True while draw_full() is being called (the dashboard calls it on every frame of a fullscreen widget)."""
        return time.monotonic() - self._full_seen < 1.5

    @property
    def interval(self) -> float:                         # worker period: snapshots a bit faster when fullscreen
        return min(self.refresh, 1.0) if self.fullscreen else self.refresh

    @property
    def fps(self):                                       # redraw rate asked of the dashboard
        if self.source == "stream_url":
            return FULL_FPS if self.fullscreen else STREAM_FPS
        return None

    @property
    def label(self) -> str:
        return str(self.cfg.get("name") or (self.cfg.get("camera") if self.source == "frigate" else "")
                   or (self.cfg.get("ha_camera") or ""))

    def frigate_base(self) -> str:
        f = self.cfg.get("frigate")
        return (f if isinstance(f, str) and "://" in f else str(self.cfg.get("url") or "")).rstrip("/")

    def _headers(self) -> dict:
        headers = dict(self.cfg.get("headers") or {})
        token = read_secret(self.cfg.get("token"), self.cfg.get("token_file"))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # worker thread -----------------------------------------------------------------------------------
    def update(self):
        try:
            self._update()
        except requests.RequestException as e:
            raise RuntimeError(describe_error(e)) from None          # the URL (and its query string) never reaches the error

    def _update(self):
        source = self.source
        if source in (None, "multiple", "stream_url"):
            return                                       # stream frames arrive on ffmpeg's own threads
        if self._image is not None and self._last_draw and time.monotonic() - self._last_draw > max(30.0, 5 * self.refresh):
            return                                       # nobody is looking (hidden page): stop polling the camera
        if source == "ha_camera":
            ha = self.ctx["integrations"].get(self.cfg.get("integration") or None, "home_assistant")
            data = ha.camera_image(self.cfg["ha_camera"])
        else:
            base = self.frigate_base()
            if source == "frigate":
                if not base or not self.cfg.get("camera"):
                    raise ValueError("frigate needs a url and a camera name")
                url = f"{base}/api/{self.cfg['camera']}/latest.jpg"
            else:
                url = self.cfg["snapshot_url"]
            data = fetch_limited(url, MAX_SNAPSHOT_BYTES, headers=self._headers(), timeout=6,
                                 verify=tls_verify(self.cfg.get("verify")))
        if len(data) > MAX_SNAPSHOT_BYTES:
            raise ValueError("image too large")
        im = Image.open(io.BytesIO(data))             # raises DecompressionBombError for absurd pixel counts
        im.draft("RGB", (1016, 592))                     # JPEG: decode at reduced size when the camera is bigger
        self._image, self._seq, self._image_time = im.convert("RGB"), self._seq + 1, time.monotonic()
        if source == "frigate":
            self._poll_event(base)

    def _poll_event(self, base: str) -> None:
        """Remember the newest Frigate event so draw() can label it while it is recent. Failures are not fatal."""
        try:
            events = json.loads(fetch_limited(f"{base}/api/events", MAX_EVENT_BYTES,
                                              params={"camera": self.cfg["camera"], "limit": 1},
                                              headers=self._headers(), timeout=6,
                                              verify=tls_verify(self.cfg.get("verify"))))
            ev = events[0] if events else None
            self._event = (str(ev.get("label") or "event"), float(ev["start_time"])) if ev else None
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):   # ValueError: bad JSON, too large
            self._event = None

    # render thread -----------------------------------------------------------------------------------
    def on_tap(self, x, y):
        if self.cfg.get("on_tap") is not None:
            return False                                 # the configured action runs instead
        self.ctx["run_action"]("fullscreen", self)
        return True

    def draw_full(self, cv, box):
        self._full_seen = time.monotonic()
        self.draw(cv, box)

    def _picture(self, size) -> tuple[Image.Image, float] | None:
        """The newest frame scaled to ``size`` plus its arrival time, or None while there is none."""
        fit, bg = self.cfg.get("fit", "cover"), THEME["card"]
        if self.source == "stream_url":
            if self._stream is None or self._stream.source != str(self.cfg["stream_url"]).strip() or self._stream.fit != fit:
                if self._stream:
                    self._stream.stop()
                self._stream = FfmpegStream(str(self.cfg["stream_url"]).strip(), fit,
                                            FULL_FPS if self.fullscreen else STREAM_FPS, loop=True, bg=bg)
            self._stream.fps = clamp_fps(self.fps)       # ffmpeg picks it up the next time it (re)starts
            self._stream.touch(size)
            got = self._stream.frame()
            return None if got is None else (got[0], got[1])
        if self._image is None:
            return None
        key = (self._seq, size, fit, bg)
        if key != self._tile_key:                        # scale once per new snapshot, not on every redraw
            self._tile, self._tile_key = fit_tile(self._image, size, fit, bg), key
        return self._tile, self._image_time

    def draw(self, cv, box):
        self._draw(cv, box)
        color = parse_color(self.cfg.get("border"))
        if color:                                        # a coloured outline, e.g. matching this camera's line on a graph
            cv.d.rounded_rectangle(tuple(box), radius=RADIUS, outline=color, width=4)

    def _draw(self, cv, box):
        self._last_draw = time.monotonic()
        source = self.source
        if source is None or source == "multiple":
            placeholder(cv, box, "no source" if source is None else "use only one source", title=self.label or None)
            return
        size = (box[2] - box[0], box[3] - box[1])
        got = self._picture(size)
        if got is None:
            status = self._stream.status if self._stream else None
            offline = status in BAD_SOURCE or (self.error and not self._stream)
            placeholder(cv, box, status or ("no signal" if self.error else "loading…"),
                        color=THEME["crit"] if offline else None, title=self.label or None)
            return
        tile, arrived = got
        paste_tile(cv, tile, box, self.cfg.get("rounded", True))
        x0, y0, x1, y1 = box
        if self.label:
            pill(cv, (x0 + 10, y0 + 10), self.label, color=parse_color(self.cfg.get("border")))
        # LIVE while frames keep coming; stale when the newest one is older than 3x the refresh period
        stale = time.monotonic() - arrived > (3.0 if source == "stream_url" else 3 * self.refresh)
        inset = 70 if self.fullscreen else 10                 # fullscreen: leave room for the close button
        pill(cv, (x1 - inset, y0 + 10), "stale" if stale else "● LIVE", color=THEME["warn"] if stale else THEME["ok"], anchor="ra")
        if self._event and time.time() - self._event[1] < EVENT_WINDOW:
            pill(cv, (x0 + 10, y1 - 10), self._event[0], color=(255, 255, 255), fill=(200, 40, 40), alpha=235, size=20,
                 anchor="lb")
