# Troubleshooting

Start with `wigix doctor`: it checks most of the causes below and prints the fix. Add `-v` to any command for debug
logs; the service logs are in `journalctl --user -u wigix`.

| Symptom | Likely cause | Fix |
|---|---|---|
| Panel shows only the boot logo, or stays black, but `wigix run` shows no errors | The panel was used by the vendor software or a firmware flash and needs a fresh enumeration | Unplug and replug the USB cable, then start wigix |
| `wigix doctor`: **WigiDash present** fails, `lsusb` shows no `28da` | Charge-only cable, bad port or hub | Use a data cable in a different port; replug |
| `lsusb` shows `28da:ef02` | Panel is in update mode (bootloader) | Finish or cancel the update with the vendor tool, or replug. wigix does not flash firmware |
| `Access denied` / `USBError errno 13` / `permission to open` fails | No udev rule, or it has not been applied | `wigix init --udev`, run the printed commands, replug. Check with `ls -l /dev/bus/usb/*/*` that the node is not `root:root 0644` |
| Rule installed, still denied | Not at the seat (SSH session), or no systemd-logind | Use a group in the rule instead of `uaccess` (`GROUP="plugdev", MODE="0660"`) and add yourself to it |
| `Resource busy` / `errno 16` / `device free` fails | The service or another `wigix run` already holds the panel, or the vendor software is open | `systemctl --user stop wigix`, close the other program |
| `NoBackendError: No backend available` | libusb missing | Arch `pacman -S libusb`, Debian/Ubuntu `apt install libusb-1.0-0`, Fedora `dnf install libusb1` |
| Handshake fails or times out | Panel in a bad state | Replug; try a port directly on the machine, not a hub |
| Black screen right after the vendor software ran | Same as the first row | Replug the cable |
| Picture is fine, touch does nothing | Touch only works after the startup handshake, which `wigix run` does. Another process may have reset the panel | Make sure only one wigix instance runs; replug; check `journalctl --user -u wigix` |
| Touch is offset or swipes do nothing | Only inside widgets that capture touch (draw pad, games) | Swipe on an area outside such a widget; tap a widget twice to leave fullscreen |
| Editor at `localhost:8765` not reachable | `editor:` missing from the config, daemon not running, or another program uses port 8765 | Add `editor: {port: 8765}`, `systemctl --user status wigix`, `ss -ltnp \| grep 8765`; or pick another port |
| Editor refuses another machine or a hostname | It binds to localhost and checks the `Host` header on purpose | Use an SSH tunnel (`ssh -L 8765:localhost:8765 host`). To expose it, set `host:` and `allowed_hosts:` under `editor:` and only on a trusted network |
| Config rejected: `config error: ...` | YAML or widget settings invalid; the running daemon keeps the old config | `wigix doctor` or `wigix preview -c <file>` prints the exact error |
| Home Assistant widgets show "offline" | Wrong `url`, token missing or expired, HA unreachable | Check `url` opens in a browser from the same machine; create a new long-lived token; `token_file` must exist and be `chmod 600` (`doctor` checks) |
| A widget says "token file ... not found" | `token_file:` path wrong or a `~` in a systemd unit | wigix expands `~` itself; check the path exists for the user running the service |
| Service starts, then exits at login | `ExecStart` points to a moved venv/pipx path | `wigix init --systemd --force` rewrites the unit for the current install |
| Service does not start at boot | User units run at login only | `loginctl enable-linger $USER` |
| Text looks ugly or boxy | DejaVu Sans Mono font not found | Arch `ttf-dejavu`, Debian/Ubuntu `fonts-dejavu-core`, Fedora `dejavu-sans-mono-fonts` |
| `media_player`, camera or key actions do nothing | Optional tool missing | `wigix doctor` lists `playerctl`, `parec`, `ffmpeg`, `wtype`/`xdotool`/`ydotool` |
| Command buttons do nothing | `command:` actions are off by default | Set `security: {allow_commands: true}` in the config, knowing what it allows |

Still stuck? Open an issue with the output of `wigix --version`, `wigix doctor --json` and the relevant
`journalctl` lines. Remove any tokens or hostnames you do not want to share.
