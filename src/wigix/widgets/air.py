# SPDX-License-Identifier: GPL-3.0-only
"""Room air readout in the btop style: a radon graph with its weekly average, plus temperature, humidity and
pressure graphs. Reads a ``home_assistant`` integration (history is fetched every few minutes)."""
from __future__ import annotations

import time

from . import widget
from .ha import HAWidget, INTEG
from .system import STYLE
from ..render import THEME, fmt_number

ENT = lambda name, help: {"name": name, "type": "entity", "default": "", "help": help}   # noqa: E731
LEVELS = {"pCi/L": (2.7, 4.0), "Bq/m³": (100.0, 148.0)}       # WHO / EPA action levels


@widget("air")
class Air(HAWidget):
    """Radon (graph + weekly average line) and room climate graphs from Home Assistant sensors."""
    interval = 300.0
    fields = [{"name": "name", "type": "string", "default": "Basement air"}, INTEG,
              ENT("radon", "radon concentration sensor"), ENT("radon_avg", "its 7-day average sensor (drawn as a dashed line)"),
              ENT("temperature", "temperature sensor"), ENT("humidity", "humidity sensor"), ENT("pressure", "pressure sensor"),
              {"name": "hours", "type": "float", "default": 168, "help": "radon graph length in hours (168 = a week)"},
              {"name": "env_hours", "type": "float", "default": 24, "help": "climate graphs length in hours"},
              {"name": "warn", "type": "float", "default": "", "help": "warning level (default 2.7 pCi/L or 100 Bq/m³)"},
              {"name": "crit", "type": "float", "default": "", "help": "high level (default 4 pCi/L or 148 Bq/m³)"}, STYLE]
    ENV = (("temperature", "TEMP"), ("humidity", "HUM"), ("pressure", "PRESS"))

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.series: dict[str, list] = {}

    def _keys(self):
        return [k for k in ("radon", "temperature", "humidity", "pressure") if self.cfg.get(k)]

    def update(self):
        ha = self.ha
        if not ha.connected:
            return
        for k in self._keys():
            hours = float(self.cfg.get("hours", 168)) if k == "radon" else float(self.cfg.get("env_hours", 24))
            self.series[k] = ha.history_series(self.cfg[k], hours)

    def _read(self, key):
        """(state dict, float value or None, unit) for a configured sensor."""
        s = self.ha.state(self.cfg[key]) if self.cfg.get(key) else None
        try:
            v = float(s["state"]) if s else None
        except (ValueError, KeyError):
            v = None
        return s, v, ((s or {}).get("attributes") or {}).get("unit_of_measurement", "")

    def draw(self, cv, box):
        try:
            ha = self.ha
        except KeyError:
            return self.status_card(cv, box, "no HA integration", THEME["crit"])
        style = self.cfg.get("style", "btop")
        if not ha.connected:
            return self.status_card(cv, box, "HA offline" if ha.error else "connecting…", THEME["crit"] if ha.error else None)
        _, rad, unit = self._read("radon")
        _, avg, _ = self._read("radon_avg")
        right = f"7-day avg {fmt_number(avg, 2)} {unit}".strip() if avg is not None else None
        inner = cv.frame(box, self.cfg.get("name", "Basement air"), right, style)
        x0, y0, x1, y1 = inner
        warn, crit = LEVELS.get(unit, (None, None))
        warn, crit = self.cfg.get("warn") or warn, self.cfg.get("crit") or crit
        hours = float(self.cfg.get("hours", 168))
        lower = [k for k, _ in self.ENV if self.cfg.get(k)]
        split = y0 + int((y1 - y0) * (0.56 if lower else 1.0))
        if self.cfg.get("radon"):
            level = THEME["ok"] if rad is None or warn is None or rad < warn else THEME["warn"] if rad < crit else THEME["crit"]
            lines = [(v, c) for v, c in ((avg, THEME["accent"]), (warn, THEME["warn"]), (crit, THEME["crit"])) if v is not None]
            cv.metric((x0, y0, x1, split - (8 if lower else 0)), "RADON",
                      f"{fmt_number(rad, 2)} {unit}" if rad is not None else "n/a", self.series.get("radon", []),
                      hours * 3600, vmax=max(crit * 1.15, max((v for _, v in self.series.get("radon", [])), default=0) * 1.1)
                      if crit else None, color=level, lines=lines)
        if not lower:
            return
        gap = 12
        cw = (x1 - x0 - gap * (len(lower) - 1)) // len(lower)
        labels = dict(self.ENV)
        for i, k in enumerate(lower):
            _, v, u = self._read(k)
            text = "n/a" if v is None else f"{fmt_number(v, 1 if k != 'pressure' else 2)}{'' if u in ('%', '°F', '°C') else ' '}{u}"
            bx = x0 + i * (cw + gap)
            cv.metric((bx, split, bx + cw, y1), labels[k], text, self.series.get(k, []),
                      float(self.cfg.get("env_hours", 24)) * 3600, color=THEME["accent"], autorange=True)
