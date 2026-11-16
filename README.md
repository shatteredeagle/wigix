# wigix

An open-source Linux driver and configurable dashboard for the **G.SKILL WigiDash** USB touch panel
(1016x592). Think "btop for all your machines" plus alerts, Home Assistant controls, weather, a calendar and
launcher buttons, laid out on a grid and edited in your browser.

> Status: early but working. Tested on Linux with firmware `1.0.20241006.1755`.
> Not affiliated with G.SKILL. The USB protocol was worked out from captures for interoperability; no vendor
> code or firmware is included.

## What you get

- **Driver** (`wigix.WigiDash`): show frames, read touch. Protocol notes in [docs/protocol.md](docs/protocol.md).
- **Dashboard daemon**: pages of widgets on a grid, touch (tap, swipe between pages, fullscreen a widget), hot
  reload of the config.
- **Web editor** at `http://localhost:8765`: drag and resize widgets over a live preview, edit their settings, save.
- **Widgets**: system stats, clock, text, service checks, launcher buttons, and an integrations system for
  Home Assistant, Proxmox, Unraid, Docker, Prometheus, ... (see [docs/](docs/)).
- **Plugins**: a widget or integration is one Python file. See [docs/writing-plugins.md](docs/writing-plugins.md).

## Install

```sh
git clone <this repo> && cd wigix
python -m venv .venv && .venv/bin/pip install -e .
# let your user talk to the panel without root:
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="28da", ATTR{idProduct}=="ef01", MODE="0666"' | sudo tee /etc/udev/rules.d/70-wigix.rules
sudo udevadm control --reload && sudo udevadm trigger
```

## Run

```sh
cp examples/dashboard.yaml ~/.config/wigix/dashboard.yaml
.venv/bin/wigix preview -c ~/.config/wigix/dashboard.yaml -o preview.png   # no hardware needed
.venv/bin/wigix run -c ~/.config/wigix/dashboard.yaml                      # drive the panel
.venv/bin/wigix editor -c ~/.config/wigix/dashboard.yaml                   # web editor only
```

Start it at login: copy `contrib/wigix.service` to `~/.config/systemd/user/` and
`systemctl --user enable --now wigix`. To get the editor inside the daemon, add `editor: {port: 8765}` to the config.

## Known quirk

After the vendor's Windows software (or a firmware flash) has used the panel, it can sit on its boot logo or a black
screen until you **unplug and replug** the USB cable. A fresh plug-in works.

## Docs

- [Home Assistant](docs/home-assistant.md)
- [Monitoring integrations](docs/integrations-monitoring.md): JSON/HTTP, Prometheus, Alertmanager, Docker, Uptime Kuma
- [Network and personal integrations](docs/integrations-network.md): Pi-hole, AdGuard, UniFi, weather, calendar, media
- [Writing widgets and integrations](docs/writing-plugins.md)
- [USB protocol](docs/protocol.md)

## Security notes

- The web editor binds to localhost, checks the `Host` header and requires a custom header on writes. Don't expose
  it to a network you don't trust: it can edit the config, and the config can start commands.
- `command:` actions are ignored unless the config sets `security: {allow_commands: true}`.
- Keep API tokens in files with mode 0600 (`token_file:`), not in the YAML.

Licensed under the GPL-3.0.
