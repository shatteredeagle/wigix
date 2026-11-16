# SPDX-License-Identifier: GPL-3.0-only
"""Weather from Open-Meteo (free, no API key).

    integrations:
      weather:
        type: open_meteo
        latitude: 48.8566
        longitude: 2.3522
        units: metric                # or imperial
        # interval: 600              # seconds
        # base_url: https://api.open-meteo.com   # only to point at a proxy or a test server

``data``: ``{current: {temp, feels_like, humidity, wind, code, is_day}, daily: [{date, code, hi, lo}] (3 days,
today first), units: {temp, wind}}``. ``code`` is a WMO weather code.
"""
import requests

from . import integration
from .polling import PollingIntegration


@integration("open_meteo")
class OpenMeteo(PollingIntegration):
    interval = 600.0

    def poll(self):
        imperial = self.cfg.get("units", "metric") == "imperial"
        params = {"latitude": self.cfg["latitude"], "longitude": self.cfg["longitude"],
                  "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
                  "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                  "forecast_days": 3, "timezone": "auto",
                  "temperature_unit": "fahrenheit" if imperial else "celsius",
                  "wind_speed_unit": "mph" if imperial else "kmh"}
        base = self.cfg.get("base_url", "https://api.open-meteo.com").rstrip("/")
        r = requests.get(base + "/v1/forecast", params=params, timeout=10)
        r.raise_for_status()
        j = r.json()
        c, d = j["current"], j.get("daily", {})
        daily = [{"date": day, "code": code, "hi": hi, "lo": lo}
                 for day, code, hi, lo in zip(d.get("time", []), d.get("weather_code", []),
                                              d.get("temperature_2m_max", []), d.get("temperature_2m_min", []))]
        return {"current": {"temp": c["temperature_2m"], "feels_like": c.get("apparent_temperature"),
                            "humidity": c.get("relative_humidity_2m"), "wind": c.get("wind_speed_10m"),
                            "code": c.get("weather_code"), "is_day": bool(c.get("is_day", 1))},
                "daily": daily,
                "units": {"temp": "°F" if imperial else "°C", "wind": "mph" if imperial else "km/h"}}
