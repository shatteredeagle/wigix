"""Docker Engine API, read-only: the container list.

    integrations:
      docker:
        type: docker
        socket: /var/run/docker.sock        # default; the user running wigidash needs access to it
        # url: tcp://docker-host.lan:2375   # or a TCP endpoint (http:// and https:// work too)
        # verify: false                     # https endpoints only

``data`` is a list of ``{name, state, status, image}``: ``state`` is Docker's (running, exited, paused,
restarting, dead, created) and ``status`` its text ("Up 3 hours (unhealthy)"). Only the standard library
is used, so there is nothing extra to install.
"""
from __future__ import annotations

import http.client
import json
import socket
import ssl
from urllib.parse import urlparse

from . import integration
from .polling import PollingIntegration


class _UnixConnection(http.client.HTTPConnection):
    """HTTPConnection that talks over an AF_UNIX socket instead of TCP."""

    def __init__(self, path, timeout=8):
        super().__init__("localhost", timeout=timeout)
        self.unix_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.unix_path)


@integration("docker")
class Docker(PollingIntegration):
    interval = 10.0

    def _connection(self):
        url = self.cfg.get("url")
        if not url:
            return _UnixConnection(self.cfg.get("socket", "/var/run/docker.sock"))
        u = urlparse(url.replace("tcp://", "http://", 1))
        if u.scheme == "https":
            ctx = ssl.create_default_context()
            if not self.cfg.get("verify", True):
                ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            return http.client.HTTPSConnection(u.hostname, u.port or 2376, timeout=8, context=ctx)
        return http.client.HTTPConnection(u.hostname, u.port or 2375, timeout=8)

    def _get(self, path):
        conn = self._connection()
        try:
            conn.request("GET", path, headers={"Accept": "application/json"})
            r = conn.getresponse()
            body = r.read()
            if r.status != 200:
                raise RuntimeError(f"docker API {r.status}: {body[:120].decode(errors='replace')}")
            return json.loads(body)
        finally:
            conn.close()

    def poll(self):
        out = []
        for c in self._get("/containers/json?all=1"):
            names = c.get("Names") or [c.get("Id", "?")[:12]]
            out.append({"name": names[0].lstrip("/"), "state": c.get("State", ""),
                        "status": c.get("Status", ""), "image": c.get("Image", "")})
        return sorted(out, key=lambda c: c["name"].lower())
