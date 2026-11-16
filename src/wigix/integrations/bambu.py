# SPDX-License-Identifier: GPL-3.0-only
"""Bambu Lab 3D printer over its local LAN MQTT broker (P1S, P1P, X1, A1 ... in LAN mode).

Config (under ``integrations:``)::

    printer:
      type: bambu
      host: printer.lan                       # the printer's address
      serial: 00M00A000000000                 # printer serial number
      access_code_file: ~/.config/wigix/bambu_code   # 8-character LAN access code (or `access_code:` / $BAMBU_ACCESS_CODE)

The printer only sends *changes* after the first full report, so incoming messages are merged into one
persistent dict, exposed as ``data`` (the printer's ``print`` object) once a complete state has arrived.
The TLS connection is always unverified: printers use a self-signed certificate and there is no way to pin it.
See docs/printer-and-audio.md for how to find the serial and access code.
"""
from __future__ import annotations

import json
import logging
import ssl
import threading

import paho.mqtt.client as mqtt

from . import Integration, integration
from .net import AUTH_FAILED, scrub
from ..render import read_secret

log = logging.getLogger("wigix.bambu")

BAD_CREDENTIALS = (134, 135)  # MQTT reason codes: bad user name or password, not authorized
PUSHALL_EVERY = 60.0          # seconds; asks the printer for a full state report again


@integration("bambu")
class Bambu(Integration):
    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.host = cfg.get("host", "")
        self.serial = str(cfg.get("serial", ""))
        self.port = int(cfg.get("port", 8883))
        self.access_code = read_secret(cfg.get("access_code"), cfg.get("access_code_file"), "BAMBU_ACCESS_CODE")
        self.data: dict | None = None         # merged ``print`` state; None until the first full report
        self.error: str | None = None
        self.connected = False
        self.auth_failed = False              # the printer rejected the access code: stop reconnecting
        self._state: dict = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: mqtt.Client | None = None

    @property
    def topic_report(self) -> str:
        return f"device/{self.serial}/report"

    @property
    def topic_request(self) -> str:
        return f"device/{self.serial}/request"

    # --- lifecycle ------------------------------------------------------------------------
    def start(self):
        if self._thread:
            return
        if not (self.host and self.serial):
            self.error = "host and serial are required"
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"bambu-{self.name}")
        self._thread.start()

    def stop(self):
        self._stop.set()
        client = self._client
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:
                pass

    def status(self):
        return {"connected": self.connected, "error": self.error, "serial": self.serial}

    def _make_client(self) -> mqtt.Client:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"wigix-{self.name}")
        c.username_pw_set("bblp", self.access_code)
        # Bambu printers always use a self-signed certificate (a protocol property, not a setting), so the TLS
        # connection is deliberately unverified; the access code is the only credential.
        c.tls_set(cert_reqs=ssl.CERT_NONE)
        c.tls_insecure_set(True)
        c.reconnect_delay_set(min_delay=1, max_delay=60)        # paho reconnects with exponential backoff
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        return c

    def _run(self):
        self._client = self._make_client()
        try:
            self._client.connect_async(self.host, self.port, keepalive=30)
            self._client.loop_start()
        except Exception as e:
            self.error = scrub(e)
            return
        # The printer only pushes deltas, so periodically request a complete report.
        while not self._stop.wait(PUSHALL_EVERY):
            self.request_full_state()

    # --- MQTT callbacks -------------------------------------------------------------------
    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None):
        if reason_code.is_failure:
            self.connected = False
            if getattr(reason_code, "value", None) in BAD_CREDENTIALS:               # "Bad user name or password" / "Not authorized"
                self.auth_failed, self.error = True, AUTH_FAILED
                self._stop.set()                                   # keep this error: _on_disconnect leaves it alone
                client.disconnect()                                # and paho stops reconnecting with the wrong code
                return
            self.error = scrub(f"connect failed: {reason_code}")
            return
        self.connected = True
        self.error = None
        client.subscribe(self.topic_report)
        self.request_full_state()
        log.info("connected to printer %s", self.host)

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties=None):
        self.connected = False
        if not self._stop.is_set():
            self.error = scrub(f"disconnected: {reason_code}")

    def _on_message(self, _client, _userdata, msg):
        self.handle_message(msg.payload)

    def request_full_state(self):
        """Ask the printer to send its complete state (it normally sends only what changed)."""
        client = self._client
        if client is None or not self.connected:
            return
        try:
            client.publish(self.topic_request, json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
        except Exception as e:
            log.debug("pushall failed: %s", e)

    # --- state ----------------------------------------------------------------------------
    def handle_message(self, payload) -> None:
        """Merge one report message (bytes, str or dict) into the persistent state. Safe to call directly."""
        try:
            msg = payload if isinstance(payload, dict) else json.loads(payload)
        except (ValueError, TypeError):
            return
        part = msg.get("print") if isinstance(msg, dict) else None
        if not isinstance(part, dict):
            return                                              # other report kinds (info, system ...) are ignored
        with self._lock:
            self._state.update(part)
            # A full report carries gcode_state; before that we only have fragments, which would draw half-empty.
            if "gcode_state" in self._state:
                self.data = dict(self._state)
