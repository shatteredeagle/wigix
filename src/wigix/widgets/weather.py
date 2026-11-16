import datetime as dt
import math

from PIL import ImageDraw

from . import IntegrationWidget, widget
from ..render import THEME

# WMO weather interpretation codes (as returned by Open-Meteo): code -> (text, icon kind)
WMO = {0: ("Clear", "sun"), 1: ("Mostly clear", "sun"), 2: ("Partly cloudy", "partly"), 3: ("Overcast", "cloud"),
       45: ("Fog", "fog"), 48: ("Rime fog", "fog"),
       51: ("Light drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Heavy drizzle", "rain"),
       56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
       61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
       66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
       71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"),
       80: ("Light showers", "rain"), 81: ("Showers", "rain"), 82: ("Heavy showers", "rain"),
       85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
       95: ("Thunderstorm", "thunder"), 96: ("Thunder, hail", "thunder"), 99: ("Thunder, hail", "thunder")}


def describe(code) -> tuple[str, str | None]:
    """WMO code -> (condition text, icon kind). Unknown codes get a text and no icon."""
    if code in WMO:
        return WMO[code]
    return ("Unknown" if code is None else f"Code {code}"), None


def _mix(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def draw_icon(d: ImageDraw.ImageDraw, kind: str | None, cx: int, cy: int, size: int) -> None:
    """Simple vector weather icon centred on (cx, cy), roughly ``size`` px wide. Unknown kinds draw nothing."""
    if kind is None:
        return
    s = size / 2
    sun, cloud = THEME["warn"], _mix(THEME["dim"], THEME["text"], 0.45)
    lw = max(2, size // 16)

    def disc(x, y, r, fill):
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    def sun_at(x, y, r):
        disc(x, y, r, sun)
        for k in range(8):                                    # rays
            a = k * math.pi / 4
            d.line((x + math.cos(a) * r * 1.35, y + math.sin(a) * r * 1.35,
                    x + math.cos(a) * r * 1.8, y + math.sin(a) * r * 1.8), fill=sun, width=lw)

    def cloud_at(x, y, w, fill=cloud):
        r = w * 0.22                                          # three bumps over a flat base
        disc(x - w * 0.22, y, r, fill)
        disc(x + w * 0.1, y - r * 0.55, r * 1.25, fill)
        disc(x + w * 0.3, y + r * 0.1, r * 0.9, fill)
        d.rounded_rectangle((x - w * 0.22, y, x + w * 0.3, y + r), radius=int(r * 0.5), fill=fill)

    if kind == "sun":
        sun_at(cx, cy, s * 0.45)
    elif kind == "partly":
        sun_at(cx - s * 0.3, cy - s * 0.3, s * 0.32)
        cloud_at(cx + s * 0.1, cy + s * 0.2, s * 1.7)
    elif kind == "cloud":
        cloud_at(cx, cy, s * 1.8)
    elif kind == "fog":
        cloud_at(cx, cy - s * 0.3, s * 1.5)
        for k, off in enumerate((0.35, 0.65)):
            y = cy + s * off
            d.line((cx - s * 0.7 + k * s * 0.15, y, cx + s * 0.7 - k * s * 0.15, y), fill=cloud, width=lw)
    else:                                                     # rain, snow, thunder: a cloud with something falling
        cloud_at(cx, cy - s * 0.3, s * 1.8)
        base = cy + s * 0.35
        if kind == "rain":
            for k in (-1, 0, 1):
                x = cx + k * s * 0.4
                d.line((x + 3, base, x - 3, base + s * 0.45), fill=THEME["accent"], width=lw)
        elif kind == "snow":
            for k in (-1, 0, 1):
                disc(cx + k * s * 0.4, base + s * (0.25 if k else 0.45), max(2, lw), THEME["text"])
        else:
            bolt = [(cx + s * 0.1, base - s * 0.1), (cx - s * 0.2, base + s * 0.3), (cx, base + s * 0.3),
                    (cx - s * 0.15, base + s * 0.75), (cx + s * 0.3, base + s * 0.2), (cx + s * 0.08, base + s * 0.2)]
            d.polygon(bolt, fill=sun)


def _t(v, unit="°") -> str:
    return "--" if v is None else f"{round(v):d}{unit}"


@widget("weather")
class Weather(IntegrationWidget):
    """Current weather and a 3-day forecast. Needs an ``open_meteo`` integration."""
    integration_type = "open_meteo"
    interval = 3600.0
    fields = [{"name": "name", "type": "string", "default": "Weather", "help": "title, e.g. the city"},
              {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one)"},
              {"name": "forecast", "type": "bool", "default": True, "help": "show the 3-day row when the card is tall enough"}]

    def draw(self, cv, box):
        d = self.guard(cv, box)
        if d is None:
            return
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name", "Weather"))
        cur, daily, units = d["current"], d.get("daily", []), d.get("units", {})
        text, kind = describe(cur.get("code"))
        deg = units.get("temp", "°")[:1]
        wide, tall = (x1 - x0) > 330, (y1 - y0) > 200
        icon = 64 if (wide or tall) else 52
        draw_icon(cv.d, kind, x0 + icon // 2 + 2, y0 + icon // 2 + 2, icon)
        tx = x0 + icon + 14
        cv.text((tx, y0 - 4), _t(cur.get("temp"), deg), size=46 if (wide or tall) else 38, bold=True, max_width=x1 - tx)
        cv.text((x0, y0 + icon + 8), text, size=18, max_width=x1 - x0)
        today = daily[0] if daily else {}
        y = y0 + icon + 32
        if today:
            cv.text((x0, y), f"H {_t(today.get('hi'), deg)}  L {_t(today.get('lo'), deg)}", size=17, color=THEME["dim"],
                    max_width=x1 - x0)
        if wide:                                              # extra details fit beside the big temperature
            wind, hum = cur.get("wind"), cur.get("humidity")
            cv.text((x1, y0 + 4), f"wind {_t(wind, '')} {units.get('wind', '')}", size=18, anchor="ra", color=THEME["dim"])
            cv.text((x1, y0 + 30), f"humidity {_t(hum, '')}%", size=18, anchor="ra", color=THEME["dim"])
        elif not tall:
            wind, hum = cur.get("wind"), cur.get("humidity")
            cv.text((x0, y + 24), f"{_t(wind, '')} {units.get('wind', '')} · {_t(hum, '')}%", size=15, color=THEME["dim"],
                    max_width=x1 - x0)
        if tall and self.cfg.get("forecast", True) and daily:
            self._forecast(cv, daily[:3], (x0, y1 - 100, x1, y1), deg)
        elif tall:
            wind, hum = cur.get("wind"), cur.get("humidity")
            cv.text((x0, y + 28), f"wind {_t(wind, '')} {units.get('wind', '')}  humidity {_t(hum, '')}%", size=16,
                    color=THEME["dim"], max_width=x1 - x0)

    def _forecast(self, cv, days, box, deg):
        x0, y0, x1, y1 = box
        cw = (x1 - x0) / len(days)
        for i, day in enumerate(days):
            cx = int(x0 + cw * i + cw / 2)
            try:
                name = "Today" if i == 0 else dt.date.fromisoformat(day["date"]).strftime("%a")
            except (ValueError, KeyError):
                name = "?"
            cv.text((cx, y0), name, size=16, color=THEME["dim"], anchor="ma")
            draw_icon(cv.d, describe(day.get("code"))[1], cx, y0 + 46, 48)
            cv.text((cx, y1), f"{_t(day.get('hi'), deg)}/{_t(day.get('lo'), deg)}", size=16, anchor="mb", max_width=cw - 6)
