# Changelog

All notable changes to wigix. Versions follow [semantic versioning](https://semver.org/).

## Unreleased

- **Settings that were missing**: switch the screensaver and dimming off (`idle: false` or `enabled: false`) or keep just
  the dimming (`screensaver: false`); rotate through pages automatically with `rotate:` (per-page `seconds` and
  `rotate: false`, holds still while you touch the panel); `page_dots`; `swipe` and `splash` toggles in the editor.
- **Editor**: drag widgets from the list onto the preview (they land in the cell you drop on; clicking an entry only opens its
  settings, which are applied when you place it), drop a widget on a page
  tab to move it, drag tabs to reorder, double-click a tab to rename; searchable, grouped widget list that is two or more cards wide, resizable by dragging its edge (the
  number of cards per row follows the width; the size and collapsed sections are remembered); page and widget
  settings as cards; overlap warnings while dragging; empty-page hint; daemon status pill with previous / pause / next
  buttons for the panel. Settings tab rebuilt as cards with switches; theme preset picker; page rotation options in the Inspector.
- **Tray**: checkable "Auto-rotate pages" and "Screensaver and dimming" items (runtime switches).
- **btop-style tiles**: `system`, `proxmox`, `unraid`, `node` and `checks` draw framed panels with live CPU / memory /
  network graphs (`style: card` keeps the old look); Proxmox graphs start filled from the node's own history.
- **`air` widget**: radon graph with weekly average and action-level lines, plus temperature, humidity and pressure graphs.
- **Look**: eight theme presets (`theme: nord`), card outlines, page position dots on the panel.

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
