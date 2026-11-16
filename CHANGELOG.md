# Changelog

All notable changes to wigix. Versions follow [semantic versioning](https://semver.org/).

## 0.1.0 (first release)

Linux driver and configurable dashboard for the G.SKILL WigiDash USB touch panel (1016x592).

- **Driver**: display (RGB565 frames, brightness), touch input; protocol notes in `docs/protocol.md`.
- **Dashboard daemon**: YAML config, pages on a grid, tap/swipe/fullscreen, hot reload, idle screensaver and dimming,
  startup splash, Home Assistant triggers that pop up a widget.
- **Web editor** (token protected): drag/resize widgets over a live preview, integrations panel with live status, theme
  and settings, undo/redo, raw YAML.
- **Integrations**: Home Assistant, Proxmox, Unraid, Docker, Prometheus, Alertmanager, Uptime Kuma, Pi-hole, AdGuard Home,
  UniFi, Open-Meteo weather, iCal calendar, MPRIS media, Bambu Lab printers, generic JSON/HTTP.
- **Widgets**: 37, including system stats, clock, service checks, camera/video/image, audio visualizer, macro pad,
  drawing pad, Snake and 2048.
- **Tooling**: `wigix doctor`, `wigix init`, `wigix autostart enable [--boot]`, `wigix url`, optional system tray icon
  (`pip install 'wigix[tray]'`).
- **Packaging**: PyPI package, AppImage, Arch PKGBUILD, Docker image, udev rule and systemd user unit.
- **Security**: editor access token, Host allow-list, strict CSP; code-execution settings are hand-edit only; TLS verified
  by default; secrets read from files. See `SECURITY.md`.
