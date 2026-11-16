# SPDX-License-Identifier: GPL-3.0-only
"""AppImage launcher: run the `wigix` CLI with the bundled libusb.

pyusb looks libusb up through ctypes.util.find_library, which only sees libraries the host registered with ldconfig.
Point it at the copy in ``lib/`` first, so the AppImage works on a machine without libusb installed.
"""
import ctypes.util
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent / "lib"

_find_library = ctypes.util.find_library


def find_library(name):
    if name in ("usb-1.0", "libusb-1.0", "usb"):
        bundled = LIB_DIR / "libusb-1.0.so.0"
        if bundled.exists():
            return str(bundled)
    return _find_library(name)


ctypes.util.find_library = find_library

from wigix.cli import main  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) == 1:      # double-clicked from a file manager: show what the command can do
        sys.argv.append("--help")
    sys.argv[0] = "wigix"
    main()
