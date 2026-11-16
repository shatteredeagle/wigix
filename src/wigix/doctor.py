"""`wigix doctor`: a checklist that says what is wrong with an install and how to fix it.

Every check is a small function returning a :class:`Result` (or a list of them), so each can be tested
with monkeypatching. Nothing here may hang or raise when hardware or tools are missing.
"""
from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .init import DEFAULT_CONFIG, UDEV_PATH, UDEV_RELOAD, UDEV_RULE

VID, PID_NORMAL, PID_UPDATE = 0x28DA, 0xEF01, 0xEF02
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# tool -> what needs it
OPTIONAL_TOOLS = {
    "playerctl": "media_player",
    "parec": "audio_visualizer",
    "ffmpeg": "video + camera",
    "wtype": "key actions (Wayland)",
    "xdotool": "key actions (X11)",
    "ydotool": "key actions (Wayland, alternative)",
}


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""
    fix: str = ""


# ---------------------------------------------------------------- environment

def check_python() -> Result:
    v = sys.version_info
    ver = ".".join(str(n) for n in v[:3])
    if v < (3, 10):
        return Result("Python", FAIL, f"{ver}, need 3.10 or newer", "install a newer Python (3.10+) and reinstall wigix")
    return Result("Python", PASS, ver)


def check_pyusb() -> Result:
    try:
        import usb.core  # noqa: F401
    except ImportError as e:
        return Result("pyusb", FAIL, str(e), "pip install pyusb   (Arch: pacman -S python-pyusb)")
    return Result("pyusb", PASS, "importable")


def check_libusb() -> Result:
    try:
        import usb.backend.libusb1 as libusb1
        backend = libusb1.get_backend()
    except Exception as e:
        return Result("libusb", FAIL, f"pyusb backend failed: {e}", _LIBUSB_FIX)
    if backend is None:
        return Result("libusb", FAIL, "no libusb 1.x library found", _LIBUSB_FIX)
    return Result("libusb", PASS, "libusb 1.x backend found")


_LIBUSB_FIX = "install libusb: Arch `pacman -S libusb`, Debian/Ubuntu `apt install libusb-1.0-0`, Fedora `dnf install libusb1`"


def check_fonts() -> Result:
    try:
        from PIL import ImageFont  # noqa: F401
        from .render import font
        f = font(20)
    except ImportError as e:
        return Result("Pillow fonts", FAIL, str(e), "pip install pillow   (Arch: pacman -S python-pillow)")
    except Exception as e:
        return Result("Pillow fonts", WARN, f"font lookup failed: {e}", _FONT_FIX)
    path = getattr(f, "path", "")
    if path and "DejaVu" in str(path):
        return Result("Pillow fonts", PASS, f"DejaVu Sans Mono ({path})")
    if path:
        return Result("Pillow fonts", PASS, str(path))
    return Result("Pillow fonts", WARN, "DejaVu Sans Mono not found; falling back to Pillow's default font", _FONT_FIX)


_FONT_FIX = "install DejaVu: Arch `pacman -S ttf-dejavu`, Debian/Ubuntu `apt install fonts-dejavu-core`, Fedora `dnf install dejavu-sans-mono-fonts`"


# ---------------------------------------------------------------- device

def find_devices() -> list[tuple[int, int]]:
    """(product id, bus*1000+address) for every matching USB device. Empty when pyusb/libusb is unusable."""
    try:
        import usb.core
        return [(d.idProduct, d.bus * 1000 + d.address) for d in usb.core.find(find_all=True, idVendor=VID) or []
                if d.idProduct in (PID_NORMAL, PID_UPDATE)]
    except Exception:
        return []


def check_device_present(found: list[tuple[int, int]] | None = None) -> Result:
    found = find_devices() if found is None else found
    pids = {pid for pid, _ in found}
    if PID_NORMAL in pids:
        return Result("WigiDash present", PASS, "28da:ef01 (normal mode)")
    if PID_UPDATE in pids:
        return Result("WigiDash present", FAIL, "28da:ef02: the panel is in update mode (bootloader)",
                      "finish or cancel the update with the vendor tool, or unplug and replug the USB cable; "
                      "wigix does not flash firmware")
    return Result("WigiDash present", FAIL, "no 28da:ef01 device on the USB bus",
                  "plug in the panel with a data-capable cable, directly or through a powered hub, then check "
                  "`lsusb | grep 28da`; if it stays missing, unplug and replug it")


@dataclass
class Probe:
    """What happened when we tried to open the panel and run the startup handshake."""
    error: Exception | None = None
    firmware: str = ""
    name: str = ""
    brightness: int | None = None
    attempted: bool = False


def probe_device(timeout: float = 15.0) -> Probe:
    """Open the panel and read firmware/brightness, in a thread so a stuck transfer cannot hang us."""
    p = Probe(attempted=True)

    def work():
        try:
            from .device import WigiDash
            w = WigiDash()
            try:
                info = w.info()
                p.firmware, p.name, p.brightness = info["firmware"], info["name"], w.brightness
            finally:
                try:
                    import usb.util
                    usb.util.dispose_resources(w.dev)
                except Exception:
                    pass
        except Exception as e:
            p.error = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        p.error = TimeoutError(f"no answer from the panel after {timeout:.0f}s")
    return p


def _errno(e: Exception | None) -> int | None:
    return getattr(e, "errno", None)


def check_permission(probe: Probe, present: bool = True) -> Result:
    if not present or not probe.attempted:
        return Result("permission to open", WARN, "skipped: no device to open")
    if _errno(probe.error) in (errno.EACCES, errno.EPERM):
        return Result("permission to open", FAIL, "permission denied opening the USB device",
                      "install the udev rule, then replug the panel:\n"
                      f"{_indent(UDEV_RULE)}\n"
                      f"      sudo tee {UDEV_PATH}   (paste the rule above)\n"
                      + "".join(f"      sudo {c}\n" for c in UDEV_RELOAD)
                      + "    `wigix init --udev` prints all of this. Same on Arch, Debian/Ubuntu and Fedora.")
    return Result("permission to open", PASS, "access OK" if probe.error is None else "not denied")


def check_busy(probe: Probe, present: bool = True) -> Result:
    if not present or not probe.attempted:
        return Result("device free", WARN, "skipped: no device to open")
    if _errno(probe.error) == errno.EBUSY:
        return Result("device free", FAIL, "device busy (EBUSY): another process holds the panel",
                      "stop the other user, usually the service: `systemctl --user stop wigix`; "
                      "also close the vendor software and any other `wigix run`")
    return Result("device free", PASS, "no other process holds it")


def check_handshake(probe: Probe, present: bool = True) -> Result:
    if not present or not probe.attempted:
        return Result("handshake", WARN, "skipped: no device to talk to")
    e = probe.error
    if e is None:
        bright = "?" if probe.brightness is None else f"{probe.brightness}%"
        return Result("handshake", PASS, f"firmware {probe.firmware}, brightness {bright}")
    if _errno(e) in (errno.EACCES, errno.EPERM, errno.EBUSY):
        return Result("handshake", WARN, "skipped: see the checks above")
    return Result("handshake", FAIL, f"{type(e).__name__}: {e}",
                  "unplug and replug the USB cable (the panel can stay on its boot logo after the vendor software "
                  "or a firmware flash), then run `wigix doctor` again")


# ---------------------------------------------------------------- config

def _token_files(node, out: list[str]):
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and k.endswith("token_file") and isinstance(v, str):
                out.append(v)
            else:
                _token_files(v, out)
    elif isinstance(node, list):
        for v in node:
            _token_files(v, out)


def check_config(path: str | Path) -> tuple[Result, dict | None]:
    """Load and build the config exactly as `wigix run` does. Returns (result, cfg or None)."""
    path = Path(path).expanduser()
    if not path.exists():
        return Result("config", FAIL, f"{path} not found",
                      f"create a starter config with `wigix init`, or pass -c <file>"), None
    try:
        from .engine import Dashboard, load_config
        cfg = load_config(path)
        Dashboard(cfg, str(path))
    except Exception as e:
        return Result("config", FAIL, f"{path}: {type(e).__name__}: {e}",
                      "fix the line named above, or check it in the web editor; "
                      "`wigix preview -c <file>` re-runs this without hardware"), None
    pages = len(cfg.get("pages", []))
    return Result("config", PASS, f"{path} ({pages} page{'s' if pages != 1 else ''})"), cfg


def check_tokens(cfg: dict | None) -> list[Result]:
    if not cfg:
        return []
    files: list[str] = []
    _token_files(cfg, files)
    out = []
    for f in sorted(set(files)):
        p = Path(os.path.expanduser(f))
        try:
            mode = stat.S_IMODE(p.stat().st_mode)
        except OSError:
            out.append(Result(f"token file {f}", FAIL, "missing or unreadable",
                              f"create it with the secret inside, then `chmod 600 {f}`"))
            continue
        if mode & 0o077:
            out.append(Result(f"token file {f}", WARN, f"mode {mode:04o}, readable by others",
                              f"chmod 600 {f}"))
        else:
            out.append(Result(f"token file {f}", PASS, f"mode {mode:04o}"))
    return out


# ---------------------------------------------------------------- systemd

def _systemctl(*args: str) -> tuple[int, str] | None:
    exe = shutil.which("systemctl")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--user", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.returncode, (r.stdout or r.stderr).strip()


def check_service() -> Result:
    st = _systemctl("is-enabled", "wigix")
    if st is None:
        return Result("systemd user unit", WARN, "systemctl --user not available (fine if you start wigix by hand)")
    code, text = st
    if text.startswith("Failed to connect") or "No medium found" in text:
        return Result("systemd user unit", WARN, "no systemd user session reachable")
    if "not-found" in text or "No such file" in text or text == "":
        return Result("systemd user unit", WARN, "wigix.service is not installed",
                      "`wigix init --systemd` installs it, then `systemctl --user enable --now wigix`")
    enabled = code == 0
    act = _systemctl("is-active", "wigix")
    active = act is not None and act[0] == 0
    detail = f"{'enabled' if enabled else text}, {'active' if active else 'not active'}"
    if enabled and active:
        return Result("systemd user unit", PASS, detail)
    return Result("systemd user unit", WARN, detail, "`systemctl --user enable --now wigix`")


# ---------------------------------------------------------------- optional tools

def check_optional_tools() -> list[Result]:
    out = []
    for tool, feature in OPTIONAL_TOOLS.items():
        if shutil.which(tool):
            out.append(Result(f"optional: {tool}", PASS, f"found (needed for: {feature})"))
        else:
            out.append(Result(f"optional: {tool}", WARN, f"not found (needed for: {feature})",
                              "only install it if you use that feature"))
    return out


# ---------------------------------------------------------------- driver

def _indent(text: str) -> str:
    return "".join(f"      {ln}\n" for ln in text.splitlines()).rstrip("\n")


def run_checks(config: str | Path = DEFAULT_CONFIG) -> list[Result]:
    results = [check_python(), check_pyusb(), check_libusb(), check_fonts()]
    usb_ok = all(r.status != FAIL for r in results[1:3])
    found = find_devices() if usb_ok else []
    present = check_device_present(found)
    results.append(present)
    probe = probe_device() if present.status == PASS and usb_ok else Probe()
    ok = present.status == PASS
    results += [check_permission(probe, ok), check_busy(probe, ok), check_handshake(probe, ok)]
    cfg_result, cfg = check_config(config)
    results.append(cfg_result)
    results += check_tokens(cfg)
    results.append(check_service())
    results += check_optional_tools()
    return results


def format_text(results: list[Result], color: bool = False) -> str:
    colors = {PASS: "32", WARN: "33", FAIL: "31"}
    lines = []
    for r in results:
        tag = f"\033[{colors[r.status]}m{r.status}\033[0m" if color else r.status
        lines.append(f"[{tag}] {r.name}: {r.detail}" if r.detail else f"[{tag}] {r.name}")
        if r.fix:
            lines.append(f"       fix: {r.fix}")
    n = {s: sum(r.status == s for r in results) for s in (PASS, WARN, FAIL)}
    lines.append(f"\n{n[PASS]} passed, {n[WARN]} warnings, {n[FAIL]} failed")
    return "\n".join(lines)


def to_json(results: list[Result]) -> str:
    return json.dumps({"ok": not any(r.status == FAIL for r in results),
                       "results": [asdict(r) for r in results]}, indent=2)


def run(config=DEFAULT_CONFIG, as_json: bool = False) -> int:
    """Run all checks, print them, and return the exit status (1 when anything FAILed)."""
    results = run_checks(config)
    print(to_json(results) if as_json else format_text(results, color=sys.stdout.isatty()))
    return 1 if any(r.status == FAIL for r in results) else 0
