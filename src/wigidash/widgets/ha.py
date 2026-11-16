"""Home Assistant widgets. They read the shared live state of a ``home_assistant`` integration
(see integrations/home_assistant.py), so there is no per-widget polling.

Common config: ``integration`` (name; optional when there is exactly one HA), ``name`` (label).
Value coloring: ``color_rules: [{above: 80, color: "#f05454"}, {below: 20, color: "#569cff"}]``.
"""
from __future__ import annotations

import time

from . import Widget, widget
from ..render import THEME, fmt_number, parse_color, rel_time

ENTITY = {"name": "entity", "type": "entity", "default": "", "help": "Home Assistant entity id"}
NAME = {"name": "name", "type": "string", "default": "", "help": "label (defaults to the friendly name)"}
INTEG = {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one)"}
RULES = {"name": "color_rules", "type": "yaml", "default": [], "help": 'e.g. [{"above": 80, "color": "#f05454"}]'}


class HAWidget(Widget):
    interval = 3600.0  # state comes over the websocket; nothing to poll

    @property
    def ha(self):
        return self.ctx["integrations"].get(self.cfg.get("integration") or None, "home_assistant")

    def state(self, entity_id=None):
        return self.ha.state(entity_id or self.cfg.get("entity", ""))

    def label(self, s, entity_id=None):
        return self.cfg.get("name") or (s or {}).get("attributes", {}).get("friendly_name") or entity_id or self.cfg.get("entity", "")

    def status_card(self, cv, box, text, color=None):
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name"))
        cv.text(((x0 + x1) // 2, (y0 + y1) // 2), text, size=20, color=color or THEME["dim"], anchor="mm", max_width=x1 - x0)

    def guard(self, cv, box, entity_id=None):
        """Draw a placeholder and return None unless the integration is up and the entity exists."""
        try:
            ha = self.ha
        except KeyError as e:
            self.status_card(cv, box, "no HA integration", THEME["crit"])
            return None
        if not ha.connected:
            self.status_card(cv, box, "HA offline" if ha.error else "connecting…", THEME["crit"] if ha.error else None)
            return None
        s = ha.state(entity_id or self.cfg.get("entity", ""))
        if s is None:
            self.status_card(cv, box, f"unknown: {entity_id or self.cfg.get('entity', '')}", THEME["warn"])
        return s

    def value_color(self, s):
        st = s["state"]
        if st in ("unavailable", "unknown"):
            return THEME["dim"]
        try:
            v = float(st)
            for rule in self.cfg.get("color_rules") or []:
                if ("above" in rule and v > rule["above"]) or ("below" in rule and v < rule["below"]):
                    return parse_color(rule.get("color"), THEME["text"])
        except ValueError:
            c = (self.cfg.get("state_colors") or {}).get(st)
            if c:
                return parse_color(c, THEME["text"])
            if st in ("on", "home", "open", "unlocked"):
                return THEME["ok"]
        return THEME["text"]

    def display(self, s):
        unit = self.cfg.get("unit", s["attributes"].get("unit_of_measurement", ""))
        v = fmt_number(s["state"], self.cfg.get("precision"))
        return v + (f" {unit}" if unit and unit not in ("%", "°C", "°F") else unit)


@widget("ha_entity")
class HAEntity(HAWidget):
    """One entity as a big value with a "changed 3m ago" line. Tap a numeric one to flip to its history graph
    (unless the widget has its own ``on_tap``). ``hours`` sets the graph span."""
    interval = 30.0
    fields = [ENTITY, NAME, INTEG, {"name": "unit", "type": "string", "default": ""},
              {"name": "precision", "type": "int", "default": ""}, {"name": "hours", "type": "float", "default": 24}, RULES]
    _graph = False
    values: list = []

    def on_tap(self, x, y):
        if self.cfg.get("on_tap"):
            return False
        self._graph = not self._graph
        return True

    def update(self):
        if self._graph:
            self.values = self.ha.history(self.cfg["entity"], float(self.cfg.get("hours", 24)))

    def draw(self, cv, box):
        s = self.guard(cv, box)
        if s is None:
            return
        x0, y0, x1, y1 = cv.card(box, self.label(s))
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        if self._graph:
            cv.text((x1, box[1] + 8), self.display(s), size=22, bold=True, color=self.value_color(s), anchor="ra")
            if len(self.values) > 1:
                cv.spark((x0, y0 + 14, x1, y1 - 4), self.values)
            else:
                cv.text((cx, cy), "loading…", size=18, color=THEME["dim"], anchor="mm")
            return
        cv.text((cx, cy - 4), self.display(s), size=54, bold=True, color=self.value_color(s), anchor="mm", max_width=x1 - x0)
        cv.text((cx, y1 - 2), rel_time(s.get("last_changed", "")), size=16, color=THEME["dim"], anchor="mb")


@widget("ha_list")
class HAList(HAWidget):
    """Several entities as rows: name left, value right."""
    fields = [{"name": "title", "type": "string", "default": ""}, INTEG,
              {"name": "entities", "type": "lines", "default": [], "help": "one entity id per line"}, RULES]

    def draw(self, cv, box):
        try:
            if not self.ha.connected:
                return self.status_card(cv, box, "connecting…")
        except KeyError:
            return self.status_card(cv, box, "no HA integration", THEME["crit"])
        x0, y0, x1, y1 = cv.card(box, self.title)
        ents = self.cfg.get("entities", [])
        h = max(24, min(34, (y1 - y0) // max(1, len(ents))))
        for i, e in enumerate(ents):
            eid, name = (e, None) if isinstance(e, str) else (e["entity"], e.get("name"))
            y = y0 + i * h
            if y + h > y1 + 4:
                break
            s = self.state(eid)
            cv.text((x0, y), (name or self.label(s, eid))[:22], size=20, color=THEME["dim"])
            if s is None:
                cv.text((x1, y), "?", size=20, color=THEME["warn"], anchor="ra")
            else:
                cv.text((x1, y), self.display(s), size=20, color=self.value_color(s), anchor="ra")


@widget("ha_toggle")
class HAToggle(HAWidget):
    """Tap to toggle a light, switch, fan, input_boolean, ... Shows brightness for dimmable lights."""
    fields = [ENTITY, NAME, INTEG]

    def on_tap(self, x, y):
        eid = self.cfg["entity"]
        domain = eid.split(".", 1)[0]
        self._pending = time.time()
        self.ha.call_service(domain if domain in ("light", "switch", "fan", "input_boolean", "cover", "lock") else "homeassistant",
                             "toggle", {"entity_id": eid})
        return True

    _pending = 0.0

    def draw(self, cv, box):
        s = self.guard(cv, box)
        if s is None:
            return
        on = s["state"] in ("on", "open", "unlocked")
        x0, y0, x1, y1 = box
        fill = (30, 70, 45) if on else THEME["card"]
        cv.d.rounded_rectangle(box, radius=14, fill=fill)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        cv.text((cx, y0 + 12), self.label(s), size=18, color=THEME["dim"], anchor="ma", max_width=x1 - x0 - 20)
        cv.text((cx, cy), "ON" if on else s["state"].upper(), size=40, bold=True,
                color=THEME["ok"] if on else THEME["dim"], anchor="mm")
        b = s["attributes"].get("brightness")
        if on and b is not None:
            cv.bar((x0 + 20, y1 - 26, x1 - 20, y1 - 16), b / 255, THEME["warn"])
        if time.time() - self._pending < 0.6:
            cv.d.rounded_rectangle(box, radius=14, outline=THEME["accent"], width=3)


@widget("ha_button")
class HAButton(HAWidget):
    """Tap to call a service, e.g. scene.turn_on or script.turn_on with an entity, or any service with data."""
    fields = [{"name": "label", "type": "string", "default": "Run"}, INTEG,
              {"name": "service", "type": "string", "default": "scene.turn_on", "help": "domain.service"},
              ENTITY, {"name": "data", "type": "yaml", "default": {}, "help": "extra service data"}]
    _sent = 0.0
    _failed = False

    def on_tap(self, x, y):
        domain, service = self.cfg["service"].split(".", 1)
        data = dict(self.cfg.get("data") or {})
        if self.cfg.get("entity"):
            data["entity_id"] = self.cfg["entity"]
        try:
            self.ha.call_service(domain, service, data)
            self._failed = False
        except Exception:
            self._failed = True
        self._sent = time.time()
        return True

    def draw(self, cv, box):
        recent = time.time() - self._sent < 1.2
        fill = (120, 40, 40) if recent and self._failed else (30, 70, 45) if recent else THEME["card"]
        cv.d.rounded_rectangle(box, radius=14, fill=fill)
        x0, y0, x1, y1 = box
        txt = ("failed" if self._failed else "done") if recent else self.cfg.get("label", "Run")
        cv.text(((x0 + x1) // 2, (y0 + y1) // 2), txt, size=30, bold=True, anchor="mm", max_width=x1 - x0 - 20)


@widget("ha_graph")
class HAGraph(HAWidget):
    """History sparkline for a numeric entity, with current value and min/max."""
    interval = 300.0
    fields = [ENTITY, NAME, INTEG, {"name": "hours", "type": "float", "default": 24}, {"name": "unit", "type": "string", "default": ""},
              {"name": "precision", "type": "int", "default": ""}, RULES]
    values: list = []

    def update(self):
        self.values = self.ha.history(self.cfg["entity"], float(self.cfg.get("hours", 24)))

    def draw(self, cv, box):
        s = self.guard(cv, box)
        if s is None:
            return
        x0, y0, x1, y1 = cv.card(box, self.label(s))
        cv.text((x1, box[1] + 8), self.display(s), size=22, bold=True, color=self.value_color(s), anchor="ra")
        if len(self.values) > 1:
            gy0 = y0 + 14
            cv.spark((x0, gy0, x1, y1 - 20), self.values)
            cv.text((x0, y1), fmt_number(min(self.values)), size=14, color=THEME["dim"], anchor="lb")
            cv.text((x1, y1), fmt_number(max(self.values)), size=14, color=THEME["dim"], anchor="rb")
        else:
            cv.text(((x0 + x1) // 2, (y0 + y1) // 2), "no history" if not self.error else "history error", size=18, color=THEME["dim"], anchor="mm")


@widget("ha_gauge")
class HAGauge(HAWidget):
    """Arc gauge for a numeric entity between min and max."""
    fields = [ENTITY, NAME, INTEG, {"name": "min", "type": "float", "default": 0}, {"name": "max", "type": "float", "default": 100},
              {"name": "unit", "type": "string", "default": ""}, {"name": "precision", "type": "int", "default": ""}, RULES]

    def draw(self, cv, box):
        s = self.guard(cv, box)
        if s is None:
            return
        x0, y0, x1, y1 = cv.card(box, self.label(s))
        lo, hi = float(self.cfg.get("min", 0)), float(self.cfg.get("max", 100))
        try:
            frac = (float(s["state"]) - lo) / (hi - lo)
        except (ValueError, ZeroDivisionError):
            frac = 0.0
        frac = max(0.0, min(1.0, frac))
        r = min((x1 - x0) // 2, y1 - y0 - 10)
        cx, cy = (x0 + x1) // 2, y1 - 6
        arc = (cx - r, cy - r, cx + r, cy + r)
        cv.d.arc(arc, 180, 360, fill=(14, 16, 22), width=16)
        if frac > 0:
            cv.d.arc(arc, 180, 180 + 180 * frac, fill=self.value_color(s) if self.cfg.get("color_rules") else THEME["accent"], width=16)
        cv.text((cx, cy - r // 3), self.display(s), size=34, bold=True, color=self.value_color(s), anchor="mm", max_width=r * 1.5)


@widget("ha_alerts")
class HAAlerts(HAWidget):
    """Lists entities that are alerting. Explicit ``entities`` and/or a scan of ``domains`` + ``device_classes``
    (default: binary_sensors reporting problem/safety/smoke/gas/moisture/co/heat/cold/tamper)."""
    fields = [{"name": "title", "type": "string", "default": "Alerts"}, INTEG,
              {"name": "entities", "type": "lines", "default": [], "help": "entity ids that alert when state is in alert_states"},
              {"name": "scan", "type": "bool", "default": True, "help": "also scan problem-type binary sensors"},
              {"name": "alert_states", "type": "yaml", "default": ["on"], "help": "states that count as alerting"}]
    PROBLEM = {"problem", "safety", "smoke", "gas", "moisture", "co", "heat", "cold", "tamper", "carbon_monoxide"}

    def alerts(self):
        states = self.cfg.get("alert_states") or ["on"]
        out = {}
        for eid in self.cfg.get("entities", []):
            s = self.state(eid)
            if s and s["state"] in states:
                out[eid] = s
        if self.cfg.get("scan", True):
            for eid, s in list(self.ha.states.items()):
                if eid.startswith("binary_sensor.") and s["state"] == "on" and s["attributes"].get("device_class") in self.PROBLEM:
                    out[eid] = s
        return out

    def draw(self, cv, box):
        try:
            if not self.ha.connected:
                return self.status_card(cv, box, "connecting…")
        except KeyError:
            return self.status_card(cv, box, "no HA integration", THEME["crit"])
        al = self.alerts()
        x0, y0, x1, y1 = cv.card(box, self.title or "Alerts")
        cv.text((x1, box[1] + 8), f"{len(al)} active" if al else "all clear", size=18,
                color=THEME["crit"] if al else THEME["ok"], anchor="ra")
        for i, (eid, s) in enumerate(al.items()):
            y = y0 + i * 30
            if y + 26 > y1 + 4:
                cv.text((x0, y), f"+{len(al) - i} more", size=18, color=THEME["dim"])
                break
            cv.d.ellipse((x0, y + 6, x0 + 14, y + 20), fill=THEME["crit"])
            cv.text((x0 + 24, y), self.label(s, eid)[:24], size=20, color=THEME["crit"])
