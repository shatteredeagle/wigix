"""Network helpers shared by integrations and widgets: error scrubbing, TLS settings and bounded downloads.

Nothing here talks to a specific service. ``scrub`` and ``describe_error`` keep secrets that travel in URLs
(query-string tokens, ``user:pass@`` credentials) out of the error text that ends up in the editor and the logs.
"""
from __future__ import annotations

import logging
import os
import re
import ssl
from typing import Any

import requests
import urllib3

log = logging.getLogger("wigix")

_USERINFO = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.\-]*://)[^/\s'\"<>@]*@")
_QUERY = re.compile(r"\?[^\s'\"<>)]+")                  # also catches the bare path in "Max retries exceeded with url: /x?a=b"

TLS_HINT = "certificate verification failed: set `verify: false` or `verify: /path/to/ca.pem` (see docs)"
AUTH_FAILED = "authentication failed: check the credentials; restart or edit the config to retry"


class AuthError(RuntimeError):
    """The service definitely rejected our credentials; retrying with the same ones only gets us banned."""

    def __init__(self, message: str = AUTH_FAILED):
        super().__init__(message)


def scrub(text: Any) -> str:
    """Remove ``user:pass@`` credentials and query strings from every URL-like piece of ``text``."""
    s = _USERINFO.sub(r"\1", str(text))
    return _QUERY.sub("", s)


def _chain(e: BaseException):
    """``e`` plus everything it wraps (``__cause__``, ``__context__`` and exceptions nested in ``args``)."""
    seen, todo = set(), [e]
    while todo:
        cur = todo.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        yield cur
        todo += [x for x in (cur.__cause__, cur.__context__, *cur.args) if isinstance(x, BaseException)]


def is_tls_verify_error(e: BaseException) -> bool:
    return any(isinstance(x, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(x) for x in _chain(e))


def describe_error(e: BaseException) -> str:
    """Error text that is safe to show: TLS verification failures get a pointer to the fix, URLs lose their secrets."""
    if is_tls_verify_error(e):
        return TLS_HINT
    return scrub(str(e)) or type(e).__name__


# --- TLS ---------------------------------------------------------------------------------------------------
def tls_verify(value: Any = None, default: bool = True) -> bool | str:
    """Normalise a ``verify:`` setting: True, False, or the path of a CA bundle (``~`` expanded).

    Only an explicit ``false`` silences urllib3's InsecureRequestWarning (that switch is process-wide in urllib3).
    """
    if value is None or value == "":
        return default
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "on"):
            return True
        if low in ("false", "no", "off"):
            value = False
        else:
            return os.path.expanduser(value.strip())
    if not value:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return False
    return True


def ssl_context(verify: bool | str) -> ssl.SSLContext:
    """A client SSL context for the standard-library based callers (docker, websockets)."""
    if verify is False:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=verify if isinstance(verify, str) else None)


# --- bounded downloads ---------------------------------------------------------------------------------------
def fetch_limited(url: str, max_bytes: int, **kw) -> bytes:
    """GET ``url`` and return the body, giving up as soon as it is larger than ``max_bytes`` (nothing more is read)."""
    with requests.get(url, stream=True, **kw) as r:
        r.raise_for_status()
        if int(r.headers.get("Content-Length") or 0) > max_bytes:
            raise ValueError("download too large")
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > max_bytes:
                raise ValueError("download too large")
        return bytes(buf)
