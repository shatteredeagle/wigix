"""Prometheus Alertmanager: the currently firing alerts (silenced and inhibited ones are left out).

    integrations:
      alerts:
        type: alertmanager
        url: https://alertmanager.example.com
        token_file: ~/.config/wigidash/am_token     # optional; or username/password(_file) for basic auth
        verify: true

``data`` is a list of ``{name, severity, summary, instance}``, most severe first.
"""
from __future__ import annotations

from . import integration
from .polling import PollingIntegration
from .prometheus import make_session

SEVERITY_ORDER = {"critical": 0, "error": 1, "warning": 2}


@integration("alertmanager")
class Alertmanager(PollingIntegration):
    interval = 30.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.s = make_session(cfg)

    def poll(self):
        r = self.s.get(self.cfg["url"].rstrip("/") + "/api/v2/alerts",
                       params={"active": "true", "silenced": "false", "inhibited": "false"}, timeout=8)
        r.raise_for_status()
        out = []
        for a in r.json():
            lab, ann = a.get("labels") or {}, a.get("annotations") or {}
            out.append({"name": lab.get("alertname", "alert"), "severity": lab.get("severity", ""),
                        "summary": ann.get("summary") or ann.get("description") or "",
                        "instance": lab.get("instance", "")})
        return sorted(out, key=lambda a: (SEVERITY_ORDER.get(a["severity"], 3), a["name"]))
