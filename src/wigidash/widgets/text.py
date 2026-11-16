from . import Widget, widget
from ..render import THEME


@widget("text")
class Text(Widget):
    """Config: text, size (default 28), color (hex)."""
    interval = 3600

    def draw(self, cv, box):
        x0, y0, x1, y1 = cv.card(box, self.title)
        color = self.cfg.get("color")
        color = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) if color else THEME["text"]
        cv.text(((x0 + x1) // 2, (y0 + y1) // 2), self.cfg.get("text", ""),
                size=int(self.cfg.get("size", 28)), color=color, anchor="mm", max_width=x1 - x0)
