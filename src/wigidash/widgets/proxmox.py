import requests
import urllib3

from . import Widget, widget
from ..render import THEME, read_secret


def _gb(b):
    return f"{b / 2**30:.0f}G"


@widget("proxmox")
class Proxmox(Widget):
    """Proxmox VE node + guests via the API.

    Config: url (https://host:8006), node, token_id (user@realm!name), token / token_file / $PVE_TOKEN,
    verify (default false: PVE ships a self-signed cert). Read-only calls only; PVEAuditor is enough.
    """
    interval = 5.0

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        if not cfg.get("verify", False):
            urllib3.disable_warnings()
        tok = read_secret(cfg.get("token"), cfg.get("token_file"), "PVE_TOKEN")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"PVEAPIToken={cfg['token_id']}={tok}"
        self.s.verify = cfg.get("verify", False)
        self.node = None
        self.guests = []

    def _get(self, path):
        r = self.s.get(self.cfg["url"].rstrip("/") + "/api2/json" + path, timeout=5)
        r.raise_for_status()
        return r.json()["data"]

    def update(self):
        self.node = self._get(f"/nodes/{self.cfg['node']}/status")
        self.guests = sorted(self._get("/cluster/resources?type=vm"), key=lambda g: g["vmid"])

    def draw(self, cv, box):
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name", self.cfg.get("node")))
        if not self.node:
            cv.text(((x0 + x1) // 2, (y0 + y1) // 2), "OFFLINE" if self.error else "…", size=26,
                    color=THEME["crit"] if self.error else THEME["dim"], anchor="mm")
            return
        n = self.node
        up = n["uptime"] // 86400
        cv.text((x1, box[1] + 8), f"up {up}d", size=18, color=THEME["dim"], anchor="ra")
        rows = (("CPU", n["cpu"], f"{n['cpu'] * 100:.0f}%"),
                ("MEM", n["memory"]["used"] / n["memory"]["total"], f"{_gb(n['memory']['used'])}/{_gb(n['memory']['total'])}"),
                ("DSK", n["rootfs"]["used"] / n["rootfs"]["total"], f"{_gb(n['rootfs']['used'])}/{_gb(n['rootfs']['total'])}"))
        y = y0
        for label, frac, txt in rows:
            cv.text((x0, y), label, size=18, color=THEME["dim"])
            cv.text((x1, y), txt, size=18, anchor="ra")
            cv.bar((x0, y + 24, x1, y + 34), frac)
            y += 46
        running = sum(1 for g in self.guests if g["status"] == "running")
        cv.text((x0, y + 2), f"guests {running}/{len(self.guests)}", size=18, color=THEME["dim"])
        y += 28
        for g in self.guests:
            if y + 22 > y1:
                break
            ok = g["status"] == "running"
            cv.d.ellipse((x0, y + 5, x0 + 10, y + 15), fill=THEME["ok"] if ok else THEME["dim"])
            cv.text((x0 + 18, y), g["name"][:20], size=17, color=THEME["text"] if ok else THEME["dim"])
            y += 22
