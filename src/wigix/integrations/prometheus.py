# SPDX-License-Identifier: GPL-3.0-only
"""Prometheus: named instant queries and range queries, polled together.

    integrations:
      prom:
        type: prometheus
        url: https://prometheus.example.com
        # auth (optional): a bearer token, or HTTP basic
        token_file: ~/.config/wigix/prom_token
        # username: wigix
        # password_file: ~/.config/wigix/prom_password
        verify: true                 # or a CA bundle path; false only for self-signed (see docs)
        queries:
          cpu: 'avg(1 - rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100'
        ranges:
          cpu_hist: {query: 'avg(1 - rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100', hours: 6, step: 300}

``data`` is ``{name: value}`` for queries (a float, or None when the query returned nothing) and
``{name: [floats]}`` for ranges. Aggregate (``sum``, ``avg``, ...) so each query yields one series;
if several come back the first is used.
"""
from __future__ import annotations

import math
import time

import requests

from . import integration
from .net import tls_verify
from .polling import PollingIntegration
from ..render import read_secret


def _num(v):
    """Prometheus sample value (a string such as "0.25", "NaN", "+Inf") -> finite float or None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def make_session(cfg) -> requests.Session:
    """Session with bearer or basic auth and TLS verification from an integration config."""
    s = requests.Session()
    s.verify = tls_verify(cfg.get("verify"))
    tok = read_secret(cfg.get("token"), cfg.get("token_file"), cfg.get("token_env", "PROMETHEUS_TOKEN"))
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    elif cfg.get("username"):
        s.auth = (cfg["username"], read_secret(cfg.get("password"), cfg.get("password_file"), "PROMETHEUS_PASSWORD"))
    return s


@integration("prometheus")
class Prometheus(PollingIntegration):
    interval = 30.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.s = make_session(cfg)

    def _api(self, path, params):
        r = self.s.get(self.cfg["url"].rstrip("/") + "/api/v1/" + path, params=params, timeout=10)
        r.raise_for_status()
        j = r.json()
        if j.get("status") != "success":
            raise RuntimeError(j.get("error") or "query failed")
        return j["data"]

    def _instant(self, query):
        data = self._api("query", {"query": query})
        res = data.get("result")
        if data.get("resultType") == "scalar":             # [ts, "value"]
            return _num(res[1]) if res else None
        return _num(res[0]["value"][1]) if res else None

    def _range(self, spec):
        hours = float(spec.get("hours", 1))
        end = time.time()
        step = int(spec.get("step") or max(15, hours * 3600 / 60))      # ~60 points by default
        data = self._api("query_range", {"query": spec["query"], "start": end - hours * 3600, "end": end, "step": step})
        res = data.get("result") or []
        vals = (_num(v[1]) for v in (res[0]["values"] if res else []))
        return [v for v in vals if v is not None]

    def poll(self):
        out = {}
        for name, q in (self.cfg.get("queries") or {}).items():
            try:
                out[name] = self._instant(q)
            except (requests.RequestException, ValueError, KeyError, IndexError) as e:
                raise RuntimeError(f"{name}: {e}") from e
        for name, spec in (self.cfg.get("ranges") or {}).items():
            try:
                out[name] = self._range(spec)
            except (requests.RequestException, ValueError, KeyError, IndexError) as e:
                raise RuntimeError(f"{name}: {e}") from e
        return out
