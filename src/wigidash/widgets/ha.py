import os

import requests

from . import Widget, widget
from ..render import THEME


class HAClient:
    """Minimal Home Assistant REST client. Token from ``token`` / ``token_file`` / $HA_TOKEN."""

    def __init__(self, ctx):
        cfg = ctx.get("home_assistant", {})
        self.url = cfg.get("url", "http://homeassistant.local:8123").rstrip("/")
        token = cfg.get("token") or os.environ.get("HA_TOKEN")
        if not token and cfg.get("token_file"):
            token = open(os.path.expanduser(cfg["token_file"])).read().strip()
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def call_service(self, domain, service, data):
        r = self.s.post(f"{self.url}/api/services/{domain}/{service}", json=data, timeout=5)
        r.raise_for_status()

    def state(self, entity_id):
        r = self.s.get(f"{self.url}/api/states/{entity_id}", timeout=4)
        r.raise_for_status()
        return r.json()


def _client(ctx):
    if "ha_client" not in ctx:
        ctx["ha_client"] = HAClient(ctx)
    return ctx["ha_client"]


@widget("ha_entity")
class HAEntity(Widget):
    """One Home Assistant entity as a big number. Config: entity, name (optional), unit (optional)."""
    interval = 10.0
    state = None

    def update(self):
        self.state = _client(self.ctx).state(self.cfg["entity"])

    def draw(self, cv, box):
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name") or (self.state or {}).get("attributes", {}).get("friendly_name"))
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        if not self.state:
            cv.text((cx, cy), "…" if not self.error else "ERR", size=30, color=THEME["dim"], anchor="mm")
            return
        unit = self.cfg.get("unit", self.state["attributes"].get("unit_of_measurement", ""))
        cv.text((cx, cy), f"{self.state['state']}{unit}", size=44, bold=True, anchor="mm")


@widget("ha_list")
class HAList(Widget):
    """Several entities as rows. Config: entities: [entity_id | {entity, name}]"""
    interval = 10.0

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.rows = []

    def update(self):
        rows = []
        for e in self.cfg["entities"]:
            eid, name = (e, None) if isinstance(e, str) else (e["entity"], e.get("name"))
            s = _client(self.ctx).state(eid)
            rows.append((name or s["attributes"].get("friendly_name", eid),
                         s["state"] + s["attributes"].get("unit_of_measurement", "")))
        self.rows = rows

    def draw(self, cv, box):
        x0, y0, x1, y1 = cv.card(box, self.title)
        h = max(24, min(34, (y1 - y0) // max(1, len(self.rows) or 1)))
        for i, (name, val) in enumerate(self.rows):
            y = y0 + i * h
            cv.text((x0, y), name[:24], size=20, color=THEME["dim"])
            cv.text((x1, y), val, size=20, anchor="ra")
