# SPDX-License-Identifier: GPL-3.0-only
import collections
import socket
import time

import psutil

from . import Widget, widget
from .. import gpu as gpu_mod
from ..render import THEME

STYLE = {"name": "style", "type": "select", "options": ["btop", "card"], "default": "btop",
         "help": "btop = outlined panel with the title in the border; card = filled card"}


def human_rate(b):
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if b < 1000:
            return f"{b:.0f} {unit}"
        b /= 1000
    return f"{b:.1f} TB/s"


def draw_machine(cv, inner, cpu, mem=None, net=None, disk=None, window=120, gpu=None):
    """The shared btop-style machine layout: a big CPU graph (with the GPU graph beside it when there is one), then
    memory and network graphs side by side and a slim disk meter. Each argument is a dict: ``series`` [(t, v)],
    ``text`` (value shown), ``frac`` (0..1 load) and an optional ``sub`` line. Missing parts are skipped and the
    others share the space."""
    x0, y0, x1, y1 = inner
    if disk:
        cv.meter((x0, y1 - 30, x1, y1), disk["label"], disk["text"], disk["frac"])
        y1 -= 38
    lower = [m for m in (mem, net) if m]
    split = y0 + int((y1 - y0) * (0.5 if lower else 1.0))
    top = [m for m in (cpu, gpu) if m]
    gap = 14
    cw = (x1 - x0 - gap * (len(top) - 1)) // len(top)
    for i, c in enumerate(top):
        bx = x0 + i * (cw + gap)
        cv.metric((bx, y0, bx + cw, split - (8 if lower else 0)), c.get("label", "CPU"), c["text"], c["series"], window,
                  frac=c["frac"], vmax=100)
        if c.get("sub"):
            cv.text((bx + cw // 2, y0 + 2), c["sub"], size=14, color=THEME["dim"], anchor="ma")
    if not lower:
        return
    cw = (x1 - x0 - gap * (len(lower) - 1)) // len(lower)
    for i, m in enumerate(lower):
        bx = x0 + i * (cw + gap)
        top = max((v for _, v in m["series"]), default=0)
        cv.metric((bx, split, bx + cw, y1), m["label"], m["text"], m["series"], m.get("window", window),
                  frac=m.get("frac"), vmax=100 if m.get("frac") is not None else max(top * 1.2, 1e5))


@widget("system")
class System(Widget):
    """btop-style panel for THIS machine: CPU, memory and network graphs plus a disk meter."""
    fields = [{"name": "title", "type": "string", "default": "", "help": "defaults to the hostname"},
              {"name": "disk", "type": "string", "default": "/", "help": "mount point for the disk meter"},
              {"name": "gpu", "type": "bool", "default": True, "help": "show a GPU graph when one is found (AMD or NVIDIA)"}, STYLE]
    interval = 1.0
    WINDOW = 120

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.cpu_hist = collections.deque(maxlen=self.WINDOW + 5)
        self.mem_hist = collections.deque(maxlen=self.WINDOW + 5)
        self.net_hist = collections.deque(maxlen=self.WINDOW + 5)
        self.last_net, self.last_t = psutil.net_io_counters(), time.time()
        self.cpu = self.mem = self.disk = 0.0
        self.net_rate = 0.0
        self.load = 0.0
        self.gpu_hist = collections.deque(maxlen=self.WINDOW + 5)
        self.gpu = None

    def update(self):
        now = time.time()
        self.cpu = psutil.cpu_percent() / 100
        self.mem = psutil.virtual_memory().percent / 100
        self.disk = psutil.disk_usage(self.cfg.get("disk", "/")).percent / 100
        n = psutil.net_io_counters()
        dt = max(now - self.last_t, 0.1)
        self.net_rate = (n.bytes_recv + n.bytes_sent - self.last_net.bytes_recv - self.last_net.bytes_sent) / dt
        self.last_net, self.last_t = n, now
        self.load = psutil.getloadavg()[0]
        self.cpu_hist.append((now, self.cpu * 100))
        self.mem_hist.append((now, self.mem * 100))
        self.net_hist.append((now, self.net_rate))
        self.gpu = gpu_mod.read_gpu() if self.cfg.get("gpu", True) else None
        if self.gpu and self.gpu["util"] is not None:
            self.gpu_hist.append((now, self.gpu["util"]))

    def gpu_part(self):
        g = self.gpu
        if not g or g["util"] is None:
            return None
        return {"label": "GPU", "text": f"{g['util']:.0f}%", "frac": g["util"] / 100, "series": list(self.gpu_hist),
                "sub": gpu_mod.summary(g)}

    def draw(self, cv, box):
        inner = cv.frame(box, self.title or socket.gethostname(), f"load {self.load:.2f}", self.cfg.get("style", "btop"))
        draw_machine(cv, inner,
                     {"text": f"{self.cpu * 100:.0f}%", "frac": self.cpu, "series": list(self.cpu_hist)},
                     {"label": "MEM", "text": f"{self.mem * 100:.0f}%", "frac": self.mem, "series": list(self.mem_hist)},
                     {"label": "NET", "text": human_rate(self.net_rate), "series": list(self.net_hist)},
                     {"label": "DSK", "text": f"{self.disk * 100:.0f}%", "frac": self.disk}, self.WINDOW, gpu=self.gpu_part())
