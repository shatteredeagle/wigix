# SPDX-License-Identifier: GPL-3.0-only
"""One-command launch at login (and optionally at boot): `wigix autostart enable|disable|status`.

Installs the systemd user unit for the daemon (and enables it right away), adds the tray's autostart entry when
PyQt6 is available, and with `--boot` turns on systemd "linger" so the daemon starts when the machine boots, before
anyone logs in. Everything it writes is per-user (nothing needs root).
"""
from __future__ import annotations

import getpass
import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import init

UNIT = "wigix.service"


def _run(*cmd: str, timeout: float = 20) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def have_systemctl() -> bool:
    return shutil.which("systemctl") is not None


def have_pyqt() -> bool:
    return importlib.util.find_spec("PyQt6") is not None


@dataclass
class Status:
    unit_installed: bool
    unit_enabled: bool
    unit_active: bool
    tray_installed: bool
    linger: bool | None            # None: cannot tell
    systemd: bool

    def lines(self) -> list[str]:
        yn = lambda b: "yes" if b else "no"                     # noqa: E731
        out = [f"daemon unit installed : {yn(self.unit_installed)}",
               f"starts at login       : {yn(self.unit_enabled)}",
               f"running now           : {yn(self.unit_active)}",
               f"tray autostart        : {yn(self.tray_installed)}",
               "starts at boot        : " + ("unknown" if self.linger is None else yn(self.linger))]
        if not self.systemd:
            out.append("systemctl not found: only the tray autostart entry can be managed here")
        return out


def status(unit_dir: Path = init.UNIT_DIR, autostart_dir: Path = init.AUTOSTART_DIR) -> Status:
    enabled = active = False
    linger = None
    if have_systemctl():
        r = _run("systemctl", "--user", "is-enabled", UNIT)
        enabled = bool(r and r.returncode == 0 and r.stdout.strip() in ("enabled", "enabled-runtime"))
        r = _run("systemctl", "--user", "is-active", UNIT)
        active = bool(r and r.returncode == 0 and r.stdout.strip() == "active")
        r = _run("loginctl", "show-user", getpass.getuser(), "-p", "Linger", "--value")
        if r and r.returncode == 0:
            linger = r.stdout.strip() == "yes"
    return Status((Path(unit_dir) / UNIT).exists(), enabled, active,
                  (Path(autostart_dir) / "wigix-tray.desktop").exists(), linger, have_systemctl())


def daemon_enabled() -> bool | None:
    """Is the daemon set to start at login? None when systemd is not available."""
    if not have_systemctl():
        return None
    r = _run("systemctl", "--user", "is-enabled", UNIT)
    return bool(r and r.returncode == 0 and r.stdout.strip() in ("enabled", "enabled-runtime"))


def enable(config: str | None = None, boot: bool = False, tray: bool | None = None,
           unit_dir: Path = init.UNIT_DIR, autostart_dir: Path = init.AUTOSTART_DIR, say=print) -> int:
    """Install and enable everything. ``tray``: True/False to force, None = only if PyQt6 is installed."""
    if not have_systemctl():
        say("systemctl not found: this system does not use systemd, so the daemon cannot be set up here.")
        say("Start it yourself from your session's autostart with:  " + init.exec_start().replace("%h", str(Path.home())))
        return 1
    cfg = Path(config).expanduser() if config else init.DEFAULT_CONFIG
    if not cfg.exists():
        say(f"no config at {cfg}: run `wigix init` first (or pass -c).")
        return 1
    path = Path(unit_dir) / UNIT
    path.parent.mkdir(parents=True, exist_ok=True)
    text = init.unit_text().replace("%h/.config/wigix/dashboard.yaml", _unit_path(cfg))
    path.write_text(text)
    say(f"wrote {path}")
    if init.enable_unit() != 0:
        say("systemctl could not enable the unit; see `systemctl --user status wigix`")
        return 1
    say("daemon enabled and started (it now starts at every login)")
    if tray is None:
        tray = have_pyqt()
    if tray:
        say(f"wrote {init.install_tray_autostart(autostart_dir, force=True)}  (tray starts at login)")
    else:
        say("tray not set up (PyQt6 not installed: `pip install 'wigix[tray]'`, then run this again)")
    if boot:
        r = _run("loginctl", "enable-linger", getpass.getuser())
        if r and r.returncode == 0:
            say("boot start enabled (systemd linger): the daemon will start when the machine boots")
        else:
            say(f"could not enable linger; run once as root:  sudo loginctl enable-linger {getpass.getuser()}")
    elif status(unit_dir, autostart_dir).linger is False:
        say("note: it starts when you log in. For start-at-boot (before login) add --boot.")
    return 0


def _unit_path(cfg: Path) -> str:
    """The config path as written in the unit: %h-relative when it lives under the home directory."""
    try:
        return "%h/" + str(cfg.resolve().relative_to(Path.home()))
    except ValueError:
        return str(cfg.resolve())


def disable(boot: bool = False, unit_dir: Path = init.UNIT_DIR, autostart_dir: Path = init.AUTOSTART_DIR,
            say=print, stop_now: bool = True) -> int:
    """Stop launching at login. ``stop_now=False`` leaves the running daemon alone (the tray's toggle uses it)."""
    rc = 0
    if have_systemctl():
        r = _run("systemctl", "--user", "disable", *(["--now"] if stop_now else []), UNIT)
        say(("daemon stopped and no longer starts at login" if stop_now else "daemon no longer starts at login")
            if r and r.returncode == 0 else "daemon was not enabled (or systemctl failed)")
        if boot:
            r = _run("loginctl", "disable-linger", getpass.getuser())
            say("boot start disabled" if r and r.returncode == 0 else
                f"could not disable linger; run: sudo loginctl disable-linger {getpass.getuser()}")
    tray = Path(autostart_dir) / "wigix-tray.desktop"
    if tray.exists():
        tray.unlink()
        say(f"removed {tray}")
    return rc


def main(action: str, config: str | None = None, boot: bool = False, tray: bool | None = None) -> int:
    if action == "status":
        print("\n".join(status().lines()))
        return 0
    if action == "enable":
        return enable(config, boot, tray)
    if action == "disable":
        return disable(boot)
    raise SystemExit(f"unknown action {action!r}")
