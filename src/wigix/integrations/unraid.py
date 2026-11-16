"""Unraid (7+) via its GraphQL API.

    integrations:
      tower:
        type: unraid
        url: https://tower.lan/graphql
        api_key_file: ~/.config/wigix/unraid_key
        # verify: ~/.config/wigix/unraid-ca.pem   # CA bundle for a self-signed certificate, or false (see docs)
"""
import requests

from . import integration
from .net import tls_verify
from .polling import PollingIntegration
from ..render import read_secret

QUERY = """{ metrics { cpu { percentTotal } memory { percentTotal used total } }
  array { state capacity { kilobytes { free used total } } }
  docker { containers { state } } }"""


@integration("unraid")
class Unraid(PollingIntegration):
    interval = 5.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.verify = tls_verify(cfg.get("verify"))
        self.key = read_secret(cfg.get("api_key"), cfg.get("api_key_file"), "UNRAID_API_KEY")

    def poll(self):
        r = requests.post(self.cfg["url"], json={"query": QUERY}, headers={"x-api-key": self.key},
                          verify=self.verify, timeout=8)
        r.raise_for_status()
        j = r.json()
        if j.get("errors"):
            raise RuntimeError(j["errors"][0]["message"])
        return j["data"]
