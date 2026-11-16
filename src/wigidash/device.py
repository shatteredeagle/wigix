"""WigiDash (28da:ef01) driver, reverse-engineered from USB captures (see docs/protocol.md).

Display: 1016x592 RGB565 little-endian, one bulk OUT on EP1 per frame.
Touch: reported in the 8-byte status request (IN 0x33); only live after the startup handshake.
"""
from __future__ import annotations

import struct
from typing import NamedTuple

import numpy as np
import usb.core
import usb.util

W, H = 1016, 592
REQ_OUT = 0x21  # host->device, class, interface
REQ_IN = 0xA1   # device->host, class, interface


def rgb565(img) -> bytes:
    """Convert a PIL image to the panel's RGB565 little-endian byte format."""
    a = np.asarray(img.convert("RGB").resize((W, H)), dtype=np.uint16)
    v = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    return v.astype("<u2").tobytes()


class Touch(NamedTuple):
    down: bool   # finger on the glass
    x: int       # 0..W-1
    y: int       # 0..H-1


class WigiDash:
    def __init__(self):
        self.dev = usb.core.find(idVendor=0x28DA, idProduct=0xEF01)
        if self.dev is None:
            raise RuntimeError("WigiDash not found")
        self.dev.set_configuration()
        self.buf = 0
        self._last_touch = Touch(False, 0, 0)
        # Startup handshake, same order as the vendor app. Touch reports stay silent without it.
        for req, n in ((0x05, 13), (0x00, 3), (0x04, 64), (0x10, 48), (0x50, 1)):
            self._read(req, n)
        self.ctrl(0x12)

    def _read(self, req, n):
        return self.dev.ctrl_transfer(REQ_IN, req, 0, 0, n).tobytes()

    def ctrl(self, req, value=0, data=b""):
        self.dev.ctrl_transfer(REQ_OUT, req, value, 0, data)

    def info(self) -> dict:
        """Firmware string and device name."""
        return {"firmware": self._read(0x04, 64).split(b"\0")[0].decode(),
                "name": self._read(0x10, 48)[8:].split(b"\0")[0].decode()}

    @property
    def brightness(self) -> int:
        """Current backlight level, 0-100 (IN 0x50)."""
        return self._read(0x50, 1)[0]

    @brightness.setter
    def brightness(self, percent: int):
        """Set the backlight, 0-100, effective immediately (OUT 0x51). Not saved to the device's flash,
        so it reverts at power-up: set it each time you connect."""
        self.ctrl(0x51, 0, bytes([max(0, min(100, int(percent)))]))

    def show(self, frame: bytes):
        """Display one RGB565 frame (use rgb565() to make one from a PIL image)."""
        assert len(frame) == W * H * 2
        b = self.buf
        self.ctrl(0x91, b << 8, struct.pack("<IHH12x", 0, W, H))
        self.ctrl(0x61, b << 8, struct.pack("<II", 0, len(frame)))
        self.dev.write(0x01, frame, timeout=5000)
        self.ctrl(0x70, 0x30 + b)
        self.buf ^= 1

    def touch(self) -> Touch | None:
        """Poll once. Returns a Touch while the finger is down or on the release edge, else None.

        Status bytes: [state, 0xEA, x_lo, x_hi, y_lo, y_hi, 0, 0]; state 1 = touching, 2 = lift
        (last coordinates), 0 with 0xEA = trailing idle report; all zeros = idle.
        """
        s = self._read(0x33, 8)
        if s[1] != 0xEA:
            if self._last_touch.down:      # went idle without a release report
                self._last_touch = Touch(False, *self._last_touch[1:])
                return self._last_touch
            return None
        t = Touch(s[0] == 1, min(W - 1, s[2] | s[3] << 8), min(H - 1, s[4] | s[5] << 8))
        was_down, self._last_touch = self._last_touch.down, t
        return t if (t.down or was_down) else None
