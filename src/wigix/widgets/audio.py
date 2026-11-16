"""Audio visualizer: a spectrum of whatever the computer is playing (PipeWire / PulseAudio).

Captures the default output's monitor with ``parec`` (or ``pw-record``) as raw mono s16le, and turns it into
log-spaced bars. The capture only runs while the widget is being drawn.
"""
import logging
import shutil
import subprocess
import threading
import time

import numpy as np

from . import Widget, widget
from ..render import THEME

log = logging.getLogger("wigix.audio")

RATE = 44100
WINDOW = 2048               # samples per FFT (~46 ms)
CHUNK = 1024                # samples read from the capture process at a time
IDLE_STOP = 4.0             # seconds without a draw() before the capture process is stopped
DB_FLOOR = -70.0            # level (dBFS) that maps to an empty bar


def spectrum_bars(samples, bars=32, rate=RATE, fmin=40.0, fmax=16000.0, db_floor=DB_FLOOR) -> np.ndarray:
    """Map audio samples (floats in -1..1, ideally ``WINDOW`` long) to ``bars`` levels in 0..1.

    Hann-windowed FFT; the bars are log-spaced between ``fmin`` and ``fmax`` and each takes the loudest FFT bin
    in its band. A full-scale sine reads 1.0 and ``db_floor`` dBFS or quieter reads 0.0, so silence gives zeros.
    """
    x = np.asarray(samples, dtype=np.float32)
    n = len(x)
    if n < 8 or bars < 1:
        return np.zeros(max(bars, 0))
    mag = np.abs(np.fft.rfft(x * np.hanning(n))) / (n / 4)     # a full-scale sine peaks at 1.0 after Hann
    binw = rate / n
    edges = np.geomspace(fmin, min(fmax, rate / 2), bars + 1)
    out = np.empty(bars)
    for i in range(bars):
        lo = int(edges[i] / binw)
        hi = max(lo + 1, int(np.ceil(edges[i + 1] / binw)))     # always at least one bin, even for narrow bass bands
        out[i] = mag[lo:hi].max()
    db = 20 * np.log10(np.maximum(out, 1e-9))
    return np.clip((db - db_floor) / -db_floor, 0.0, 1.0)


def gradient_color(frac: float):
    """Bar color at height ``frac`` (0 bottom .. 1 top): accent, then ok, warn and crit toward the peak."""
    stops = [(0.0, THEME["accent"]), (0.5, THEME["ok"]), (0.8, THEME["warn"]), (1.0, THEME["crit"])]
    frac = max(0.0, min(1.0, frac))
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if frac <= b:
            t = (frac - a) / (b - a)
            return tuple(int(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))
    return stops[-1][1]


@widget("audio_visualizer")
class AudioVisualizer(Widget):
    """Audio spectrum of the default output (needs parec or pw-record). Options: bars, style bars|wave|line, device."""
    interval = 1.0              # update() only watches for idleness; frames come from the capture thread
    fps = 15
    fields = [{"name": "name", "type": "string", "default": "", "help": "optional card title"},
              {"name": "bars", "type": "int", "default": 32, "help": "number of frequency bars"},
              {"name": "style", "type": "string", "default": "bars", "help": "bars | wave | line"},
              {"name": "device", "type": "string", "default": "", "help": "audio source to capture (default: the default output's monitor)"}]

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.n = max(4, min(128, int(cfg.get("bars", 32))))
        self.levels = np.zeros(self.n)                  # smoothed bar heights, 0..1
        self._buf = np.zeros(WINDOW, dtype=np.float32)  # most recent samples
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._last_draw = 0.0
        self._last_frame = 0.0
        self._last_start = 0.0                          # throttles restarts when the recorder keeps failing
        self._cmd: list[str] | None = None
        self.error: str | None = None

    # --- capture process ------------------------------------------------------------------
    def command(self) -> list[str] | None:
        """The capture command line (resolved once), or None (and ``error`` set) when no recorder is installed."""
        if self._cmd is None:
            self._cmd = self._build_command() or []
        return self._cmd or None

    def _build_command(self) -> list[str] | None:
        dev = str(self.cfg.get("device") or "")
        if shutil.which("parec"):
            return ["parec", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=30",
                    f"--device={dev or '@DEFAULT_MONITOR@'}"]
        if shutil.which("pw-record"):
            # stream.capture.sink makes PipeWire record the sink's monitor rather than a microphone
            tail = ["--target", dev] if dev else ["-P", "stream.capture.sink=true"]
            return ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", *tail, "-"]
        self.error = "parec missing"
        return None

    def _start(self):
        cmd = self.command()
        if cmd is None or time.time() - self._last_start < 2.0:
            return
        self._last_start = time.time()
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        except OSError as e:
            self.error = str(e)
            return
        self.error = None
        self._thread = threading.Thread(target=self._read, args=(self._proc,), daemon=True, name="audio-capture")
        self._thread.start()

    def _read(self, proc):
        """Background thread: keep the sample buffer filled until the process ends or nobody is watching."""
        step = CHUNK * 2                                # bytes per chunk (s16)
        try:
            while True:
                raw = proc.stdout.read(step)
                if not raw:
                    break
                pcm = np.frombuffer(raw[:len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
                with self._lock:
                    self._buf = np.concatenate((self._buf, pcm))[-WINDOW:]
                    self._last_frame = time.time()
                if time.time() - self._last_draw > IDLE_STOP:
                    break
        except (OSError, ValueError):
            pass
        finally:
            self._stop_proc(proc)

    def _stop_proc(self, proc):
        """Terminate and reap a capture process (idempotent)."""
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if self._proc is proc:
            self._proc = None

    def stop(self):
        """Stop capturing now (also used on idle and by tests)."""
        proc = self._proc
        if proc is not None:
            self._stop_proc(proc)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None

    def update(self):
        """Worker thread, once a second: shut the capture down when the widget has not been drawn for a while."""
        if self._proc is not None and time.time() - self._last_draw > IDLE_STOP:
            self.stop()

    # --- drawing --------------------------------------------------------------------------
    def _advance(self):
        """Fold the newest samples into the smoothed levels (fast attack, slow decay)."""
        with self._lock:
            samples = self._buf.copy()
        target = spectrum_bars(samples, self.n)
        rising = target > self.levels
        self.levels = np.where(rising, self.levels + (target - self.levels) * 0.7, self.levels * 0.88)
        self.levels = np.maximum(self.levels, 0.0)

    def draw(self, cv, box):
        self._last_draw = time.time()
        if self._proc is None:
            self._start()
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name") or None)
        if self.error:
            cv.text(((x0 + x1) // 2, (y0 + y1) // 2), self.error, size=20, color=THEME["crit"], anchor="mm", max_width=x1 - x0)
            return
        self._advance()
        style = str(self.cfg.get("style", "bars"))
        draw = {"wave": self._draw_wave, "line": self._draw_line}.get(style, self._draw_bars)
        draw(cv, (x0, y0, x1, y1))

    def _draw_bars(self, cv, area):
        x0, y0, x1, y1 = area
        h, slot = y1 - y0, (x1 - x0) / self.n
        gap = max(1, int(slot * 0.2))
        for i, v in enumerate(self.levels):
            bx0 = int(x0 + i * slot)
            bx1 = max(bx0 + 1, int(x0 + (i + 1) * slot) - gap)
            top = y1 - int(h * v)
            cv.d.rectangle((bx0, y1 - 2, bx1, y1), fill=gradient_color(0))     # a thin baseline stub keeps silence visible
            y = y1
            while y > top:                                                       # vertical gradient in 4 px slices
                ys = max(top, y - 4)
                cv.d.rectangle((bx0, ys, bx1, y), fill=gradient_color((y1 - y) / h))
                y = ys

    def _points(self, area, mirror):
        x0, y0, x1, y1 = area
        h = y1 - y0
        xs = [x0 + (x1 - x0) * i / (self.n - 1) for i in range(self.n)]
        if mirror:
            mid = (y0 + y1) / 2
            return xs, [mid - h / 2 * v for v in self.levels], [mid + h / 2 * v for v in self.levels]
        return xs, [y1 - h * v for v in self.levels], None

    def _draw_wave(self, cv, area):
        """A filled, vertically mirrored envelope of the spectrum."""
        xs, upper, lower = self._points(area, mirror=True)
        peak = float(self.levels.max())
        pts = list(zip(xs, upper)) + list(zip(xs[::-1], lower[::-1]))
        cv.d.polygon(pts, fill=tuple(int(c * 0.45 + b * 0.55) for c, b in zip(THEME["accent"], THEME["card"])))
        cv.d.line(list(zip(xs, upper)), fill=gradient_color(peak), width=2)
        cv.d.line(list(zip(xs, lower)), fill=gradient_color(peak), width=2)

    def _draw_line(self, cv, area):
        """A single polyline through the bar heights."""
        xs, ys, _ = self._points(area, mirror=False)
        cv.d.line(list(zip(xs, ys)), fill=gradient_color(float(self.levels.max())), width=3, joint="curve")

    def draw_full(self, cv, box):
        self.draw(cv, box)
