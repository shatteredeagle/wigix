# SPDX-License-Identifier: GPL-3.0-only
from . import IntegrationWidget, widget
from ..render import THEME


@widget("unraid")
class Unraid(IntegrationWidget):
    """Unraid server: CPU, memory, array usage, container counts. Needs an ``unraid`` integration."""
    integration_type = "unraid"
    interval = 3600.0
    fields = [{"name": "name", "type": "string", "default": "Unraid"},
              {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one)"}]

    def draw(self, cv, box):
        d = self.guard(cv, box)
        if d is None:
            return
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name", "Unraid"))
        m, arr = d["metrics"], d["array"]
        cap = arr["capacity"]["kilobytes"]
        used, total = float(cap["used"]), float(cap["total"])
        started = arr["state"] == "STARTED"
        cv.text((x1, box[1] + 8), arr["state"].lower(), size=18, color=THEME["ok"] if started else THEME["crit"], anchor="ra")
        rows = (("CPU", m["cpu"]["percentTotal"] / 100, f"{m['cpu']['percentTotal']:.0f}%"),
                ("MEM", m["memory"]["percentTotal"] / 100, f"{m['memory']['percentTotal']:.0f}%"),
                ("ARR", used / total if total else 0, f"{used / 1e9:.1f}/{total / 1e9:.1f}T"))
        y = y0
        for label, frac, txt in rows:
            cv.text((x0, y), label, size=18, color=THEME["dim"])
            cv.text((x1, y), txt, size=18, anchor="ra")
            cv.bar((x0, y + 24, x1, y + 34), frac)
            y += 46
        cs = d["docker"]["containers"]
        up = sum(1 for c in cs if c["state"] == "RUNNING")
        cv.text((x0, y + 2), f"containers {up}/{len(cs)}", size=18, color=THEME["ok"] if up == len(cs) else THEME["warn"])
