# SPDX-License-Identifier: GPL-3.0-only
"""The ``video`` widget: play a video file or a stream by piping it through ``ffmpeg`` (no audio).

Config: ``source`` (file path, or a URL ffmpeg understands: rtsp://, http:// MJPEG/HLS, ...), ``fps`` (frames per second
to render, default 12, at most 15), ``loop`` (restart files when they end, default true), ``fit`` (cover | contain |
stretch), ``rounded``, ``name``. Network sources are treated as live streams and reconnect with a growing delay.

ffmpeg runs only while the widget is being drawn: a few seconds after its page is hidden the process is killed, and it
is started again on the next draw. ``FfmpegStream`` does the process handling and is reused by the ``camera`` widget.

The source is untrusted input for ffmpeg: it must be a local path or a URL with one of ``URL_SCHEMES`` (anything else,
such as ``concat:``, ``subfile:`` or ``data:``, is refused), ffmpeg gets a ``-protocol_whitelist`` for the nested
protocols a stream legitimately needs (never ``pipe``), and local paths are passed as ``file:<absolute path>`` so
nothing can be parsed as an option.
"""
from __future__ import annotations

import atexit
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import weakref
from urllib.parse import unquote, urlsplit

from PIL import Image

from . import Widget, widget
from .image import FIT, FITS, NAME, ROUNDED, paste_tile, pill, placeholder
from ..render import THEME

log = logging.getLogger("wigix")

MAX_FPS = 15
PROTOCOL_WHITELIST = "file,http,https,tcp,tls,udp,rtp,rtsp,rtmp,crypto"    # no pipe
URL_SCHEMES = ("http", "https", "rtsp", "rtmp", "rtp", "udp", "tcp")
BAD_SOURCE = ("ffmpeg missing", "file not found", "source not allowed")
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*):")
IDLE_SECONDS = 5.0           # kill ffmpeg when nobody has drawn the widget for this long
_LIVE: "weakref.WeakSet[FfmpegStream]" = weakref.WeakSet()


def classify_source(source: str) -> tuple[str, str] | None:
    """Sort a configured source into ``("net", url)`` or ``("file", absolute path)``; None when it must be refused."""
    source = os.path.expanduser(source.strip())
    if not source or source.startswith("-"):
        return None                                             # would be read as an ffmpeg option
    m = _SCHEME.match(source)
    if m and not source.startswith("/"):
        scheme = m.group(1).lower()
        if scheme == "file":
            path = unquote(urlsplit(source).path)
            return ("file", os.path.abspath(path)) if path.startswith("/") else None
        if scheme in URL_SCHEMES and source[m.end():].startswith("//"):
            return "net", source
        return None                                             # concat:, subfile:, data:, ...
    return "file", os.path.abspath(source)


@atexit.register
def _stop_all():
    """Do not leave ffmpeg children behind when the dashboard exits."""
    for s in list(_LIVE):
        s.stop()


class FfmpegStream:
    """Decodes ``source`` with ffmpeg into raw RGB frames of ``size`` on background threads and keeps only the newest.

    Usage: call ``touch(size)`` from every draw (it starts ffmpeg when needed and tells the stream it is still wanted),
    then read ``frame()``. Nothing here blocks the caller. ``status`` explains why there is no picture
    ("ffmpeg missing", "file not found", "source not allowed", "ended", "reconnecting…") and is None while things work.
    """

    def __init__(self, source: str, fit: str = "cover", fps: float = 12, loop: bool = True,
                 idle: float = IDLE_SECONDS, bg=(28, 32, 40)):
        self.source = os.path.expanduser(str(source))
        kind = classify_source(self.source)
        self.kind, self.target = kind or (None, "")             # target: the URL, or the absolute path of a file
        self.fit = fit if fit in FITS else "cover"
        self.fps = max(1.0, min(float(fps), MAX_FPS))
        self.loop, self.idle, self.bg = loop, idle, bg
        self.is_net = self.kind == "net"
        self.size = (0, 0)
        self.proc: subprocess.Popen | None = None       # the live ffmpeg child, if any (tests check it is reaped)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._quit = threading.Event()
        self._resized = False
        self._last_touch = 0.0
        self._data: tuple[bytes, tuple[int, int], float] | None = None   # newest raw frame, its size, arrival time
        self._image: tuple[int, Image.Image] | None = None               # cache of the decoded newest frame
        self._ended = False
        self._reconnecting = False
        self._problem: str | None = None
        _LIVE.add(self)

    @staticmethod
    def available() -> bool:
        return shutil.which("ffmpeg") is not None

    # public API ---------------------------------------------------------------------------------------
    @property
    def status(self) -> str | None:
        return self._problem or ("ended" if self._ended else "reconnecting…" if self._reconnecting else None)

    def touch(self, size: tuple[int, int]) -> None:
        """Declare that the widget is on screen at ``size``: (re)start ffmpeg if it is not running."""
        if min(size) < 2:
            return
        with self._lock:
            self._last_touch = time.monotonic()
            if size != self.size:
                self.size, self._resized = size, True
                self._kill()                                    # the manager thread restarts it at the new size
            if self._ended or (self._thread and self._thread.is_alive()):
                return
            if self.kind is None:
                self._problem = "source not allowed"
                return
            if not self.available():
                self._problem = "ffmpeg missing"
                return
            if not self.is_net and not os.path.isfile(self.target):
                self._problem = "file not found"
                return
            self._problem = None
            self._quit = threading.Event()
            self._thread = threading.Thread(target=self._manage, args=(self._quit,), daemon=True, name="ffmpeg-manager")
            self._thread.start()

    def frame(self) -> tuple[Image.Image, float] | None:
        """The newest decoded frame and the ``time.monotonic()`` it arrived, or None before the first one."""
        data = self._data
        if data is None:
            return None
        raw, size, ts = data
        if self._image is None or self._image[0] != id(raw):
            self._image = (id(raw), Image.frombytes("RGB", size, raw))
        return self._image[1], ts

    def stop(self) -> None:
        """Stop ffmpeg and wait for it (no zombies). The stream can be ``touch``ed again afterwards."""
        with self._lock:
            self._quit.set()
            self._kill()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(3)
        self._reconnecting = False

    # internals ----------------------------------------------------------------------------------------
    def _argv(self, size) -> list[str]:
        w, h = size
        if self.fit == "stretch":
            scale = f"scale={w}:{h}"
        elif self.fit == "contain":
            color = "0x%02x%02x%02x" % tuple(self.bg[:3])
            scale = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={color}")
        else:
            scale = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
        args = ["ffmpeg", "-nostdin", "-loglevel", "error"]
        if self.target.startswith("rtsp"):
            args += ["-rtsp_transport", "tcp"]                  # UDP drops packets on busy Wi-Fi
        if not self.is_net:
            args += ["-re"]                                     # files: play in real time, not as fast as possible
            if self.loop:
                args += ["-stream_loop", "-1"]
        source = self.target if self.is_net else "file:" + self.target
        return args + ["-protocol_whitelist", PROTOCOL_WHITELIST, "-i", source, "-an", "-vf", f"fps={self.fps},{scale}",
                       "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]

    def _kill(self) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def _manage(self, quit_: threading.Event) -> None:
        """Run ffmpeg, watch it, and restart it (with backoff) when it dies and should not have."""
        backoff = 1.0
        while not quit_.is_set():
            with self._lock:
                size, self._resized = self.size, False
            try:
                proc = self.proc = subprocess.Popen(self._argv(size), stdin=subprocess.DEVNULL,
                                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except OSError as e:
                log.debug("ffmpeg failed to start: %s", e)
                self._problem = "ffmpeg missing"
                return
            self._reconnecting = False
            got = []
            reader = threading.Thread(target=self._read, args=(proc, size, got), daemon=True, name="ffmpeg-reader")
            reader.start()
            idle = False
            while proc.poll() is None and not quit_.is_set() and not self._resized:
                if time.monotonic() - self._last_touch > self.idle:
                    idle = True                                 # the page is hidden: stop burning CPU
                    break
                quit_.wait(0.25)
            if proc.poll() is None:
                proc.kill()
            proc.wait()                                         # reap: no zombie
            reader.join(2)
            if idle or quit_.is_set():
                self._ended = False                             # a later touch starts again from the beginning
                return
            if self._resized:
                continue                                        # restart at the new size right away
            if not self.is_net and not self.loop:
                self._ended = True                              # a file played to the end; keep its last frame
                return
            self._reconnecting = True
            backoff = 1.0 if got else min(backoff * 2, 30.0)
            quit_.wait(backoff)

    def _read(self, proc, size, got: list) -> None:
        n = size[0] * size[1] * 3
        while True:
            raw = proc.stdout.read(n)
            if len(raw) < n:
                break
            self._data = (raw, size, time.monotonic())          # drop the previous frame: only the newest is kept
            got.append(1)
        proc.stdout.close()


def clamp_fps(value, default: float = 12) -> float:
    try:
        return max(1.0, min(float(value if value not in (None, "") else default), MAX_FPS))
    except (TypeError, ValueError):
        return default


@widget("video")
class VideoWidget(Widget):
    """A video file or live stream (no sound), decoded by ffmpeg while the page is visible."""
    interval = 3600.0                  # nothing to poll: ffmpeg is driven by draw()
    fields = [{"name": "source", "type": "string", "default": "", "help": "video file or stream URL (rtsp://, http://, ...)"},
              {"name": "fps", "type": "int", "default": 12, "help": f"frames per second to render (max {MAX_FPS})"},
              {"name": "loop", "type": "bool", "default": True, "help": "restart a file when it ends"},
              FIT, ROUNDED, NAME]

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.fps = clamp_fps(cfg.get("fps"))
        self.stream: FfmpegStream | None = None
        self._stream_key = None

    def _ensure_stream(self) -> FfmpegStream | None:
        source = str(self.cfg.get("source") or "").strip()
        if not source:
            return None
        key = (source, self.cfg.get("fit", "cover"), self.fps, self.cfg.get("loop", True), THEME["card"])
        if key != self._stream_key:                            # config or theme changed: start over
            if self.stream:
                self.stream.stop()
            self.stream = FfmpegStream(source, self.cfg.get("fit", "cover"), self.fps, bool(self.cfg.get("loop", True)),
                                       bg=THEME["card"])
            self._stream_key = key
        return self.stream

    def draw(self, cv, box):
        stream = self._ensure_stream()
        if stream is None:
            placeholder(cv, box, "no source")
            return
        stream.touch((box[2] - box[0], box[3] - box[1]))       # starts ffmpeg; never waits for it
        got = stream.frame()
        if got is None:
            status = stream.status or "loading…"
            placeholder(cv, box, status, color=THEME["crit"] if status in BAD_SOURCE else None)
            return
        paste_tile(cv, got[0], box, self.cfg.get("rounded", True))
        if self.cfg.get("name"):
            pill(cv, (box[0] + 10, box[1] + 10), str(self.cfg["name"]))
        if stream.status == "reconnecting…":
            pill(cv, (box[2] - 10, box[1] + 10), "reconnecting…", color=THEME["warn"], anchor="ra")

    # draw_full: the default calls draw() with the fullscreen box, and touch() re-scales ffmpeg's output to it.
