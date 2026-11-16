# SPDX-License-Identifier: GPL-3.0-only
"""UniFi Network on a UniFi OS console (UDM, UDR, Cloud Key Gen2+, ...), read-only.

    integrations:
      unifi:
        type: unifi
        url: https://unifi.lan                     # the console, not the controller port 8443
        username: wigix                         # a local read-only account is enough
        password_file: ~/.config/wigix/unifi_pw
        site: default
        # verify: ~/.config/wigix/unifi-ca.pem     # consoles ship a self-signed cert: trust its CA file (best),
        # verify: false                            # or turn verification off (see docs: TLS and self-signed certificates)

``data``: ``{wan: {status, ip, rx_bps, tx_bps}, clients, wifi_clients, wired_clients, devices_up, devices_down}``.
"""
import requests

from . import integration
from .net import AuthError, tls_verify
from .polling import PollingIntegration
from ..render import read_secret


@integration("unifi")
class UniFi(PollingIntegration):
    interval = 10.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.base = cfg["url"].rstrip("/")
        self.site = cfg.get("site", "default")
        self.s = requests.Session()               # keeps the TOKEN cookie
        self.s.verify = tls_verify(cfg.get("verify"))
        self.logged_in = False

    def _login(self):
        password = read_secret(self.cfg.get("password"), self.cfg.get("password_file"), "UNIFI_PASSWORD")
        r = self.s.post(self.base + "/api/auth/login", timeout=8,
                        json={"username": self.cfg.get("username", ""), "password": password, "remember": False})
        if r.status_code in (401, 403):
            raise AuthError()                     # a wrong password: do not retry until the config changes
        r.raise_for_status()
        self._csrf(r)
        self.logged_in = True

    def _csrf(self, r):
        # The console issues a CSRF token at login and may rotate it on later replies.
        tok = r.headers.get("X-Updated-CSRF-Token") or r.headers.get("X-CSRF-Token")
        if tok:
            self.s.headers["X-CSRF-Token"] = tok

    def _get(self, path):
        """GET a Network API path; a 401 means the session expired, so log in again and retry once."""
        url = f"{self.base}/proxy/network/api/s/{self.site}{path}"
        for attempt in (0, 1):
            if not self.logged_in:
                self._login()
            r = self.s.get(url, timeout=8)
            if r.status_code == 401 and attempt == 0:
                self.logged_in = False
                self.s.cookies.clear()
                continue
            r.raise_for_status()
            self._csrf(r)
            return r.json().get("data", [])

    def poll(self):
        health = {h.get("subsystem"): h for h in self._get("/stat/health")}
        clients = self._get("/stat/sta")
        wan = health.get("wan", {})
        # Adopted devices are counted per subsystem: gateways under wan, switches under lan, APs under wlan.
        up = sum(int(health.get(k, {}).get("num_adopted", 0)) for k in ("wan", "lan", "wlan"))
        down = sum(int(health.get(k, {}).get("num_disconnected", 0)) for k in ("wan", "lan", "wlan"))
        wifi = sum(1 for c in clients if not c.get("is_wired"))
        return {"wan": {"status": wan.get("status", "unknown"),
                        "ip": wan.get("wan_ip", ""),
                        "rx_bps": float(wan.get("rx_bytes-r", 0)) * 8,   # the API reports bytes/s
                        "tx_bps": float(wan.get("tx_bytes-r", 0)) * 8},
                "clients": len(clients), "wifi_clients": wifi, "wired_clients": len(clients) - wifi,
                "devices_up": up, "devices_down": down}
