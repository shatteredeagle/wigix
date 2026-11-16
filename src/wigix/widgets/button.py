import pathlib

from PIL import Image

from . import Widget, widget
from ..render import THEME, parse_color


@widget("button")
class Button(Widget):
    """A launcher tile: icon image and/or label that runs actions on tap (pages, fullscreen, commands, URLs,
    Home Assistant services). Set the actions with the usual ``on_tap`` (a single action or a list).

    Config: label, icon (path to a PNG/JPG/SVG-less image), color (hex background), on_tap.
    Commands need ``security: {allow_commands: true}`` at the top level of the config.
    """
    interval = 3600.0
    fields = [{"name": "label", "type": "string", "default": "Button"},
              {"name": "icon", "type": "string", "default": "", "help": "path to a PNG/JPG icon"},
              {"name": "color", "type": "color", "default": "#1c2028", "help": "background"}]
    _icon = None
    _icon_key = None

    def _load_icon(self, size):
        path = self.cfg.get("icon")
        key = (path, size)
        if key != self._icon_key:
            self._icon_key, self._icon = key, None
            if path:
                try:
                    im = Image.open(pathlib.Path(path).expanduser()).convert("RGBA")
                    im.thumbnail((size, size))
                    self._icon = im
                except OSError:
                    self._icon = None
        return self._icon

    def draw(self, cv, box):
        x0, y0, x1, y1 = box
        cv.d.rounded_rectangle(box, radius=14, fill=parse_color(self.cfg.get("color"), THEME["card"]))
        label = self.cfg.get("label", "")
        icon = self._load_icon(int(min(x1 - x0, y1 - y0) * (0.55 if label else 0.7)))
        cx = (x0 + x1) // 2
        if icon:
            iy = y0 + ((y1 - y0 - icon.height - (34 if label else 0)) // 2) + 6
            cv.image.paste(icon, (cx - icon.width // 2, iy), icon)
            if label:
                cv.text((cx, y1 - 14), label, size=20, anchor="mb", max_width=x1 - x0 - 16)
        elif label:
            cv.text((cx, (y0 + y1) // 2), label, size=28, bold=True, anchor="mm", max_width=x1 - x0 - 16)
