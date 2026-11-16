import socket
import time

import requests

from . import Widget, widget
from ..render import THEME


@widget("checks")
class Checks(Widget):
    """Service up/down board. Config: checks: [{name, http: url} | {name, tcp: "host:port"}], timeout."""
    fields = [{"name": "title", "type": "string", "default": "Services"}, {"name": "checks", "type": "yaml", "default": [{"name": "Example", "http": "https://example.com"}], "help": "list of {name, http|tcp}"}, {"name": "timeout", "type": "float", "default": 3}]
    interval = 15.0

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.results = [(c["name"], None) for c in cfg.get("checks", [])]

    def update(self):
        out = []
        for c in self.cfg.get("checks", []):
            t = float(self.cfg.get("timeout", 3))
            try:
                if "http" in c:
                    ok = requests.get(c["http"], timeout=t, verify=c.get("verify", True)).status_code < 500
                else:
                    host, port = c["tcp"].rsplit(":", 1)
                    socket.create_connection((host, int(port)), timeout=t).close()
                    ok = True
            except Exception:
                ok = False
            out.append((c["name"], ok))
        self.results = out

    def draw(self, cv, box):
        down = sum(1 for _, ok in self.results if ok is False)
        x0, y0, x1, y1 = cv.card(box, self.title or "Services")
        pending = any(ok is None for _, ok in self.results)
        cv.text((x1, box[1] + 8), "checking" if pending else f"{down} down" if down else "all up", size=18,
                color=THEME["dim"] if pending else THEME["crit"] if down else THEME["ok"], anchor="ra")
        h = 30
        cols = 2 if len(self.results) * h > (y1 - y0) else 1
        per = (y1 - y0) // h
        for i, (name, ok) in enumerate(self.results):
            col, rowi = divmod(i, per)
            if col >= cols:
                break
            x = x0 + col * (x1 - x0) // cols
            y = y0 + rowi * h
            color = THEME["dim"] if ok is None else THEME["ok"] if ok else THEME["crit"]
            cv.d.ellipse((x, y + 6, x + 14, y + 20), fill=color)
            cv.text((x + 24, y), name[:18], size=20, color=THEME["text"] if ok is not False else THEME["crit"])
