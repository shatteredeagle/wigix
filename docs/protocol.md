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

The device is not HID: everything goes over control requests on endpoint 0 plus the one bulk OUT endpoint.

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

## Startup handshake

The vendor app does this before the first frame, and **touch reports stay silent without it** (and a frame):

1. IN `0x05` (13 bytes), `0x00` (3 bytes, `"WD"`), `0x04` (64 bytes, firmware string), `0x10` (48 bytes, device info), `0x50` (1 byte, brightness)
2. OUT `0x12` (no data)
3. send a frame (see Display)

## Touch

Reported in the 8-byte status read (IN `0x33`, polled ~20-60 Hz):

```
[state, tag, x_lo, x_hi, y_lo, y_hi, 0, 0]
```

`tag` is nonzero: `0xEA` on the first unit examined, `0xED` on a second one (firmware WigiDash-1.0_20231024_1928).
Treat any nonzero value as "a touch report".

`state` 1 = finger down/moving, 2 = lift (carries the last coordinates), 0 = trailing report after a lift. All zeros =
idle. Coordinates are in screen pixels (0..1015, 0..591). A quick tap is `1, 2, 0`. Contact can bounce on firm presses,
so debounce taps.

## Brightness

- Read: IN `0x50`, 1 byte (percent).
- Set: OUT `0x51`, 1 byte (percent 0-100), takes effect immediately and is **not** persisted.
- The vendor app's "Save" additionally sends OUT `0x11` (48-byte device-config block: `[0x01, brightness, ..., "name", ..., checksum]`
  x4) then OUT `0x14`, which persist settings to flash. Not implemented here: it rewrites the device's stored
  configuration, and re-sending the brightness at startup is enough.

## Status / info (IN, `bmRequestType = 0xA1`, wIndex 0)

| Request | Length | Meaning (observed) |
|---|---|---|
| `0x33` | 8 | status + touch report (above); the vendor app polls it ~20 Hz |
| `0x00` | 3 | `"WD"` |
| `0x04` | 64 | firmware string, e.g. `WigiDash-1.0_20241006_1755` |
| `0x05` | 13 | version/serial-like string |
| `0x10` | 48 | device info; contains the name ("New WigiDash") |
| `0x50` | 1 | brightness percent |

`0x12` (OUT, no data) is sent once during startup; purpose unknown (harmless to send).

## Quirks

- After the vendor software (or a firmware flash) has used the device, it can stay on its boot logo
  or a black screen and ignore frames until the USB cable is physically replugged. A driver
  started right after enumeration works.
- Firmware older than what the vendor software expects triggers a flash through update mode
  (`ef02`). Flashing is the vendor tool's job; this project does not implement it.

## Unknown / TODO

Partial-frame updates (the vendor app sometimes sends several `0x61` + bulk pairs between commits), the meaning
of `0x12`, the full `0x11` config block, the update-mode flashing protocol, and whether other firmware versions
differ.
