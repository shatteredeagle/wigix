# WigiDash USB protocol (as observed)

Derived from USB captures of the vendor's Windows software talking to a device,
for interoperability. Nothing here is vendor code. Corrections welcome.

## Device

| | |
|---|---|
| Normal mode | `28da:ef01` "WigiDash" |
| Update (bootloader) mode | `28da:ef02` "WigiDash Update Mode" |
| Interface | one vendor-specific interface (class 0xFF), one bulk OUT endpoint `0x01` (512 B packets) |
| Tested firmware | `1.0.20241006.1755` |

The device is not HID. Touch input is not documented here yet.

## Display

- Resolution: **1016 x 592**
- Pixel format: **RGB565, little-endian**, row-major, no padding (1,202,944 bytes per frame)
- Double-buffered: buffer index 0 or 1

Per frame, all control requests use `bmRequestType = 0x21` (host-to-device, class, interface), `wIndex = 0`:

| Step | Request | wValue | Data |
|---|---|---|---|
| 1 | `0x91` rect | `buf << 8` | 20 bytes: `u32 0, u16 width, u16 height, 12 x 0` |
| 2 | `0x61` length | `buf << 8` | 8 bytes: `u32 0, u32 byte_count` (little-endian) |
| 3 | bulk OUT, EP `0x01` | | `byte_count` bytes of pixels |
| 4 | `0x70` commit | `0x30 + buf` | none |

Alternate `buf` between 0 and 1 each frame. The vendor software sometimes skips step 1 on
consecutive frames and sends several `0x61` + bulk pairs before a commit; those partial-update
semantics are not yet understood.

## Status / info (IN, `bmRequestType = 0xA1`, wIndex 0)

| Request | Length | Meaning (observed) |
|---|---|---|
| `0x33` | 8 | status poll; the vendor app polls this ~20 Hz; returns zeros when idle |
| `0x00` | 3 | `"WD"` |
| `0x04` | 64 | firmware string, e.g. `WigiDash-1.0_20241006_1755` |
| `0x05` | 13 | version/serial-like string |
| `0x10` | 48 | device info; contains the name ("New WigiDash") |
| `0x50` | 1 | unknown (`"2"`) |

`0x12` (OUT, no data) is sent once after the first commit; purpose unknown.

## Quirks

- After the vendor software (or a firmware flash) has used the device, it can stay on its boot logo
  or a black screen and ignore frames until the USB cable is physically replugged. A driver
  started right after enumeration works.
- Firmware older than what the vendor software expects triggers a flash through update mode
  (`ef02`). Flashing is the vendor tool's job; this project does not implement it.

## Unknown / TODO

Touch input reports, brightness, partial updates, the exact meaning of `0x12`, `0x50`, `0x33`.
