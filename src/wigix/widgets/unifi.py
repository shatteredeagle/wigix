# SPDX-License-Identifier: GPL-3.0-only
from . import IntegrationWidget, widget
from ..render import THEME, font

WAN_COLOR = {"ok": "ok", "warning": "warn"}      # anything else (error, unknown, ...) is shown as critical


def rate(bps) -> str:
    """Bits per second -> '12.3 Mb/s'."""
    bps = float(bps)
    for unit, size in (("Gb/s", 1e9), ("Mb/s", 1e6), ("kb/s", 1e3)):
        if bps >= size:
            return f"{bps / size:.1f} {unit}" if bps < size * 100 else f"{bps / size:.0f} {unit}"
    return f"{bps:.0f} b/s"


@widget("unifi")
class UniFi(IntegrationWidget):
    """UniFi network: WAN status, up/down throughput, client counts and devices online. Needs a ``unifi`` integration."""
    integration_type = "unifi"
    interval = 3600.0
    fields = [{"name": "name", "type": "string", "default": "Network"},
              {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one)"}]

    def draw(self, cv, box):
        d = self.guard(cv, box)
        if d is None:
            return
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name", "Network"))
        wan = d.get("wan", {})
        status = wan.get("status", "unknown")
        color = THEME[WAN_COLOR.get(status, "crit")]
        # WAN status dot + word in the title row
        label = f"WAN {status}"
        cv.text((x1, box[1] + 8), label, size=16, color=color, anchor="ra")
        dot_x = x1 - font(16).getlength(label) - 16
        cv.d.ellipse((dot_x - 5, box[1] + 14, dot_x + 5, box[1] + 24), fill=color)
        big = (y1 - y0) > 170
        y = y0
        for arrow, key in (("v", "rx_bps"), ("^", "tx_bps")):
            # arrow glyphs are drawn, not typed, so they do not depend on the font's coverage
            self._arrow(cv, x0 + 8, y + 13, arrow == "v", THEME["accent"] if arrow == "v" else THEME["ok"])
            cv.text((x0 + 26, y), rate(wan.get(key, 0)), size=24 if big else 20, bold=True, max_width=x1 - x0 - 26)
            y += 36 if big else 30
        y += 4
        cv.text((x0, y), f"{d.get('clients', 0)} clients", size=20 if big else 17, max_width=x1 - x0)
        if big:
            y += 28
            cv.text((x0, y), f"{d.get('wifi_clients', 0)} wifi  {d.get('wired_clients', 0)} wired", size=16,
                    color=THEME["dim"], max_width=x1 - x0)
        down = d.get("devices_down", 0)
        cv.text((x0, y1), f"devices {d.get('devices_up', 0)} up" + (f", {down} down" if down else ""),
                size=17, color=THEME["crit"] if down else THEME["dim"], anchor="lb", max_width=x1 - x0)

    @staticmethod
    def _arrow(cv, cx, cy, down, color):
        s = 7
        pts = [(cx - s, cy - s // 2), (cx + s, cy - s // 2), (cx, cy + s)] if down else \
              [(cx - s, cy + s // 2), (cx + s, cy + s // 2), (cx, cy - s)]
        cv.d.polygon(pts, fill=color)
