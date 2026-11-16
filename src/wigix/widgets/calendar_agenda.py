# SPDX-License-Identifier: GPL-3.0-only
import datetime as dt

from . import IntegrationWidget, widget
from ..render import THEME


def _now(tz) -> dt.datetime:
    return dt.datetime.now(tz)


def day_label(day: dt.date, today: dt.date) -> str:
    """Today / Tomorrow / Mon (within the next week) / Nov 24."""
    delta = (day - today).days
    if delta <= 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return day.strftime("%a") if delta < 7 else day.strftime("%b %d").replace(" 0", " ")


@widget("calendar_agenda")
class CalendarAgenda(IntegrationWidget):
    """The next few calendar events with relative day labels. Needs an ``ical`` integration."""
    integration_type = "ical"
    interval = 3600.0
    fields = [{"name": "name", "type": "string", "default": "Agenda"},
              {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one)"},
              {"name": "max_events", "type": "int", "default": 4},
              {"name": "time_format", "type": "string", "default": "%H:%M", "help": "strftime, e.g. %I:%M %p"}]

    def _when(self, ev, today, fmt) -> str:
        start = ev["start"]
        label = day_label(max(start.date(), today), today)       # a multi-day event that began earlier counts as today
        return f"{label} all day" if ev["all_day"] else f"{label} {start.strftime(fmt)}"

    def draw(self, cv, box):
        events = self.guard(cv, box)
        if events is None:
            return
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name", "Agenda"))
        if not events:
            cv.text(((x0 + x1) // 2, (y0 + y1) // 2), "nothing planned", size=20, color=THEME["dim"], anchor="mm",
                    max_width=x1 - x0)
            return
        tz = getattr(self.integ, "tz", None)
        today = _now(tz).date()
        fmt = self.cfg.get("time_format", "%H:%M")
        wide = (x1 - x0) > 600                                   # only a full-width strip has room for one line each
        row_h = 30 if wide else 46                                # wide: one line per event; narrow: when + title
        shown = max(1, min(int(self.cfg.get("max_events", 4)), (y1 - y0 + 4) // row_h))
        y = y0
        for ev in events[:shown]:
            when = self._when(ev, today, fmt)
            if wide:
                cv.text((x0, y), when, size=18, color=THEME["accent"])
                cv.text((x0 + 200, y), ev["title"], size=20, max_width=x1 - x0 - 200)
            else:
                cv.text((x0, y), when, size=15, color=THEME["accent"], max_width=x1 - x0)
                cv.text((x0, y + 18), ev["title"], size=19, max_width=x1 - x0)
            y += row_h
        if len(events) > shown:
            cv.text((x1, box[1] + 10), f"+{len(events) - shown} more", size=14, color=THEME["dim"], anchor="ra")
