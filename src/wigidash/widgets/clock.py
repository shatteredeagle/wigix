import time

from . import Widget, widget
from ..render import THEME


@widget("clock")
class Clock(Widget):
    """Config: format (strftime, default %H:%M), date_format (default %a %b %d)."""
    interval = 1.0

    def draw(self, cv, box):
        x0, y0, x1, y1 = cv.card(box)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        cv.text((cx, cy - 8), time.strftime(self.cfg.get("format", "%H:%M")), size=90, bold=True, anchor="mm", max_width=x1 - x0)
        cv.text((cx, y1 - 10), time.strftime(self.cfg.get("date_format", "%a %b %d")), size=22,
                color=THEME["dim"], anchor="mb")
