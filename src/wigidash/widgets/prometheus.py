"""Prometheus widgets (``prom_value``, ``prom_graph``) and Alertmanager's ``alertmanager_alerts``.

``query`` / ``range`` are the *names* of entries under ``queries:`` / ``ranges:`` in the integration config,
not PromQL; the PromQL lives in one place (the config) and widgets just pick a result by name.
"""
from __future__ import annotations

from . import IntegrationWidget, widget
from .http import INTEG, NAME, PRECISION, RULES, UNIT, as_float, dot_rows, draw_big_value, format_value, header_note, rule_color
from ..render import THEME, fmt_number


@widget("prom_value")
class PromValue(IntegrationWidget):
    """One Prometheus query as a big value with a unit and color rules. Needs a ``prometheus`` integration."""
    integration_type = "prometheus"
    interval = 3600.0
    fields = [{"name": "query", "type": "string", "default": "", "help": "name of an entry under queries:"},
              NAME, INTEG, UNIT, PRECISION, RULES]

    def draw(self, cv, box):
        d = self.guard(cv, box)
        if d is None:
            return
        q = self.cfg.get("query", "")
        v = as_float(d.get(q))
        draw_big_value(cv, box, self.cfg.get("name") or q, format_value(v, self.cfg.get("unit", ""), self.cfg.get("precision")),
                       THEME["dim"] if v is None else rule_color(self.cfg, v))


@widget("prom_graph")
class PromGraph(IntegrationWidget):
    """Sparkline of a Prometheus range query with the current value and min/max. Needs a ``prometheus`` integration."""
    integration_type = "prometheus"
    interval = 3600.0
    fields = [{"name": "range", "type": "string", "default": "", "help": "name of an entry under ranges:"},
              NAME, INTEG, UNIT, PRECISION, RULES]

    def draw(self, cv, box):
        d = self.guard(cv, box)
        if d is None:
            return
        r = self.cfg.get("range", "")
        vals = [v for v in (d.get(r) or []) if as_float(v) is not None]
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name") or r)
        if len(vals) < 2:
            cv.text(((x0 + x1) // 2, (y0 + y1) // 2), "no data", size=18, color=THEME["dim"], anchor="mm")
            return
        unit, prec = self.cfg.get("unit", ""), self.cfg.get("precision")
        cv.text((x1, box[1] + 8), format_value(vals[-1], unit, prec), size=22, bold=True,
                color=rule_color(self.cfg, vals[-1]), anchor="ra")
        lo, hi = min(vals), max(vals)
        # Plot relative to the minimum so a small wobble around 40 fills the graph instead of a flat line.
        span = hi - lo
        cv.spark((x0, y0 + 14, x1, y1 - 20), [(v - lo) / span if span else 0.5 for v in vals], vmax=1)
        cv.text((x0, y1), fmt_number(lo, prec), size=14, color=THEME["dim"], anchor="lb")
        cv.text((x1, y1), fmt_number(hi, prec), size=14, color=THEME["dim"], anchor="rb")


@widget("alertmanager_alerts")
class AlertmanagerAlerts(IntegrationWidget):
    """Firing Alertmanager alerts, most severe first, colored by severity. Needs an ``alertmanager`` integration."""
    integration_type = "alertmanager"
    interval = 3600.0
    fields = [{"name": "title", "type": "string", "default": "Alerts"}, INTEG]

    @staticmethod
    def color(severity):
        return THEME["crit"] if severity in ("critical", "error") else THEME["warn"] if severity == "warning" else THEME["accent"]

    def draw(self, cv, box):
        alerts = self.guard(cv, box)
        if alerts is None:
            return
        inner = cv.card(box, self.title or "Alerts")
        x0, y0, x1, y1 = inner
        worst = self.color(alerts[0].get("severity")) if alerts else THEME["ok"]
        header_note(cv, box, self.title or "Alerts", [f"{len(alerts)} firing"] if alerts else ["all clear"], worst)
        if not alerts:
            return
        dot_rows(cv, inner, [(self.color(a.get("severity")), a.get("name") or "alert", a.get("severity", ""),
                              self.color(a.get("severity"))) for a in alerts], row_h=30, size=20)
