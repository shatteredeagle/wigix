# SPDX-License-Identifier: GPL-3.0-only
from . import IntegrationWidget, widget
from .system import STYLE
from ..render import THEME, metric_color


def _gb(b):
    return f"{b / 2**30:.0f}G"


def _short(name, n):
    return name if len(name) <= n else name[:n - 1] + "…"


@widget("proxmox")
class Proxmox(IntegrationWidget):
    """Proxmox node, btop style: CPU and memory graphs (last 10 minutes), disk meter, and the guest list with load.
    Needs a ``proxmox`` integration."""
    integration_type = "proxmox"
    interval = 3600.0
    fields = [{"name": "name", "type": "string", "default": "", "help": "title (defaults to the node name)"},
              {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one)"}, STYLE]
    WINDOW = 600

    def draw(self, cv, box):
        d = self.guard(cv, box)
        if d is None:
            return
        n, guests = d["status"], d["guests"]
        running = [g for g in guests if g["status"] == "running"]
        inner = cv.frame(box, self.cfg.get("name") or d["node"],
                         f"up {n['uptime'] // 86400}d · {len(running)}/{len(guests)} guests", self.cfg.get("style", "btop"))
        x0, y0, x1, y1 = inner
        wide = (x1 - x0) > 330
        lx1 = x0 + int((x1 - x0) * 0.58) if wide else x1          # graphs on the left, guests on the right
        mem = n["memory"]["used"] / n["memory"]["total"]
        dsk = n["rootfs"]["used"] / n["rootfs"]["total"]
        integ = self.integ
        gy1 = y1 - 38
        half = y0 + (gy1 - y0) // 2
        cv.metric((x0, y0, lx1, half - 8), "CPU", f"{n['cpu'] * 100:.0f}%", integ.series("cpu"), self.WINDOW, n["cpu"], 100)
        cv.metric((x0, half, lx1, gy1), "MEM", f"{_gb(n['memory']['used'])}/{_gb(n['memory']['total'])}",
                  integ.series("mem"), self.WINDOW, mem, 100)
        cv.meter((x0, y1 - 30, lx1, y1), "DSK", f"{_gb(n['rootfs']['used'])}/{_gb(n['rootfs']['total'])}", dsk)
        if not wide:
            return
        gx = lx1 + 16
        cv.text((gx, y0), "GUESTS", size=16, color=THEME["dim"])
        y = y0 + 26
        for g in sorted(guests, key=lambda g: (g["status"] != "running", -(g.get("cpu") or 0))):
            if y + 22 > y1:
                break
            ok = g["status"] == "running"
            cv.d.ellipse((gx, y + 6, gx + 9, y + 15), fill=THEME["ok"] if ok else THEME["dim"])
            cv.text((gx + 16, y), _short(g["name"], 11), size=16, color=THEME["text"] if ok else THEME["dim"])
            if ok:
                c = g.get("cpu") or 0
                cv.bar((x1 - 34, y + 6, x1, y + 14), min(1.0, c * 4), metric_color(min(1.0, c * 2)))
            y += 22
