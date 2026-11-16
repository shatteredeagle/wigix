# SPDX-License-Identifier: GPL-3.0-only
from . import IntegrationWidget, widget
from ..render import THEME


def _gb(b):
    return f"{b / 2**30:.0f}G"


@widget("proxmox")
class Proxmox(IntegrationWidget):
    """Proxmox node: CPU/memory/disk bars and the guest list. Needs a ``proxmox`` integration."""
    integration_type = "proxmox"
    interval = 3600.0
    fields = [{"name": "name", "type": "string", "default": "", "help": "title (defaults to the node name)"},
              {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one)"}]

    def draw(self, cv, box):
        d = self.guard(cv, box)
        if d is None:
            return
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name") or d["node"])
        n, guests = d["status"], d["guests"]
        cv.text((x1, box[1] + 8), f"up {n['uptime'] // 86400}d", size=18, color=THEME["dim"], anchor="ra")
        rows = (("CPU", n["cpu"], f"{n['cpu'] * 100:.0f}%"),
                ("MEM", n["memory"]["used"] / n["memory"]["total"], f"{_gb(n['memory']['used'])}/{_gb(n['memory']['total'])}"),
                ("DSK", n["rootfs"]["used"] / n["rootfs"]["total"], f"{_gb(n['rootfs']['used'])}/{_gb(n['rootfs']['total'])}"))
        y = y0
        for label, frac, txt in rows:
            cv.text((x0, y), label, size=18, color=THEME["dim"])
            cv.text((x1, y), txt, size=18, anchor="ra")
            cv.bar((x0, y + 24, x1, y + 34), frac)
            y += 46
        running = sum(1 for g in guests if g["status"] == "running")
        cv.text((x0, y + 2), f"guests {running}/{len(guests)}", size=18, color=THEME["dim"])
        y += 28
        for g in guests:
            if y + 22 > y1:
                break
            ok = g["status"] == "running"
            cv.d.ellipse((x0, y + 5, x0 + 10, y + 15), fill=THEME["ok"] if ok else THEME["dim"])
            cv.text((x0 + 18, y), g["name"][:20], size=17, color=THEME["text"] if ok else THEME["dim"])
            y += 22
