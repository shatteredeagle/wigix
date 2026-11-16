# Install and first run

You need a G.SKILL WigiDash (USB `28da:ef01`) and Linux with Python 3.10+. The steps below take you from nothing to a
dashboard on the panel; `wigix doctor` checks each one and tells you what to fix.

## 1. Install wigix

**pipx** (recommended, no root):

```sh
pipx install git+https://github.com/shatteredeagle/wigix      # or: pipx install wigix, once it is on PyPI
```

You also need the libusb system library: Arch `pacman -S libusb`, Debian/Ubuntu `apt install libusb-1.0-0`,
Fedora `dnf install libusb1`. For crisp text install DejaVu fonts (`ttf-dejavu` / `fonts-dejavu-core` /
`dejavu-sans-mono-fonts`).

**Arch Linux** (package with the udev rule and systemd unit included):

```sh
git clone https://github.com/shatteredeagle/wigix && cd wigix/packaging
makepkg -si          # reads packaging/PKGBUILD
```

**From source**:

```sh
git clone https://github.com/shatteredeagle/wigix && cd wigix
python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/wigix --version
```

**Docker**: see [packaging/docker-compose.yml](../packaging/docker-compose.yml). The container gets
`/dev/bus/usb` (raw USB access) and nothing else; key and launcher actions cannot reach your desktop from inside it.

## 2. Let your user open the panel (udev)

Without a rule, only root can open the device. The Arch package installs this for you. Otherwise:

```sh
wigix init --udev          # prints the rule and the commands, runs nothing
```

which amounts to:

```sh
sudo tee /etc/udev/rules.d/70-wigix.rules <<'RULES'
SUBSYSTEM=="usb", ATTR{idVendor}=="28da", ATTR{idProduct}=="ef01", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="28da", ATTR{idProduct}=="ef02", MODE="0660", TAG+="uaccess"
RULES
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=usb --action=add
```

The same on Arch, Debian/Ubuntu and Fedora. `uaccess` gives the user at the console access to the panel; it needs
systemd-logind (every mainstream desktop distro). Unplug and replug the panel afterwards. The copy of this file in
the repository is [contrib/70-wigix.rules](../contrib/70-wigix.rules).

## 3. Write a config and check everything

```sh
wigix init                 # ~/.config/wigix/dashboard.yaml, a 2-page starter (never overwrites without --force)
wigix doctor               # PASS / WARN / FAIL checklist with a fix for each problem
wigix preview -c ~/.config/wigix/dashboard.yaml    # renders preview.png, no hardware needed
wigix run -c ~/.config/wigix/dashboard.yaml        # drives the panel
```

`wigix devices` lists the matching USB devices; `wigix doctor --json` is the same report for scripts.

While `wigix run` is up, open the editor at **http://localhost:8765**: drag and resize widgets over a live preview and
save; the daemon hot-reloads the file. The starter config enables it with `editor: {port: 8765}`. It binds to
localhost only.

## 4. Start it at login (systemd user unit)

```sh
wigix init --systemd       # writes ~/.config/systemd/user/wigix.service for THIS install
systemctl --user daemon-reload && systemctl --user enable --now wigix
# or in one step: wigix init --systemd --enable
journalctl --user -u wigix -f      # logs
```

The Arch package ships the unit at `/usr/lib/systemd/user/wigix.service`: just `systemctl --user enable --now wigix`.
To keep the dashboard running while you are logged out, run `loginctl enable-linger $USER`.

Only one process can hold the panel: stop the service (`systemctl --user stop wigix`) before running `wigix run` by
hand.

## 5. The replug quirk

After the vendor's Windows software (or a firmware flash) has used the panel it can sit on its boot logo or a black
screen until you **unplug and replug** the USB cable. A fresh plug-in works. If `wigix doctor` passes everything but
the screen stays dark, replug first. If the panel enumerates as `28da:ef02` it is in update mode (bootloader): finish
the update with the vendor tool or replug it; wigix does not flash firmware.

## Optional tools

Features that shell out to other programs, which `wigix doctor` lists:

| Tool | Needed for |
|---|---|
| `playerctl` | `media_player` widget |
| `parec` (libpulse / pipewire-pulse) | `audio_visualizer` widget |
| `ffmpeg` | video and camera widgets |
| `wtype` or `ydotool` (Wayland), `xdotool` (X11) | key actions |

See also [troubleshooting](troubleshooting.md).
