# wigidash

Open-source Linux driver for the G.SKILL WigiDash USB panel (1016x592 touchscreen), plus a
configurable dashboard on top: system stats for many machines, alerts, Home Assistant widgets.

Status: **early.** The display works (see `docs/protocol.md`). The dashboard daemon and touch
input are in progress.

## Quick start

```sh
python -m venv .venv && .venv/bin/pip install -e .
# udev rule so you don't need root:
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="28da", ATTR{idProduct}=="ef01", MODE="0666"' | sudo tee /etc/udev/rules.d/70-wigidash.rules
sudo udevadm control --reload && sudo udevadm trigger
.venv/bin/python examples/test_pattern.py
```

If the device ignores frames, unplug and replug it (see Quirks in `docs/protocol.md`).

## Not affiliated

Not affiliated with or endorsed by G.SKILL. The protocol was worked out from USB captures for
interoperability. No vendor code or firmware is included.

Licensed under the GPL-3.0.
