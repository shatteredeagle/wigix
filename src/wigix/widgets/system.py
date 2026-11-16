# SPDX-License-Identifier: GPL-3.0-only
import collections
import socket

import psutil

from . import Widget, widget
from ..render import THEME, level_color


@widget("system")
class System(Widget):
    """btop-style stats for THIS machine: CPU, memory, disk, network. Config: disk (path, default /)."""
    fields = [{"name":"title","type":"string","default":"","help":"defaults to the hostname"},{"name":"disk","type":"string","default":"/","help":"mount point for the DSK bar"}]
    interval = 1.0

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.cpu_hist = collections.deque(maxlen=60)
        self.net_hist = collections.deque(maxlen=60)
        self.last_net = psutil.net_io_counters()
        self.cpu = self.mem = self.disk = 0.0
        self.net_rate = 0.0

    def update(self):
        self.cpu = psutil.cpu_percent() / 100
        self.mem = psutil.virtual_memory().percent / 100
        self.disk = psutil.disk_usage(self.cfg.get("disk", "/")).percent / 100
        n = psutil.net_io_counters()
        self.net_rate = (n.bytes_recv + n.bytes_sent - self.last_net.bytes_recv - self.last_net.bytes_sent) / self.interval
        self.last_net = n
        self.cpu_hist.append(self.cpu * 100)
        self.net_hist.append(self.net_rate)

    def draw(self, cv, box):
        x0, y0, x1, y1 = cv.card(box, self.title or socket.gethostname())
        row = (y1 - y0) // 4
        for i, (label, frac) in enumerate((("CPU", self.cpu), ("MEM", self.mem), ("DSK", self.disk))):
            y = y0 + i * row
            cv.text((x0, y), label, size=18, color=THEME["dim"])
            cv.text((x1, y), f"{frac * 100:3.0f}%", size=18, anchor="ra")
            cv.bar((x0, y + 24, x1, y + 36), frac)
        cv.text((x0, y0 + 3 * row), "NET", size=18, color=THEME["dim"])
        cv.text((x1, y0 + 3 * row), human_rate(self.net_rate), size=18, anchor="ra")
        cv.spark((x0, y0 + 3 * row + 24, x1, y1), self.net_hist)


def human_rate(b):
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if b < 1000:
            return f"{b:.0f} {unit}"
        b /= 1000
    return f"{b:.1f} TB/s"
