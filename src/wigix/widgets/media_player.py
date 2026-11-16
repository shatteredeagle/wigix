# SPDX-License-Identifier: GPL-3.0-only
import time

from . import IntegrationWidget, widget
from ..render import THEME


def mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


@widget("media_player")
class MediaPlayer(IntegrationWidget):
    """Now playing with progress and cover art; tap the left third for previous, the middle for play/pause, the
    right third for next. Needs an ``mpris`` integration (Linux, playerctl)."""
    integration_type = "mpris"
    interval = 3600.0
    fields = [{"name": "name", "type": "string", "default": ""},
              {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one)"},
              {"name": "art", "type": "bool", "default": True, "help": "show cover art when the card is big enough"}]
    _box = None
    _pressed = (0.0, "")

    def on_tap(self, x, y):
        try:
            integ = self.integ
        except KeyError:
            return False
        x0, _, x1, _ = self._box or (0, 0, 1, 1)
        zone = min(2, max(0, int((x - x0) * 3 / max(1, x1 - x0))))
        action = ("previous", "play_pause", "next")[zone]
        self._pressed = (time.time(), action)
        try:
            getattr(integ, action)()
        except Exception as e:                      # e.g. playerctl missing: surface it, do not crash the touch loop
            integ.error = str(e) or type(e).__name__
        return True

    def guard(self, cv, box):
        # Unlike other integrations a missing playerctl is worth spelling out instead of a bare "offline".
        try:
            integ = self.integ
        except KeyError:
            return super().guard(cv, box)
        if integ.data is None and integ.error and "playerctl" in integ.error:
            self.placeholder(cv, box, "playerctl missing", THEME["crit"])
            return None
        return super().guard(cv, box)

    def draw(self, cv, box):
        d = self.guard(cv, box)
        if d is None:
            return
        self._box = box
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name") or (d.get("player") or "Media"))
        playing = d["status"] == "playing"
        if d["status"] == "stopped" and not d.get("title"):
            cv.text(((x0 + x1) // 2, (y0 + y1) // 2), "nothing playing", size=20, color=THEME["dim"], anchor="mm",
                    max_width=x1 - x0)
            return
        integ = self.integ
        art = getattr(integ, "art_image", None) if self.cfg.get("art", True) else None
        tx = x0
        if art is not None and (x1 - x0) > 300:                 # cover on the left only when there is room
            side = min(y1 - y0 - 70, 200)
            thumb = art.resize((side, side))
            cv.image.paste(thumb, (x0, y0))
            tx = x0 + side + 14
        cv.text((tx, y0), d["title"] or "Unknown", size=22, bold=True, max_width=x1 - tx)
        cv.text((tx, y0 + 28), d["artist"], size=17, color=THEME["dim"], max_width=x1 - tx)
        if tx > x0 and d["album"]:
            cv.text((tx, y0 + 52), d["album"], size=15, color=THEME["dim"], max_width=x1 - tx)
        # progress: interpolate between the 1 s polls so the bar moves smoothly while playing
        length = d["length"]
        pos = d["position"] + ((time.time() - integ.updated) if playing and integ.updated else 0)
        pos = min(pos, length) if length else pos
        by = y1 - 54
        cv.bar((tx, by, x1, by + 8), pos / length if length else 0, THEME["accent"])
        cv.text((tx, by + 12), mmss(pos), size=14, color=THEME["dim"])
        if length:
            cv.text((x1, by + 12), mmss(length), size=14, color=THEME["dim"], anchor="ra")
        self._controls(cv, box, (x0, y0, x1, y1), playing)

    def _controls(self, cv, box, inner, playing):
        """Previous / play-pause / next glyphs, centred in the three tap zones."""
        x0, _, x1, y1 = box
        cy, r = inner[3] - 8, 8
        w = (x1 - x0) / 3
        recent, which = self._pressed
        for i, name in enumerate(("previous", "play_pause", "next")):
            cx = int(x0 + w * (i + 0.5))
            col = THEME["accent"] if time.time() - recent < 0.6 and which == name else THEME["text"]
            d = cv.d
            if name == "play_pause":
                if playing:
                    d.rectangle((cx - r + 1, cy - r, cx - 2, cy + r), fill=col)
                    d.rectangle((cx + 2, cy - r, cx + r - 1, cy + r), fill=col)
                else:
                    d.polygon([(cx - r + 2, cy - r), (cx - r + 2, cy + r), (cx + r, cy)], fill=col)
            elif name == "next":
                d.polygon([(cx - r, cy - r), (cx - r, cy + r), (cx + r - 3, cy)], fill=col)
                d.rectangle((cx + r - 2, cy - r, cx + r, cy + r), fill=col)
            else:
                d.polygon([(cx + r, cy - r), (cx + r, cy + r), (cx - r + 3, cy)], fill=col)
                d.rectangle((cx - r, cy - r, cx - r + 2, cy + r), fill=col)
