"""Minimal WigiDash (28da:ef01) display driver, reverse-engineered from USB captures.

Frame: 1016x592 RGB565 little-endian, pushed as one bulk OUT on EP1.
Per frame: 0x91 (rect) -> 0x61 (byte length) -> bulk -> 0x70 (commit).
"""
import struct

import usb.core
import usb.util

W, H = 1016, 592
REQ_TYPE = 0x21  # host->device, class, interface

def rgb565(img):
    img = img.convert("RGB").resize((W, H))
    out = bytearray(W * H * 2)
    px = img.load()
    i = 0
    for y in range(H):
        for x in range(W):
            r, g, b = px[x, y]
            v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            out[i] = v & 255; out[i + 1] = v >> 8; i += 2
    return bytes(out)

class WigiDash:
    def __init__(self):
        self.dev = usb.core.find(idVendor=0x28DA, idProduct=0xEF01)
        if self.dev is None: raise RuntimeError("WigiDash not found")
        self.dev.set_configuration()
        self.buf = 0

    def ctrl(self, req, value=0, data=b""):
        self.dev.ctrl_transfer(REQ_TYPE, req, value, 0, data)

    def show(self, frame: bytes):
        assert len(frame) == W * H * 2
        b = self.buf
        self.ctrl(0x91, b << 8, struct.pack("<IHH12x", 0, W, H))
        self.ctrl(0x61, b << 8, struct.pack("<II", 0, len(frame)))
        self.dev.write(0x01, frame, timeout=5000)
        self.ctrl(0x70, 0x30 + b)
        self.buf ^= 1
