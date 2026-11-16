# SPDX-License-Identifier: GPL-3.0-only
"""Base class for integrations that poll an API on an interval and expose the latest result as ``.data``."""
from __future__ import annotations

import logging
import threading
import time

from . import Integration
from .net import AUTH_FAILED, AuthError, describe_error

log = logging.getLogger("wigix")


class PollingIntegration(Integration):
    """Subclass and implement ``poll() -> data`` (do the network I/O there; raise on failure).

    - ``interval`` seconds between polls (overridable with ``interval:`` in the config)
    - ``data`` is None until the first success; after a failure the previous data is kept and ``error`` is set
    - ``connected`` is True while the last poll succeeded
    - ``poll()`` raises ``AuthError`` when the service definitely rejected our credentials: polling then stops
      (retrying a bad password only gets us banned) until the config is edited or wigix restarted
    - ``error`` is scrubbed of URL query strings and credentials, so tokens never reach the editor or the logs
    """
    interval = 10.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.data = None
        self.error: str | None = None
        self.updated = 0.0
        self.auth_failed = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self.data is not None and self.error is None

    def poll(self):
        raise NotImplementedError

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"{self.type_name}-{self.name}")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def refresh(self):
        """Poll once now (used by tests and for 'refresh after an action')."""
        if self.auth_failed:
            return
        try:
            self.data = self.poll()
            self.error = None
            self.updated = time.time()
        except AuthError:
            self.auth_failed, self.error = True, AUTH_FAILED
            log.warning("%s %s: %s", self.type_name, self.name, AUTH_FAILED)
        except Exception as e:
            self.error = describe_error(e)
            log.debug("%s %s poll failed: %s", self.type_name, self.name, self.error)

    def _run(self):
        backoff = 0
        while not self._stop.is_set() and not self.auth_failed:
            self.refresh()
            interval = float(self.cfg.get("interval", self.interval))
            backoff = min(backoff * 2 or interval, 60.0) if self.error else 0
            self._stop.wait(max(interval, backoff))

    def status(self):
        return {"connected": self.connected, "error": self.error, "updated": self.updated}
