# SPDX-License-Identifier: GPL-3.0-only
"""Fake HTTP services for integration tests (TCP and unix socket). Stdlib only.

    srv = FakeHTTP({("GET", "/api/x"): {"a": 1}})          # JSON by default
    srv.routes[("POST", "/login")] = lambda req: (200, {"sid": "s"}, {"Set-Cookie": "k=v"})
    srv.url; srv.requests -> [Request(method, path, query, headers, body)]
"""
import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple
from urllib.parse import parse_qs, urlparse


class Request(NamedTuple):
    method: str
    path: str
    query: dict
    headers: dict
    body: bytes

    def json(self):
        return json.loads(self.body or b"null")


def _handler(srv):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _do(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            req = Request(self.command, u.path, parse_qs(u.query), {k.lower(): v for k, v in self.headers.items()},
                          self.rfile.read(n) if n else b"")
            srv.requests.append(req)
            route = srv.routes.get((self.command, u.path), srv.routes.get(("*", u.path)))
            status, body, headers = 404, {"error": "no route"}, {}
            if route is not None:
                out = route(req) if callable(route) else route
                if isinstance(out, tuple):
                    status, body, *rest = out
                    headers = rest[0] if rest else {}
                else:
                    status, body = 200, out
            data = body if isinstance(body, (bytes, str)) else json.dumps(body)
            data = data.encode() if isinstance(data, str) else data
            self.send_response(status)
            ctype = "application/json" if not isinstance(body, (bytes, str)) else headers.pop("Content-Type", "text/plain")
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = _do
    return H


class FakeHTTP:
    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests: list[Request] = []
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self.url = f"http://127.0.0.1:{self._srv.server_port}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self):
        self._srv.shutdown()


class FakeUnixHTTP:
    """Same, but over a unix socket (for the Docker engine API)."""

    def __init__(self, path, routes=None):
        self.routes = dict(routes or {})
        self.requests: list[Request] = []
        self.path = str(path)
        handler = _handler(self)

        class Srv(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        class H(handler):
            def address_string(self):
                return "unix"

        self._srv = Srv(self.path, H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self):
        self._srv.shutdown()
