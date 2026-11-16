# SPDX-License-Identifier: GPL-3.0-only
import argparse
import logging
import sys

from .engine import Dashboard, load_config
from .init import DEFAULT_CONFIG


def _version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("wigix")
        except PackageNotFoundError:
            pass
    except ImportError:
        pass
    return "unknown (not installed)"


def _devices() -> int:
    from .doctor import PID_NORMAL, PID_UPDATE, find_devices
    found = find_devices()
    if not found:
        print("no WigiDash found (looking for USB 28da:ef01 / 28da:ef02); try `wigix doctor`")
        return 1
    for pid, loc in found:
        mode = "normal" if pid == PID_NORMAL else "update mode (bootloader)" if pid == PID_UPDATE else "?"
        print(f"28da:{pid:04x}  bus {loc // 1000} address {loc % 1000}  {mode}")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="wigix")
    ap.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the dashboard on the panel")
    run.add_argument("-c", "--config", required=True)
    pv = sub.add_parser("preview", help="render page 1 to a PNG, no hardware needed")
    pv.add_argument("-c", "--config", required=True)
    pv.add_argument("-o", "--out", default="preview.png")
    ed = sub.add_parser("editor", help="open the web editor (the running daemon hot-reloads saved changes)")
    ed.add_argument("-c", "--config", required=True)
    ed.add_argument("--host", default="127.0.0.1")
    ed.add_argument("--port", type=int, default=8765)
    dr = sub.add_parser("doctor", help="check the install, the USB device and the config; say how to fix problems")
    dr.add_argument("-c", "--config", default=str(DEFAULT_CONFIG))
    dr.add_argument("--json", action="store_true", help="machine-readable output")
    it = sub.add_parser("init", help="write a starter config (and optionally the udev rule / systemd unit)")
    it.add_argument("-o", "--out", help=f"config path (default {DEFAULT_CONFIG})")
    it.add_argument("--force", action="store_true", help="overwrite existing files (config keeps a .bak)")
    it.add_argument("--udev", action="store_true", help="print the udev rule and the commands to install it")
    it.add_argument("--systemd", action="store_true", help="install the systemd user unit")
    it.add_argument("--tray", action="store_true", help="install the tray autostart entry (~/.config/autostart)")
    it.add_argument("--enable", action="store_true", help="with --systemd: also enable and start it")
    tr = sub.add_parser("tray", help="system tray icon for the running daemon (needs PyQt6: pip install 'wigix[tray]')")
    tr.add_argument("-c", "--config", default=str(DEFAULT_CONFIG))
    tr.add_argument("--quiet", action="store_true", help="no desktop notifications when the state turns bad")
    sub.add_parser("devices", help="list matching USB devices")
    au = sub.add_parser("autostart", help="start wigix at login (and optionally at boot): enable | disable | status")
    au.add_argument("action", choices=["enable", "disable", "status"])
    au.add_argument("-c", "--config", default=str(DEFAULT_CONFIG))
    au.add_argument("--boot", action="store_true", help="also start at boot, before login (systemd linger)")
    tg = au.add_mutually_exclusive_group()
    tg.add_argument("--tray", dest="tray", action="store_true", default=None, help="also start the tray icon")
    tg.add_argument("--no-tray", dest="tray", action="store_false", help="do not set up the tray icon")
    ur = sub.add_parser("url", help="print the web editor's access link (contains a secret token)")
    ur.add_argument("-c", "--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if a.cmd == "doctor":
        from .doctor import run as doctor
        sys.exit(doctor(a.config, a.json))
    if a.cmd == "init":
        from .init import run as init
        sys.exit(init(a.out, a.force, a.udev, a.systemd, a.enable, a.tray))
    if a.cmd == "tray":
        from .tray import main as tray
        sys.exit(tray(a.config, a.quiet))
    if a.cmd == "devices":
        sys.exit(_devices())
    if a.cmd == "autostart":
        from .autostart import main as autostart
        sys.exit(autostart(a.action, a.config, a.boot, a.tray))
    if a.cmd == "url":
        from .editor import editor_url
        ecfg = load_config(a.config).get("editor") or {}
        print(editor_url(a.config, ecfg.get("host", "127.0.0.1"), int(ecfg.get("port", 8765))))
        return
    if a.cmd == "editor":
        from .editor import serve
        serve(a.config, a.host, a.port, announce=True)
        return
    try:
        dash = Dashboard(load_config(a.config), a.config)
    except (ValueError, KeyError) as e:
        raise SystemExit(f"config error: {e}")
    if a.cmd == "run":
        try:
            dash.run()
        except KeyboardInterrupt:
            pass
    else:
        dash.preview(a.out)
        print("wrote", a.out)


if __name__ == "__main__":
    main()
