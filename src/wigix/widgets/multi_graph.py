# SPDX-License-Identifier: GPL-3.0-only
"""Several Home Assistant sensors drawn as coloured lines on shared graphs (e.g. every enclosure's temperature)."""
from __future__ import annotations

from . import widget
from .ha import HAWidget, INTEG
from .system import STYLE
from ..render import THEME, font, fmt_number, parse_color


def _color(spec, i):
    c = parse_color(spec)
    if c:
        return c
    return tuple(THEME[("accent", "ok", "warn", "crit")[i % 4]])


@widget("multi_graph")
class MultiGraph(HAWidget):
    """One graph per group, one coloured line per sensor, with a legend of the current values.

    ``graphs: [{title: Temperature, series: [{entity: sensor.a, label: A, color: "#c084fc", dashed: false}, ...]}]``
    Give a camera widget the same ``border`` colour to tie a line to its picture."""
    interval = 300.0
    fields = [{"name": "name", "type": "string", "default": ""}, INTEG,
              {"name": "graphs", "type": "yaml", "default": [{"title": "Temperature", "series": [
                  {"entity": "sensor.example", "label": "Example", "color": "#4ad682"}]}],
               "help": "list of {title, series: [{entity, label, color (#rrggbb), dashed}]}"},
              {"name": "hours", "type": "float", "default": 24, "help": "graph length in hours"}, STYLE]

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.series: dict[str, list] = {}

    def _graphs(self):
        return [g for g in (self.cfg.get("graphs") or []) if isinstance(g, dict)]

    def _entities(self):
        return [s["entity"] for g in self._graphs() for s in (g.get("series") or []) if isinstance(s, dict) and s.get("entity")]

    def update(self):
        ha = self.ha
        if not ha.connected:
            self.interval = 5.0
            return
        try:
            for e in self._entities():
                self.series[e] = ha.history_series(e, float(self.cfg.get("hours", 24)))
        except Exception:
            self.interval = 30.0
            raise
        self.interval = 300.0

    @staticmethod
    def _legend_width(entries, size):
        return sum(font(size).getlength(t) + 31 for _, _, t in entries)

    def _fit_size(self, entries, avail, size=15):
        while size > 11 and self._legend_width(entries, size) > avail:
            size -= 1
        return size

    def _legend(self, cv, x1, y, entries, size):
        """Right-aligned legend [(colour, dashed, text)] on one row."""
        x = x1
        for color, dashed, text in reversed(entries):
            x -= font(size).getlength(text)
            cv.text((x, y), text, size=size, color=THEME["text"])
            x -= 23
            cv.d.line((x, y + size // 2 + 2, x + 18, y + size // 2 + 2), fill=color, width=3)
            if dashed:
                cv.d.line((x + 6, y + size // 2 + 2, x + 9, y + size // 2 + 2), fill=THEME["card"], width=3)
            x -= 8

    def draw(self, cv, box):
        try:
            ha = self.ha
        except KeyError:
            return self.status_card(cv, box, "no HA integration", THEME["crit"])
        if not ha.connected:
            return self.status_card(cv, box, "HA offline" if ha.error else "connecting…", THEME["crit"] if ha.error else None)
        x0, y0, x1, y1 = cv.frame(box, self.cfg.get("name") or "Sensors", None, self.cfg.get("style", "btop"))
        rows = []                                           # (title, plot series, legend entries, legend on its own row?)
        for g in self._graphs():
            plot, entries = [], []
            for si, s in enumerate(g.get("series") or []):
                if not isinstance(s, dict) or not s.get("entity"):
                    continue
                color, dashed = _color(s.get("color"), si), bool(s.get("dashed"))
                plot.append((self.series.get(s["entity"], []), color, dashed))
                st = ha.state(s["entity"])
                try:
                    float(st["state"])
                    val = fmt_number(st["state"], 1)
                except (TypeError, ValueError, KeyError):
                    val = "n/a"
                entries.append((color, dashed, f"{s.get('label') or s['entity']} {val}"))
            title = str(g.get("title") or "")
            beside = self._legend_width(entries, 13) + font(16).getlength(title) + 20 <= x1 - x0
            rows.append((title, plot, entries, not beside))
        if not rows:
            return
        gap = 10
        head = [24 + (20 if own else 0) for *_, own in rows]
        plot_h = (y1 - y0 - gap * (len(rows) - 1) - sum(head)) // len(rows)
        window = float(self.cfg.get("hours", 24)) * 3600
        size = min(self._fit_size(e, (x1 - x0) if own else x1 - x0 - font(16).getlength(t) - 14) for t, _, e, own in rows)
        top = y0
        for (title, plot, entries, own), h in zip(rows, head):
            cv.text((x0, top), title, size=16, color=THEME["dim"])
            self._legend(cv, x1, top + (22 if own else 1), entries, size)
            cv.lines((x0, top + h, x1, top + h + plot_h), plot, window)
            top += h + plot_h + gap
