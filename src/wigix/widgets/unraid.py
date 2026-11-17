# SPDX-License-Identifier: GPL-3.0-only
from . import IntegrationWidget, widget
from .system import STYLE
from ..render import THEME


@widget("unraid")
class Unraid(IntegrationWidget):
    """Unraid server, btop style: CPU and memory graphs (last 10 minutes), array usage and container counts.
    Needs an ``unraid`` integration."""
    integration_type = "unraid"
    interval = 3600.0
    fields = [{"name": "name", "type": "string", "default": "Unraid"},
              {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one)"}, STYLE]
    WINDOW = 600

    def draw(self, cv, box):
        d = self.guard(cv, box)
        if d is None:
            return
        m, arr = d["metrics"], d["array"]
        cap = arr["capacity"]["kilobytes"]
        used, total = float(cap["used"]), float(cap["total"])
        started = arr["state"] == "STARTED"
        cs = d["docker"]["containers"]
        up = sum(1 for c in cs if c["state"] == "RUNNING")
        inner = cv.frame(box, self.cfg.get("name", "Unraid"), f"array {arr['state'].lower()} · {up}/{len(cs)} containers",
                         self.cfg.get("style", "btop"), color=THEME["accent"] if started else THEME["crit"])
        x0, y0, x1, y1 = inner
        integ = self.integ
        gy1 = y1 - 38
        half = y0 + (gy1 - y0) // 2
        cpu, mem = m["cpu"]["percentTotal"] / 100, m["memory"]["percentTotal"] / 100
        cv.metric((x0, y0, x1, half - 8), "CPU", f"{cpu * 100:.0f}%", integ.series("cpu"), self.WINDOW, cpu, 100)
        cv.metric((x0, half, x1, gy1), "MEM", f"{mem * 100:.0f}%", integ.series("mem"), self.WINDOW, mem, 100)
        cv.meter((x0, y1 - 30, x1, y1), "ARRAY", f"{used / 1e9:.1f}/{total / 1e9:.1f} T", used / total if total else 0)
