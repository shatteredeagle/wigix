import collections
import time

import requests

from . import Widget, widget
from ..render import THEME
from .system import human_rate


def parse_prom(text):
    """Tiny Prometheus text parser: yields (name, labels_str, value)."""
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        try:
            head, val = line.rsplit(" ", 1)
            name, _, labels = head.partition("{")
            yield name, labels.rstrip("}"), float(val)
        except ValueError:
            continue


@widget("node")
class Node(Widget):
    """A remote machine via Prometheus node_exporter. Config: url (e.g. http://nas.lan:9100/metrics), name."""
    fields = [{"name":"name","type":"string","default":""},{"name":"url","type":"string","default":"http://host:9100/metrics","help":"node_exporter metrics URL"}]
    interval = 3.0

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.prev = None
        self.cpu = self.mem = self.disk = self.net_rate = 0.0
        self.load1 = 0.0
        self.net_hist = collections.deque(maxlen=60)

    def update(self):
        r = requests.get(self.cfg["url"], timeout=3)
        r.raise_for_status()
        idle = total = rx = tx = 0.0
        m = {}
        for name, labels, v in parse_prom(r.text):
            if name == "node_cpu_seconds_total":
                total += v
                idle += v if 'mode="idle"' in labels else 0
            elif name in ("node_memory_MemTotal_bytes", "node_memory_MemAvailable_bytes", "node_load1"):
                m[name] = v
            elif name == "node_filesystem_size_bytes" and 'mountpoint="/"' in labels:
                m["fs_size"] = v
            elif name == "node_filesystem_avail_bytes" and 'mountpoint="/"' in labels:
                m["fs_avail"] = v
            elif name == "node_network_receive_bytes_total" and "lo" not in labels:
                rx += v
            elif name == "node_network_transmit_bytes_total" and "lo" not in labels:
                tx += v
        now = time.time()
        if self.prev:
            dt = now - self.prev["t"]
            dtotal = total - self.prev["total"]
            if dtotal > 0:
                self.cpu = 1 - (idle - self.prev["idle"]) / dtotal
            self.net_rate = (rx + tx - self.prev["net"]) / dt
            self.net_hist.append(self.net_rate)
        self.prev = {"t": now, "total": total, "idle": idle, "net": rx + tx}
        if "node_memory_MemTotal_bytes" in m:
            self.mem = 1 - m["node_memory_MemAvailable_bytes"] / m["node_memory_MemTotal_bytes"]
        if m.get("fs_size"):
            self.disk = 1 - m["fs_avail"] / m["fs_size"]
        self.load1 = m.get("node_load1", 0.0)

    def draw(self, cv, box):
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name", self.title))
        if self.error:
            cv.text(((x0 + x1) // 2, (y0 + y1) // 2), "OFFLINE", size=26, color=THEME["crit"], anchor="mm")
            return
        row = (y1 - y0) // 4
        for i, (label, frac) in enumerate((("CPU", self.cpu), ("MEM", self.mem), ("DSK", self.disk))):
            y = y0 + i * row
            cv.text((x0, y), label, size=18, color=THEME["dim"])
            cv.text((x1, y), f"{frac * 100:3.0f}%", size=18, anchor="ra")
            cv.bar((x0, y + 24, x1, y + 36), frac)
        cv.text((x0, y0 + 3 * row), "NET", size=18, color=THEME["dim"])
        cv.text((x1, y0 + 3 * row), human_rate(self.net_rate), size=18, anchor="ra")
        cv.spark((x0, y0 + 3 * row + 24, x1, y1), self.net_hist)
