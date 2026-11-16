"""Uptime Kuma via a public status page (no login or API key needed).

    integrations:
      kuma:
        type: uptime_kuma
        url: https://kuma.example.com
        slug: default                 # the status page's URL slug: https://kuma.example.com/status/<slug>
        verify: true

Create a status page in Kuma and add the monitors you want; only those are visible here.
``data`` is a list of ``{name, status, ping}``: status 1 up, 0 down, 2 pending, 3 maintenance
(None when a monitor has no heartbeat yet); ping is the latest response time in ms or None.
"""
from __future__ import annotations

import requests

from . import integration
from .polling import PollingIntegration


@integration("uptime_kuma")
class UptimeKuma(PollingIntegration):
    interval = 30.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.s = requests.Session()
        self.s.verify = cfg.get("verify", True)

    def _get(self, path):
        r = self.s.get(f"{self.cfg['url'].rstrip('/')}/api/status-page/{path}", timeout=8)
        r.raise_for_status()
        return r.json()

    def poll(self):
        slug = self.cfg["slug"]
        page = self._get(slug)
        beats = self._get(f"heartbeat/{slug}").get("heartbeatList") or {}
        out = []
        for group in page.get("publicGroupList") or []:
            for m in group.get("monitorList") or []:
                hb = (beats.get(str(m.get("id"))) or [None])[-1] or {}          # newest heartbeat is last
                out.append({"name": m.get("name", "?"), "status": hb.get("status"), "ping": hb.get("ping")})
        return out
