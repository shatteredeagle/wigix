"""Proxmox VE via the API (read-only calls; a PVEAuditor token is enough).

    integrations:
      pve:
        type: proxmox
        url: https://pve.lan:8006
        token_id: user@pam!name
        token_file: ~/.config/wigidash/pve_token   # the token secret
        node: pve                                   # optional: defaults to the first node
        verify: false                               # PVE ships a self-signed certificate
"""
import requests
import urllib3

from . import integration
from .polling import PollingIntegration
from ..render import read_secret


@integration("proxmox")
class Proxmox(PollingIntegration):
    interval = 5.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        if not cfg.get("verify", False):
            urllib3.disable_warnings()
        self.s = requests.Session()
        self.s.verify = cfg.get("verify", False)
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
