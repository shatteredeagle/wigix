import threading
import time

from . import IntegrationWidget, widget
from ..render import THEME, font


def short(n) -> str:
    """12345 -> '12.3k', 2_500_000 -> '2.5M'; small numbers stay whole."""
    n = float(n)
    if abs(n) >= 1e6:
        return f"{n / 1e6:.1f}M"
    if abs(n) >= 1e4:
        return f"{n / 1e3:.1f}k"
    return f"{n:.0f}"


@widget("dns_stats")
class DnsStats(IntegrationWidget):
    """DNS ad blocker: queries today, blocked count and percentage, on/off state. Needs a ``pihole`` or ``adguard``
    integration. With ``tap_toggle: true`` a tap switches Pi-hole v6 blocking on/off."""
    interval = 3600.0
    fields = [{"name": "name", "type": "string", "default": "", "help": "title (defaults to Pi-hole / AdGuard)"},
              {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one Pi-hole/AdGuard)"},
              {"name": "tap_toggle", "type": "bool", "default": False, "help": "tap switches blocking on/off (Pi-hole v6 only)"}]
    _pending = 0.0

    @property
    def integ(self):
        # Two integration types can feed this widget, so the base class lookup by one type does not fit.
        reg = self.ctx["integrations"]
        name = self.cfg.get("integration") or None
        if name:
            return reg.get(name, "pihole")
        found = [i for i in reg.items.values() if i.type_name in ("pihole", "adguard")]
        if len(found) != 1:
            raise KeyError(f"expected exactly one pihole/adguard integration, found {len(found)}; set `integration:`")
        return found[0]

    def on_tap(self, x, y):
        if not self.cfg.get("tap_toggle"):
            return False
        try:
            integ = self.integ
        except KeyError:
            return False
        if not getattr(integ, "can_toggle", False) or integ.data is None:
            return False
        self._pending = time.time()
        # The HTTP calls take a moment: keep the touch thread free.
        threading.Thread(target=self._toggle, args=(integ, not integ.data["enabled"]), daemon=True).start()
        return True

    def _toggle(self, integ, enabled):
        try:
            integ.set_blocking(enabled)
        except Exception as e:
            integ.error = f"toggle failed: {e}"

    def draw(self, cv, box):
        d = self.guard(cv, box)
        if d is None:
            return
        title = self.cfg.get("name") or ("AdGuard" if d.get("kind") == "adguard" else "Pi-hole")
        x0, y0, x1, y1 = cv.card(box, title)
        on = d.get("enabled", True)
        # state dot + word in the title row
        label = "blocking" if on else "paused"
        cv.text((x1, box[1] + 8), label, size=16, color=THEME["ok"] if on else THEME["warn"], anchor="ra")
        dot_x = x1 - font(16).getlength(label) - 16
        cv.d.ellipse((dot_x - 5, box[1] + 14, dot_x + 5, box[1] + 24), fill=THEME["ok"] if on else THEME["warn"])
        queries, blocked = d.get("queries", 0), d.get("blocked", 0)
        pct = d.get("percent_blocked", 0.0)
        big = (y1 - y0) > 170
        cv.text((x0, y0), short(queries), size=44 if big else 34, bold=True, max_width=x1 - x0)
        cv.text((x0, y0 + (52 if big else 40)), "queries", size=16, color=THEME["dim"])
        y = y0 + (84 if big else 66)
        cv.text((x0, y), f"{short(blocked)} blocked", size=20 if big else 17, color=THEME["text"] if on else THEME["dim"],
                max_width=(x1 - x0) * 0.62)
        cv.text((x1, y), f"{pct:.0f}%" if pct >= 10 else f"{pct:.1f}%", size=20 if big else 17, bold=True, anchor="ra")
        cv.bar((x0, y1 - 12, x1, y1), pct / 100, THEME["accent"] if on else THEME["dim"])
        if big:
            extra = [f"{short(d['domains_blocked'])} domains listed"] if d.get("domains_blocked") else []
            if d.get("clients"):
                extra.append(f"{d['clients']} clients")
            cv.text((x0, y + 34), "  ".join(extra), size=16, color=THEME["dim"], max_width=x1 - x0)
        if time.time() - self._pending < 0.6:
            cv.d.rounded_rectangle(box, radius=14, outline=THEME["accent"], width=3)
