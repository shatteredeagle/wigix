import math
import time

from . import Widget, widget
from ..render import THEME


@widget("screensaver")
class Screensaver(Widget):
    """Idle-screen art that drifts slowly (so the same pixels never stay lit): the project name and the time.

    Config: text (default "wigix"), style: bounce (drift around the screen) | clock (big dim clock).
    Used by `idle: {screensaver: ...}`; also fine as a normal widget.
    """
    fps = 8
    interval = 3600.0
    fields = [{"name": "text", "type": "string", "default": "wigix"},
              {"name": "style", "type": "select", "options": ["bounce", "clock"], "default": "bounce"}]

    def draw(self, cv, box):
        x0, y0, x1, y1 = box
        cv.d.rectangle((0, 0, cv.image.width, cv.image.height), fill=(0, 0, 0))
        t = time.time()
        w, h = x1 - x0, y1 - y0
        dim = tuple(int(c * 0.55) for c in THEME["accent"])
        if self.cfg.get("style", "bounce") == "clock":
            cv.text((x0 + w // 2, y0 + h // 2), time.strftime("%H:%M"), size=min(200, h // 2), bold=True,
                    color=dim, anchor="mm")
            return
        # a slow Lissajous drift keeps the text inside the box without bounce bookkeeping
        cx = x0 + w // 2 + int(math.sin(t / 7.0) * w * 0.28)
        cy = y0 + h // 2 + int(math.sin(t / 5.0 + 1.3) * h * 0.25)
        cv.text((cx, cy), self.cfg.get("text", "wigix"), size=96, bold=True, color=dim, anchor="mm")
        cv.text((cx, cy + 70), time.strftime("%H:%M"), size=30, color=(70, 76, 90), anchor="mm")
