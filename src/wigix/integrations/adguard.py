# SPDX-License-Identifier: GPL-3.0-only
"""AdGuard Home (HTTP basic auth).

    integrations:
      adguard:
        type: adguard
        url: http://adguard.lan:3000
        username: admin
        password_file: ~/.config/wigix/adguard_pw
        # verify: ~/.config/wigix/ca.pem   # https with a self-signed certificate: a CA file, or false (see docs)

``data`` has the same keys as the Pi-hole integration:
``{kind, queries, blocked, percent_blocked, enabled, ...}`` (statistics cover AdGuard's retention window,
24 h by default).
"""
import requests

from . import integration
from .net import AuthError, tls_verify
from .polling import PollingIntegration
from ..render import read_secret


@integration("adguard")
class AdGuard(PollingIntegration):
    interval = 15.0
    can_toggle = False

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.verify = tls_verify(cfg.get("verify"))
        self.base = cfg["url"].rstrip("/")
        password = read_secret(cfg.get("password"), cfg.get("password_file"), "ADGUARD_PASSWORD")
        self.auth = (cfg.get("username", ""), password) if cfg.get("username") else None

    def _get(self, path):
        r = requests.get(self.base + path, auth=self.auth, verify=self.verify, timeout=6)
        if r.status_code in (401, 403):
            raise AuthError()
        r.raise_for_status()
        return r.json()

    def poll(self):
        stats = self._get("/control/stats")
        status = self._get("/control/status")
        queries = int(stats.get("num_dns_queries", 0))
        # Everything AdGuard rewrote or refused counts as blocked: filter lists plus safe browsing/parental.
        blocked = sum(int(stats.get(k, 0)) for k in
                      ("num_blocked_filtering", "num_replaced_safebrowsing", "num_replaced_parental"))
        return {"kind": "adguard",
                "queries": queries,
                "blocked": blocked,
                "percent_blocked": 100.0 * blocked / queries if queries else 0.0,
                "enabled": bool(status.get("protection_enabled", False)),
                "avg_ms": float(stats.get("avg_processing_time", 0.0)) * 1000,
                "version": status.get("version", "")}
