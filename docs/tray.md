# System tray icon

`wigix tray` puts a small icon in your desktop's system tray that shows what the dashboard daemon is doing and gives
you quick controls. It is optional and needs PyQt6:

```sh
pip install 'wigix[tray]'      # Arch: pacman -S python-pyqt6
wigix tray                     # add -c path/to/config.yaml if yours is not ~/.config/wigix/dashboard.yaml
```

It works on KDE Plasma (X11 and Wayland), on GNOME with the AppIndicator extension, and on any desktop where Qt's
`QSystemTrayIcon` works. Only one tray runs at a time (a second one exits at once).

## Icons

| Icon | Meaning |
|---|---|
| connected | The daemon is running, the panel is attached and nothing is wrong. |
| disconnected | The daemon is not reachable, or it is running but the panel is not attached. |
| paused | The display was paused from the tray (or the editor). |
| attention | An integration (Home Assistant, Proxmox, ...) reports an error. The tooltip names the first few. |

The tooltip shows the panel state, the current page and the failing integrations. When the state changes to
*attention* or *disconnected* the tray shows a desktop notification; start it with `--quiet` to turn that off.

## Menu

Left-click (or right-click) opens the menu; middle-click toggles pause.

- a status line (disabled)
- **Open editor**: opens the editor with its access link in your browser
- **Page**: the pages as a checkable list (the current one is ticked) and **Next page**
- **Brightness**: 10, 25, 50, 75 or 100 % (the nearest to the current value is ticked)
- **Auto-rotate pages** and **Screensaver and dimming**: checkable runtime switches (they last until the daemon restarts;
  the config file decides the defaults)
- **Desktop notifications**: on/off (remembered). Off silences the warnings below.
- **Pause display** / **Resume display**
- **Restart daemon**: runs `systemctl --user restart wigix` (only shown if `systemctl` exists)
- **Quit tray**

## Start at login

```sh
wigix autostart enable  # daemon + tray at login (the tray only if PyQt6 is installed)
wigix init --tray       # just the tray: writes ~/.config/autostart/wigix-tray.desktop (--force to replace it)
```

The tray menu also has a **Start at login** checkbox that turns the daemon's login start on and off.

or copy [contrib/wigix-tray.desktop](../contrib/wigix-tray.desktop) there yourself. If the tray host is not up yet at
login, the tray waits up to 30 seconds for it before giving up.

## How it talks to the daemon

The tray polls the daemon's HTTP API every 2 seconds (`GET /api/status`) and sends `POST /api/control` for the menu
actions, always on a background thread, so a hung daemon never freezes the icon. It reads the host and port from the
`editor:` block of your config (default `127.0.0.1:8765`) and authenticates with the access token stored in
`editor_token` next to the config file, sent as `X-Wigix-Token`. The token never leaves your machine and is not logged.
If the daemon is not running, or runs without the editor, the icon shows *disconnected*.

## Troubleshooting

- **No icon on GNOME**: GNOME has no tray. Install the *AppIndicator and KStatusNotifierItem Support* extension and
  enable it; `wigix tray` prints a message and exits with status 1 if no tray appears within 30 seconds.
- **Always disconnected**: check `systemctl --user status wigix`, that `editor:` is enabled in the config, and that you
  pass the same `-c` file the daemon uses (the token file is next to it). `wigix -v tray` logs every poll.
- **Wayland**: nothing special is needed; the icon uses the StatusNotifierItem protocol through D-Bus. If you start the
  tray from a plain SSH or cron environment, set `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS`.
  Some compositors place the left-click menu at the pointer rather than at the icon.
- **No notifications**: they use the tray's own message mechanism; check your desktop's do-not-disturb setting.


## Notifications

The tray only speaks up when something stays wrong: the panel or daemon unreachable, or an integration failing, for two
minutes in a row. A blip that clears by itself (replugging the panel, restarting the daemon, one slow request) never
notifies, the same message is shown at most once an hour, and recoveries are silent. Switch them off from the tray menu
(**Desktop notifications**) or in the config:

```yaml
tray:
  notifications: false     # default true
  notify_delay: 120        # seconds a problem must last before it is announced
  notify_cooldown: 3600    # seconds before the same message can appear again
```

The menu choice is saved in `~/.local/state/wigix/tray.json` and wins over the config file. `wigix tray --quiet` never notifies.
