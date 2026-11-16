import requests
import urllib3

from . import Widget, widget
from ..render import THEME, level_color, read_secret

QUERY = """{ metrics { cpu { percentTotal } memory { percentTotal used total } }
  array { state capacity { kilobytes { free used total } } }
  docker { containers { state } } }"""


@widget("unraid")
class Unraid(Widget):
    """Unraid server via its GraphQL API (Unraid 7+).

    Config: url (https://host/graphql), api_key / api_key_file / $UNRAID_API_KEY, verify (default false).
    """
    interval = 5.0
    data = None

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        if not cfg.get("verify", False):
            urllib3.disable_warnings()
        self.key = read_secret(cfg.get("api_key"), cfg.get("api_key_file"), "UNRAID_API_KEY")

    def update(self):
        r = requests.post(self.cfg["url"], json={"query": QUERY}, headers={"x-api-key": self.key},
                          verify=self.cfg.get("verify", False), timeout=8)
        r.raise_for_status()
        j = r.json()
        if j.get("errors"):
            raise RuntimeError(j["errors"][0]["message"])
        self.data = j["data"]

    def draw(self, cv, box):
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name", "Unraid"))
        d = self.data
        if not d:
            cv.text(((x0 + x1) // 2, (y0 + y1) // 2), "OFFLINE" if self.error else "…", size=26,
                    color=THEME["crit"] if self.error else THEME["dim"], anchor="mm")
            return
        m, arr = d["metrics"], d["array"]
        cap = arr["capacity"]["kilobytes"]
        used, total = float(cap["used"]), float(cap["total"])
        started = arr["state"] == "STARTED"
        cv.text((x1, box[1] + 8), arr["state"].lower(), size=18,
                color=THEME["ok"] if started else THEME["crit"], anchor="ra")
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
        cv.text((x0, y + 2), f"containers {up}/{len(cs)}", size=18,
                color=THEME["ok"] if up == len(cs) else THEME["warn"])
