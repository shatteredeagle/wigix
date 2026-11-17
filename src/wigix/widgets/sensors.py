# SPDX-License-Identifier: GPL-3.0-only
"""Stacked history graphs for a handful of Home Assistant sensors (an enclosure, a room), in the btop style."""
from __future__ import annotations

from . import widget
from .ha import HAWidget, INTEG
from .system import STYLE
from ..render import THEME, fmt_number

PALETTE = ("accent", "warn", "ok", "crit")          # one colour per graph, cycling


@widget("sensor_graphs")
class SensorGraphs(HAWidget):
    """Each sensor as a labelled graph with its current value; optional battery level in the title.
    ``sensors: [{entity: sensor.frog_temperature, label: Warm}, ...]``."""
    interval = 300.0
    fields = [{"name": "name", "type": "string", "default": ""}, INTEG,
              {"name": "sensors", "type": "yaml", "default": [{"entity": "sensor.example", "label": "Temp"}],
               "help": "list of {entity, label, color (a theme name: accent, ok, warn, crit)}"},
              {"name": "battery", "type": "entity", "default": "", "help": "battery sensor shown in the title (optional)"},
              {"name": "hours", "type": "float", "default": 24, "help": "graph length in hours"}, STYLE]

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.series: dict[str, list] = {}

    def _sensors(self):
        return [s for s in (self.cfg.get("sensors") or []) if isinstance(s, dict) and s.get("entity")]

    def update(self):
        ha = self.ha
        if not ha.connected:
            self.interval = 5.0
            return
        try:
            for s in self._sensors():
                self.series[s["entity"]] = ha.history_series(s["entity"], float(self.cfg.get("hours", 24)))
        except Exception:
            self.interval = 30.0
            raise
        self.interval = 300.0

    def draw(self, cv, box):
        try:
            ha = self.ha
        except KeyError:
            return self.status_card(cv, box, "no HA integration", THEME["crit"])
        if not ha.connected:
            return self.status_card(cv, box, "HA offline" if ha.error else "connecting…", THEME["crit"] if ha.error else None)
        bat = ha.state(self.cfg["battery"]) if self.cfg.get("battery") else None
        right = f"battery {fmt_number(bat['state'], 0)}%" if bat and bat["state"] not in ("unavailable", "unknown") else None
        x0, y0, x1, y1 = cv.frame(box, self.cfg.get("name") or "Sensors", right, self.cfg.get("style", "btop"))
        sensors = self._sensors()
        if not sensors:
            return
        gap = 10
        rh = (y1 - y0 - gap * (len(sensors) - 1)) // len(sensors)
        for i, s in enumerate(sensors):
            st = ha.state(s["entity"])
            attrs = (st or {}).get("attributes") or {}
            unit = attrs.get("unit_of_measurement", "")
            try:
                text = f"{fmt_number(st['state'], 1)}{'' if unit in ('%', '°F', '°C') else ' '}{unit}"
                float(st["state"])
            except (TypeError, ValueError, KeyError):
                text = "n/a"
            label = str(s.get("label") or attrs.get("friendly_name") or s["entity"])
            color = THEME.get(s.get("color") or PALETTE[i % len(PALETTE)], THEME["accent"])
            top = y0 + i * (rh + gap)
            cv.metric((x0, top, x1, top + rh), label, text, self.series.get(s["entity"], []),
                      float(self.cfg.get("hours", 24)) * 3600, color=color, autorange=True)
