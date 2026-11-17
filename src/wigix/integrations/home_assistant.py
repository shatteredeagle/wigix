# SPDX-License-Identifier: GPL-3.0-only
"""Home Assistant over the WebSocket API: one connection, live state cache, auto-reconnect.

Config (under ``integrations:``)::

    ha:
      type: home_assistant
      url: http://homeassistant.local:8123      # or https://...
      token_file: ~/.config/wigix/ha_token   # long-lived access token (or `token:` / $HA_TOKEN)
      verify: true                               # or a CA bundle path; false only for self-signed (see docs)
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time

import requests
import websocket

from . import Integration, integration
from .net import describe_error, scrub, tls_verify
from ..render import read_secret

log = logging.getLogger("wigix.ha")


@integration("home_assistant")
class HomeAssistant(Integration):
    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.url = cfg.get("url", "http://homeassistant.local:8123").rstrip("/")
        self.token = read_secret(cfg.get("token"), cfg.get("token_file"), "HA_TOKEN")
        self.verify = tls_verify(cfg.get("verify"))
        self.states: dict[str, dict] = {}
        self.connected = False
        self.error: str | None = None
        self._ws: websocket.WebSocketApp | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ids = iter(range(1, 1 << 30))
        self._pending: dict[int, threading.Event] = {}
        self._results: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._rest = requests.Session()
        self._rest.headers["Authorization"] = f"Bearer {self.token}"
        self._rest.verify = self.verify
        self._history_cache: dict[tuple, tuple[float, list]] = {}

    # --- lifecycle ------------------------------------------------------------------------
    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"ha-{self.name}")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def status(self):
        return {"connected": self.connected, "error": self.error, "entities": len(self.states)}

    def _ws_url(self):
        return self.url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"

    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            t0 = time.time()
            self._ws = websocket.WebSocketApp(
                self._ws_url(), on_message=self._on_message, on_close=self._on_close, on_error=self._on_error)
            sslopt = ({"cert_reqs": ssl.CERT_NONE, "check_hostname": False} if self.verify is False
                      else {"ca_certs": self.verify} if isinstance(self.verify, str) else {})
            try:
                self._ws.run_forever(ping_interval=25, ping_timeout=10, sslopt=sslopt)
            except Exception as e:
                self.error = describe_error(e)
            self.connected = False
            if time.time() - t0 > 30:
                backoff = 1.0
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)

    def _on_error(self, _ws, err):
        self.error = describe_error(err) if isinstance(err, BaseException) else scrub(err)

    def _on_close(self, *_):
        self.connected = False
        with self._lock:                      # release anyone waiting on a reply
            for ev in self._pending.values():
                ev.set()

    # --- protocol -------------------------------------------------------------------------
    def _send(self, payload: dict) -> int:
        mid = next(self._ids)
        self._ws.send(json.dumps({"id": mid, **payload}))
        return mid

    def _on_message(self, ws, raw):
        msg = json.loads(raw)
        t = msg.get("type")
        if t == "auth_required":
            ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        elif t == "auth_ok":
            self.error = None
            self._send_subscribe()
        elif t == "auth_invalid":
            self.error = "authentication failed (bad token?)"
            self._stop.set()                  # retrying with a bad token only spams HA's ban list
            ws.close()
        elif t == "event":
            self._on_event(msg["event"])
        elif t == "result":
            with self._lock:
                ev = self._pending.pop(msg["id"], None)
                if ev is not None:
                    self._results[msg["id"]] = msg
                    ev.set()
            if msg["id"] == self._states_id and msg.get("success"):
                self.states = {s["entity_id"]: s for s in msg["result"]}
                self.connected = True
                log.info("connected to %s (%d entities)", self.url, len(self.states))

    def _send_subscribe(self):
        self._send({"type": "subscribe_events", "event_type": "state_changed"})
        self._states_id = self._send({"type": "get_states"})

    _states_id = -1

    def _on_event(self, ev):
        if ev.get("event_type") != "state_changed":
            return
        d = ev["data"]
        if d.get("new_state"):
            self.states[d["entity_id"]] = d["new_state"]
        else:
            self.states.pop(d["entity_id"], None)

    # --- API for widgets ------------------------------------------------------------------
    def state(self, entity_id: str) -> dict | None:
        return self.states.get(entity_id)

    def entities(self) -> list[dict]:
        """Light summary of every entity, for the editor's picker."""
        out = []
        for eid, s in sorted(self.states.items()):
            a = s.get("attributes", {})
            out.append({"id": eid, "name": a.get("friendly_name", eid), "state": s["state"],
                        "unit": a.get("unit_of_measurement", ""), "domain": eid.split(".", 1)[0],
                        "device_class": a.get("device_class", "")})
        return out

    def call_service(self, domain: str, service: str, data: dict | None = None):
        r = self._rest.post(f"{self.url}/api/services/{domain}/{service}", json=data or {}, timeout=6)
        r.raise_for_status()

    def camera_image(self, entity_id: str) -> bytes:
        """Current still (JPEG/PNG bytes) of a ``camera.*`` entity via HA's camera proxy. Raises on any error."""
        r = self._rest.get(f"{self.url}/api/camera_proxy/{entity_id}", timeout=6)
        r.raise_for_status()
        return r.content

    def history_series(self, entity_id: str, hours: float = 24, max_points: int = 240) -> list[tuple[float, float]]:
        """Numeric history as [(unix_time, value)] for time-based graphs, cached for 5 minutes."""
        import datetime as dt
        key = ("series", entity_id, hours)
        hit = self._history_cache.get(key)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        start = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - hours * 3600))
        r = self._rest.get(f"{self.url}/api/history/period/{start}",
                           params={"filter_entity_id": entity_id, "minimal_response": "1", "no_attributes": "1"},
                           timeout=20)
        r.raise_for_status()
        pts = []
        for s in (r.json() or [[]])[0]:
            try:
                ts = dt.datetime.fromisoformat(str(s.get("last_changed") or s["last_updated"]).replace("Z", "+00:00")).timestamp()
                pts.append((ts, float(s["state"])))
            except (ValueError, KeyError, TypeError):
                continue
        if len(pts) > max_points:                      # thin evenly, always keeping the newest point
            step = len(pts) / max_points
            pts = [pts[int(i * step)] for i in range(max_points - 1)] + [pts[-1]]
        self._history_cache[key] = (time.time(), pts)
        return pts

    def history(self, entity_id: str, hours: float = 24, max_points: int = 120) -> list[float]:
        """Numeric history for a sparkline, cached for 5 minutes."""
        key = (entity_id, hours)
        hit = self._history_cache.get(key)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        start = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - hours * 3600))
        r = self._rest.get(f"{self.url}/api/history/period/{start}",
                           params={"filter_entity_id": entity_id, "minimal_response": "1", "no_attributes": "1"},
                           timeout=15)
        r.raise_for_status()
        vals = []
        for s in (r.json() or [[]])[0]:
            try:
                vals.append(float(s["state"]))
            except (ValueError, KeyError):
                continue
        if len(vals) > max_points:                      # thin evenly
            step = len(vals) / max_points
            vals = [vals[int(i * step)] for i in range(max_points)]
        self._history_cache[key] = (time.time(), vals)
        return vals
