# SPDX-License-Identifier: GPL-3.0-only
"""Pi-hole, v6 (REST API with sessions) and legacy v5 (``api.php`` with an API token).

    integrations:
      pihole:
        type: pihole
        url: http://pihole.lan                     # v6: web interface URL
        password_file: ~/.config/wigix/pihole_pw   # v6 web/app password (omit when none is set)
        # version: 5                                # legacy v5: use api_token(_file) instead of a password
        # api_token_file: ~/.config/wigix/pihole_token
        # verify: ~/.config/wigix/ca.pem             # https with a self-signed certificate: a CA file, or false (see docs)

``data`` is normalised so widgets do not care about the version (same keys as the AdGuard integration):
``{kind, queries, blocked, percent_blocked, enabled, domains_blocked, clients}``.
"""
import requests

from . import integration
from .net import AuthError, tls_verify
from .polling import PollingIntegration
from ..render import read_secret


@integration("pihole")
class PiHole(PollingIntegration):
    interval = 15.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.version = int(cfg.get("version", 6))
        self.verify = tls_verify(cfg.get("verify"))
        self.base = cfg["url"].rstrip("/")
        self.password = read_secret(cfg.get("password"), cfg.get("password_file"), "PIHOLE_PASSWORD")
        self.token = read_secret(cfg.get("api_token"), cfg.get("api_token_file"), "PIHOLE_API_TOKEN")
        self.sid: str | None = None

    @property
    def can_toggle(self) -> bool:
        """Switching blocking is only implemented for the v6 API."""
        return self.version >= 6

    # -- v6 session handling ---------------------------------------------------------------------------
    def _login(self):
        r = requests.post(self.base + "/api/auth", json={"password": self.password}, verify=self.verify, timeout=6)
        if r.status_code in (401, 403):
            raise AuthError()
        r.raise_for_status()
        session = r.json().get("session", {})
        if not session.get("valid") or not session.get("sid"):
            raise AuthError()
        self.sid = session["sid"]

    def _request(self, method, path, **kw):
        """Authenticated v6 call. A 401 means the session expired: log in again and retry once."""
        for attempt in (0, 1):
            if self.sid is None and self.password:
                self._login()
            headers = {"sid": self.sid} if self.sid else {}
            r = requests.request(method, self.base + path, headers=headers, verify=self.verify, timeout=6, **kw)
            if r.status_code == 401 and attempt == 0 and self.password:
                self.sid = None
                continue
            r.raise_for_status()
            return r.json()

    # -- polling ---------------------------------------------------------------------------------------
    def poll(self):
        return self._poll_v6() if self.version >= 6 else self._poll_v5()

    def _poll_v6(self):
        s = self._request("GET", "/api/stats/summary")
        blocking = self._request("GET", "/api/dns/blocking")
        q = s.get("queries", {})
        return {"kind": "pihole",
                "queries": int(q.get("total", 0)),
                "blocked": int(q.get("blocked", 0)),
                "percent_blocked": float(q.get("percent_blocked", 0.0)),
                "enabled": blocking.get("blocking") == "enabled",
                "domains_blocked": int(s.get("gravity", {}).get("domains_being_blocked", 0)),
                "clients": int(s.get("clients", {}).get("active", 0))}

    def _poll_v5(self):
        params = {"summaryRaw": "", "auth": self.token} if self.token else {"summaryRaw": ""}
        r = requests.get(self.base + "/admin/api.php", params=params, verify=self.verify, timeout=6)
        r.raise_for_status()
        s = r.json()
        if not isinstance(s, dict) or "dns_queries_today" not in s:   # v5 answers 200 with [] on a bad token
            raise RuntimeError("unexpected reply (wrong api_token?)")
        return {"kind": "pihole",
                "queries": int(s["dns_queries_today"]),
                "blocked": int(s.get("ads_blocked_today", 0)),
                "percent_blocked": float(s.get("ads_percentage_today", 0.0)),
                "enabled": s.get("status") == "enabled",
                "domains_blocked": int(s.get("domains_being_blocked", 0)),
                "clients": int(s.get("unique_clients", 0))}

    # -- actions ---------------------------------------------------------------------------------------
    def set_blocking(self, enabled: bool):
        """Enable or disable blocking (v6 only) and refresh. Disabling stays off until switched back on."""
        if not self.can_toggle:
            raise NotImplementedError("toggling needs Pi-hole v6")
        self._request("POST", "/api/dns/blocking", json={"blocking": bool(enabled), "timer": None})
        self.refresh()
