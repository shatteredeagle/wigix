# SPDX-License-Identifier: GPL-3.0-only
"""Generic JSON over HTTP: poll a few URLs and let widgets pick values out of the responses.

    integrations:
      api:
        type: http
        base_url: https://api.example.com
        token_file: ~/.config/wigix/api_token   # optional bearer token (or token: / env HTTP_TOKEN)
        headers: {Accept: application/json}        # optional extra headers
        paths: [/v1/stats, /v1/queue]              # polled every `interval` seconds
        interval: 30
        verify: true                               # or a CA bundle path; false only for self-signed (see docs)

The token and ``headers`` are sent only to ``base_url``: a path given as an absolute URL on another host is
fetched without them.

Widgets name the request they want (``request: /v1/stats``) and a path into the JSON (``path: data.items[0].name``).
A request that is not in ``paths`` is added the first time a widget asks for it and shows up one poll later.
"""
from __future__ import annotations

import logging
import re
import threading
from urllib.parse import urlsplit

import requests

from . import integration
from .net import tls_verify
from .polling import PollingIntegration
from ..render import read_secret

log = logging.getLogger("wigix")
_MISSING = object()
_DEFAULT_PORTS = {"http": 80, "https": 443}
# One path step: a bare key, an [index], or a ["quoted key"] (for keys that contain dots or brackets).
_STEP = re.compile(r"""\.?([^.\[\]]+)|\[(-?\d+)\]|\[\s*"([^"]*)"\s*\]|\[\s*'([^']*)'\s*\]""")


def json_path(data, expr, default=None):
    """Look ``expr`` up in parsed JSON: ``data.items[0].name``, ``[-1]``, ``a["b.c"]``. A leading ``$`` is allowed.

    Returns ``default`` when any step is missing (never raises). An empty expression returns ``data`` itself.
    A bare number applied to a list also indexes it, so ``items.0.name`` works too.
    """
    expr = (expr or "").strip()
    if expr.startswith("$"):
        expr = expr[1:]
    pos, cur = 0, data
    while pos < len(expr):
        m = _STEP.match(expr, pos)
        if not m or m.end() == pos:
            return default                                  # malformed expression
        pos = m.end()
        key, idx, dq, sq = m.groups()
        if idx is None and key is not None and isinstance(cur, list) and re.fullmatch(r"-?\d+", key):
            idx, key = key, None
        if idx is not None:
            if not isinstance(cur, list) or not -len(cur) <= int(idx) < len(cur):
                return default
            cur = cur[int(idx)]
        else:
            k = key if key is not None else dq if dq is not None else sq
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
    return cur


def _origin(url: str) -> tuple:
    u = urlsplit(url)
    scheme = u.scheme.lower()
    try:
        port = u.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        port = None
    return scheme, (u.hostname or "").lower(), port


@integration("http")
class HttpJson(PollingIntegration):
    interval = 30.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self._lock = threading.Lock()
        self._paths: list[str] = []
        paths = cfg.get("paths") or []
        for p in ([cfg["path"]] if cfg.get("path") else []) + ([paths] if isinstance(paths, str) else list(paths)):
            self._want(p)
        self.s = requests.Session()
        self.s.verify = tls_verify(cfg.get("verify"))
        # Kept off the session: they are sent only to the configured base_url, never to an absolute URL elsewhere.
        self._headers = {str(k): str(v) for k, v in (cfg.get("headers") or {}).items()}
        tok = read_secret(cfg.get("token"), cfg.get("token_file"), cfg.get("token_env", "HTTP_TOKEN"))
        if tok:
            self._headers["Authorization"] = f"Bearer {tok}"

    def _want(self, path: str) -> None:
        with self._lock:
            if path not in self._paths:
                self._paths.append(path)

    def get(self, path: str | None = None):
        """Cached JSON for a request path (the first configured path when None); no I/O. None until polled."""
        if not path:
            path = self._paths[0] if self._paths else ""
        elif path not in self._paths:
            self._want(path)                               # picked up by the next poll
        return (self.data or {}).get(path)

    def poll(self):
        with self._lock:
            paths = list(self._paths)
        base = (self.cfg.get("base_url") or "").rstrip("/")
        out = {}
        for p in paths:
            url = p if p.startswith(("http://", "https://")) else base + "/" + p.lstrip("/")
            same_host = bool(base) and _origin(url) == _origin(base)
            if self._headers and not same_host:
                log.debug("http %s: not sending the integration's headers to %s (outside base_url)", self.name, _origin(url)[1])
            try:
                r = self.s.get(url, headers=self._headers if same_host else None, timeout=8)
                r.raise_for_status()
                out[p] = r.json()
            except (requests.RequestException, ValueError) as e:
                raise RuntimeError(f"{p}: {e}") from e
        return out
