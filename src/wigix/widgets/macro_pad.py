"""A Stream-Deck-style grid of launcher buttons inside a single widget."""
from __future__ import annotations

import math
import pathlib
import time

from PIL import Image

from . import Widget, widget
from ..render import THEME, parse_color

GAP = 8
FLASH_SECS = 0.25        # how long a pressed button stays highlighted


def _lighten(c, amount=0.35):
    return tuple(int(v + (255 - v) * amount) for v in c)


@widget("macro_pad")
class MacroPad(Widget):
    """A grid of launcher buttons. Each button runs any ``on_tap`` action (pages, fullscreen, commands, key
    presses, typed text, URLs, Home Assistant services, or a list of those).

    Config: ``columns`` (default 4) and ``buttons: [{label, icon, color, on_tap, toggle: {ha_entity: ...}}]``.
    ``toggle`` shows the on/off state of a Home Assistant entity when the integration is connected.
    command/key/type actions need ``security: {allow_commands: true}`` at the top level of the config.
    """
    interval = 3600.0
    fields = [{"name": "columns", "type": "int", "default": 4},
              {"name": "buttons", "type": "yaml", "default": [],
               "help": "list of {label, icon, color, on_tap, toggle: {ha_entity}}"}]

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self._icons: dict = {}            # (path, size) -> RGBA image or None
        self._pressed = (-1, 0.0)         # (button index, time of the tap)

    @property
    def fps(self):                        # redraw quickly just long enough to show the press highlight
        return 10 if time.time() - self._pressed[1] < 2 * FLASH_SECS else None

    # --- layout ------------------------------------------------------------------------------
    def _buttons(self):
        return [b for b in (self.cfg.get("buttons") or []) if isinstance(b, dict)]

    def _cells(self, box, n):
        """Screen rectangles of the n buttons, row by row."""
        cols = max(1, int(self.cfg.get("columns", 4) or 4))
        rows = max(1, math.ceil(n / cols))
        x0, y0, x1, y1 = box
        cw = max(1.0, (x1 - x0 - GAP * (cols - 1)) / cols)
        ch = max(1.0, (y1 - y0 - GAP * (rows - 1)) / rows)
        return [(int(x0 + (i % cols) * (cw + GAP)), int(y0 + (i // cols) * (ch + GAP)),
                 int(x0 + (i % cols) * (cw + GAP) + cw), int(y0 + (i // cols) * (ch + GAP) + ch)) for i in range(n)]

    # --- touch -------------------------------------------------------------------------------
    def on_tap(self, x, y):
        """x, y are screen coordinates; work out which button they fall in using ``self.box``."""
        buttons = self._buttons()
        for i, (cx0, cy0, cx1, cy1) in enumerate(self._cells(self.box, len(buttons))):
            if cx0 <= x <= cx1 and cy0 <= y <= cy1:
                self._pressed = (i, time.time())
                run = self.ctx.get("run_action")
                if run:
                    run(buttons[i].get("on_tap"), self)
                return True
        return False

    # --- drawing -----------------------------------------------------------------------------
    def _icon(self, path, size):
        key = (path, size)
        if key not in self._icons:
            try:
                im = Image.open(pathlib.Path(path).expanduser()).convert("RGBA")
                im.thumbnail((size, size))
                self._icons[key] = im
            except (OSError, ValueError):
                self._icons[key] = None
        return self._icons[key]

    def _toggle_state(self, btn):
        """True/False from the Home Assistant entity in ``toggle``, or None when unknown or unavailable."""
        entity = (btn.get("toggle") or {}).get("ha_entity")
        if not entity:
            return None
        try:
            ha = self.ctx["integrations"].get((btn["toggle"].get("integration")), "home_assistant")
            s = ha.state(entity) if ha.connected else None
        except (KeyError, AttributeError):
            return None
        return None if s is None else s["state"] in ("on", "open", "unlocked", "playing", "home")

    def draw(self, cv, box):
        buttons = self._buttons()
        if not buttons:
            cv.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), "no buttons configured", size=18,
                    color=THEME["dim"], anchor="mm", max_width=box[2] - box[0] - 16)
            return
        flash = time.time() - self._pressed[1] < FLASH_SECS
        for i, (btn, cell) in enumerate(zip(buttons, self._cells(box, len(buttons)))):
            x0, y0, x1, y1 = cell
            base = parse_color(btn.get("color"), THEME["card"])
            pressed = flash and i == self._pressed[0]
            cv.d.rounded_rectangle(cell, radius=12, fill=_lighten(base, 0.45) if pressed else base)
            state = self._toggle_state(btn)
            if state is not None:
                cv.d.rounded_rectangle(cell, radius=12, outline=THEME["ok"] if state else THEME["dim"], width=3)
                cv.d.ellipse((x1 - 20, y0 + 8, x1 - 8, y0 + 20), fill=THEME["ok"] if state else THEME["dim"])
            label = str(btn.get("label", ""))
            w, h = x1 - x0, y1 - y0
            cx = (x0 + x1) // 2
            icon = self._icon(btn["icon"], max(8, int(min(w, h) * (0.5 if label else 0.7)))) if btn.get("icon") else None
            fsize = max(12, min(26, h // 5))
            if icon:
                top = y0 + (h - icon.height - (fsize + 6 if label else 0)) // 2
                cv.image.paste(icon, (cx - icon.width // 2, max(y0, top)), icon)
                if label:
                    cv.text((cx, y1 - 8), label, size=fsize, anchor="mb", max_width=w - 12)
            elif label:
                cv.text((cx, (y0 + y1) // 2), label, size=fsize + 4, bold=True, anchor="mm", max_width=w - 12)
