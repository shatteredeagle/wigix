# SPDX-License-Identifier: GPL-3.0-only
"""Proxmox VE via the API (read-only calls; a PVEAuditor token is enough).

    integrations:
      pve:
        type: proxmox
        url: https://pve.lan:8006
        token_id: user@pam!name
        token_file: ~/.config/wigix/pve_token   # the token secret
        node: pve                                   # optional: defaults to the first node
        # verify: ~/.config/wigix/pve-ca.pem        # PVE ships a self-signed certificate: trust its CA file (best),
        # verify: false                             # or turn verification off (see docs: TLS and self-signed certificates)
"""
import requests

from . import integration
from .net import tls_verify
from .polling import PollingIntegration
from ..render import read_secret


@integration("proxmox")
class Proxmox(PollingIntegration):
    interval = 5.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.s = requests.Session()
        self.s.verify = tls_verify(cfg.get("verify"))
        tok = read_secret(cfg.get("token"), cfg.get("token_file"), "PVE_TOKEN")
        self.s.headers["Authorization"] = f"PVEAPIToken={cfg['token_id']}={tok}"

    def _get(self, path):
        r = self.s.get(self.cfg["url"].rstrip("/") + "/api2/json" + path, timeout=6)
        r.raise_for_status()
        return r.json()["data"]

    def poll(self):
        node = self.cfg.get("node") or self._get("/nodes")[0]["node"]
        guests = sorted(self._get("/cluster/resources?type=vm"), key=lambda g: g["vmid"])
        return {"node": node, "status": self._get(f"/nodes/{node}/status"),
                "guests": [g for g in guests if g.get("node", node) == node]}
