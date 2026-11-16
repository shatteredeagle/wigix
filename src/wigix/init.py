# SPDX-License-Identifier: GPL-3.0-only
"""`wigix init`: starter config, udev rule, systemd user unit and tray autostart entry."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_CONFIG = Path("~/.config/wigix/dashboard.yaml").expanduser()
UNIT_DIR = Path("~/.config/systemd/user").expanduser()
AUTOSTART_DIR = Path("~/.config/autostart").expanduser()
UDEV_PATH = "/etc/udev/rules.d/70-wigix.rules"

UDEV_RULE = '''\
# ef01 = normal mode, ef02 = update mode (bootloader). uaccess grants the logged-in user access.
SUBSYSTEM=="usb", ATTR{idVendor}=="28da", ATTR{idProduct}=="ef01", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="28da", ATTR{idProduct}=="ef02", MODE="0660", TAG+="uaccess"
'''

UDEV_RELOAD = ["udevadm control --reload",
               "udevadm trigger --subsystem-match=usb --action=add"]

STARTER_CONFIG = '''\
# wigix dashboard. Edit it here or in the web editor (http://localhost:8765 while `wigix run` is up).
# The running daemon hot-reloads this file when it changes.
fps: 2
page_seconds: 0            # >0 rotates through the pages by itself
plugin_dirs: ["~/.config/wigix/widgets"]   # drop your own widget .py files here

editor: {port: 8765}       # web editor inside the daemon; binds to localhost only

# Shared connections for the widgets below. Keep secrets in files with mode 0600 (chmod 600 <file>).
# integrations:
#   ha:
#     type: home_assistant
#     url: http://homeassistant.local:8123
#     token_file: ~/.config/wigix/ha_token
#   prom:
#     type: prometheus
#     url: http://prometheus.local:9090
#   docker:
#     type: docker

pages:
  - name: Overview
    grid: {cols: 4, rows: 3, gap: 10}
    widgets:
      - {type: clock, at: [0, 0, 2, 1]}
      - {type: system, at: [0, 1, 2, 2]}
      - {type: checks, title: Services, at: [2, 0, 2, 3], checks: [
          {name: "Example", http: "https://example.com"},
          {name: "Local SSH", tcp: "127.0.0.1:22"}]}

  - name: Notes
    grid: {cols: 4, rows: 3, gap: 10}
    widgets:
      - {type: text, text: "hello from wigix", at: [0, 0, 4, 1]}
      - {type: text, text: "edit me in the web editor", at: [0, 1, 2, 2]}
      # - {type: ha_entity, entity: sensor.outdoor_temperature, at: [2, 1, 1, 1]}
      # - {type: ha_toggle, entity: light.desk, at: [3, 1, 1, 1]}
      # - {type: ha_alerts, at: [2, 2, 2, 1]}
'''


def write_config(out: Path, force: bool = False) -> str:
    """Write the starter config to ``out``. Returns a status line. Raises FileExistsError without ``force``."""
    out = Path(out).expanduser()
    msg = f"wrote {out}"
    if out.exists():
        if not force:
            raise FileExistsError(f"{out} already exists; use --force to replace it (a .bak copy is kept)")
        bak = out.with_name(out.name + ".bak")
        shutil.copyfile(out, bak)
        msg += f" (previous version saved as {bak})"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(STARTER_CONFIG)
    return msg


def udev_instructions() -> str:
    cmds = "\n".join(f"  sudo {c}" for c in UDEV_RELOAD)
    return (f"Put this in {UDEV_PATH}:\n\n{UDEV_RULE}\n"
            f"Or in one go:\n  sudo tee {UDEV_PATH} <<'EOF'\n{UDEV_RULE}EOF\n\n"
            f"then reload the rules:\n{cmds}\n\n"
            "Unplug and replug the panel once (or run the trigger line above).\n"
            "The same commands work on Arch, Debian/Ubuntu and Fedora. uaccess needs systemd-logind; "
            "on a system without it, add your user to a group and use GROUP=\"<group>\" in the rule instead.")


def appimage_path() -> str | None:
    """The .AppImage file this process runs from, or None. (Its mount point vanishes on exit; the file stays.)"""
    return os.environ.get("APPIMAGE") or None


def exec_start() -> str:
    """ExecStart for the current install: the AppImage file, else the `wigix` on PATH, else this interpreter."""
    appimage = appimage_path()
    if appimage:
        # systemd expands % specifiers and $ variables even inside quotes
        exe = appimage.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
        return f'"{exe}" run -c %h/.config/wigix/dashboard.yaml'
    exe = shutil.which("wigix")
    if exe:
        return f'"{exe}" run -c %h/.config/wigix/dashboard.yaml'
    return f'"{sys.executable}" -m wigix.cli run -c %h/.config/wigix/dashboard.yaml'


def unit_text() -> str:
    return f"""\
[Unit]
Description=Wigix dashboard
After=network-online.target

[Service]
ExecStart={exec_start()}
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""


def install_unit(unit_dir: Path = UNIT_DIR, force: bool = False) -> Path:
    path = Path(unit_dir) / "wigix.service"
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; use --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text())
    return path


def tray_desktop_text() -> str:
    """The autostart entry (contrib/wigix-tray.desktop), with Exec pointing at this install."""
    appimage = appimage_path()
    if appimage:
        # desktop entry quoting: backslash-escape \ " ` $ inside quotes; % is a field code, so double it
        exe = "".join("\\" + c if c in '\\"`$' else c for c in appimage).replace("%", "%%")
        cmd = f'"{exe}" tray'
    else:
        exe = shutil.which("wigix")
        cmd = f'"{exe}" tray' if exe else f'"{sys.executable}" -m wigix.cli tray'
    return f"""\
[Desktop Entry]
Type=Application
Name=wigix tray
Comment=System tray icon for the wigix dashboard
Exec={cmd}
Icon=wigix
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
"""


def install_tray_autostart(autostart_dir: Path = AUTOSTART_DIR, force: bool = False) -> Path:
    path = Path(autostart_dir) / "wigix-tray.desktop"
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; use --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tray_desktop_text())
    return path


def enable_unit() -> int:
    """Reload systemd and enable --now the user unit. Returns the exit status."""
    for cmd in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", "wigix"]):
        try:
            r = subprocess.run(cmd, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"could not run {' '.join(cmd)}: {e}", file=sys.stderr)
            return 1
        if r.returncode:
            return r.returncode
    return 0


def run(out=None, force=False, udev=False, systemd=False, enable=False, tray=False) -> int:
    """Entry point for the CLI. Returns the process exit status.

    With no flags it writes the starter config. ``--udev`` / ``--systemd`` / ``--enable`` add their steps;
    the config is then only written if it does not exist yet (or ``--out`` / ``--force`` was given).
    """
    extras = udev or systemd or enable or tray
    if udev:
        print(udev_instructions())
    if systemd or enable:
        try:
            print(f"installed {install_unit(force=force)}")
        except FileExistsError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if not enable:
            print("start it at login with:\n  systemctl --user daemon-reload\n"
                  "  systemctl --user enable --now wigix")
    if tray:
        try:
            print(f"installed {install_tray_autostart(force=force)} (the tray starts at your next login; "
                  "needs `pip install 'wigix[tray]'`)")
        except FileExistsError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    target = Path(out).expanduser() if out else DEFAULT_CONFIG
    if extras and not out and not force and target.exists():
        print(f"keeping existing {target}")
    else:
        try:
            print(write_config(target, force))
        except FileExistsError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"next: wigix doctor -c {target}   then   wigix run -c {target}")
    if enable:
        return enable_unit()
    return 0
