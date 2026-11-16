# SPDX-License-Identifier: GPL-3.0-only
"""``uptime_kuma``: monitor status board from an ``uptime_kuma`` integration (public status page)."""
from __future__ import annotations

from . import IntegrationWidget, widget
from .http import INTEG, as_float, dot_rows, header_note
from ..render import THEME

# Uptime Kuma heartbeat status codes.
UP, DOWN, PENDING, MAINTENANCE = 1, 0, 2, 3


def status_color(status):
    return {UP: THEME["ok"], DOWN: THEME["crit"], PENDING: THEME["warn"], MAINTENANCE: THEME["accent"]}.get(status, THEME["dim"])


@widget("uptime_kuma")
class UptimeKuma(IntegrationWidget):
    """Uptime Kuma monitors as dots and names with the latest ping. Needs an ``uptime_kuma`` integration."""
    integration_type = "uptime_kuma"
    interval = 3600.0
    fields = [{"name": "title", "type": "string", "default": "Uptime"}, INTEG,
              {"name": "only_problems", "type": "bool", "default": False, "help": "hide monitors that are up"}]

    def draw(self, cv, box):
        mons = self.guard(cv, box)
        if mons is None:
            return
        inner = cv.card(box, self.title or "Uptime")
        x0, y0, x1, y1 = inner
        down = sum(1 for m in mons if m["status"] == DOWN)
        pending = sum(1 for m in mons if m["status"] == PENDING)
        summary = f"{down} down" if down else f"{pending} pending" if pending else "all up" if mons else "none"
        header_note(cv, box, self.title or "Uptime", [summary],
                    THEME["crit"] if down else THEME["warn"] if pending else THEME["ok"] if mons else THEME["dim"])
        total = len(mons)
        if self.cfg.get("only_problems"):
            mons = [m for m in mons if m["status"] != UP]
        if not mons:
            cv.text(((x0 + x1) // 2, (y0 + y1) // 2), "all up" if total else "no monitors", size=20,
                    color=THEME["ok"] if total else THEME["dim"], anchor="mm")
            return
        # Worst first, so problems are never the rows cut off.
        order = {DOWN: 0, PENDING: 1, MAINTENANCE: 2, UP: 3}
        mons = sorted(mons, key=lambda m: order.get(m["status"], 4))
        rows = []
        for m in mons:
            ping = as_float(m.get("ping"))
            rows.append((status_color(m["status"]), m["name"], f"{ping:.0f}ms" if ping is not None else "",
                         THEME["crit"] if m["status"] == DOWN else None))
        dot_rows(cv, inner, rows)
