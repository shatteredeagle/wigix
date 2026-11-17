# SPDX-License-Identifier: GPL-3.0-only
"""`wigix tray`: a system tray icon for the running daemon (PyQt6, optional).

The first half of this module is Qt-free: ``ControlClient`` talks to the daemon's editor API, and
``tray_state`` / ``tooltip`` / ``build_menu`` turn a status snapshot into what the tray shows. The second half is the
thin Qt shell around them. PyQt6 is imported lazily so the rest of the package works without it.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

log = logging.getLogger("wigix.tray")

STATES = ("connected", "disconnected", "paused", "attention")
ICON_SIZES = (16, 24, 32, 48, 64, 72, 96)
BRIGHTNESS_PRESETS = (10, 25, 50, 75, 100)
POLL_MS = 2000
TIMEOUT = 2.0
MAX_TOOLTIP_NAMES = 3
TRAY_WAIT_SECONDS = 30
INSTALL_HINT = "wigix tray needs PyQt6: pip install 'wigix[tray]'"


# --- talking to the daemon (no Qt) ---------------------------------------------------------------
class ControlClient:
    """Calls the editor's status/control API. Every method returns None on any failure, so the UI never sees an exception."""

    def __init__(self, base_url: str, token_fn, timeout: float = TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self._token_fn = token_fn if callable(token_fn) else (lambda: token_fn)
        self.timeout = timeout

    @classmethod
    def from_config(cls, config_path) -> "ControlClient":
        """Client for the daemon that uses ``config_path`` (host/port from its ``editor:`` block, token from editor_token)."""
        from .editor import load_token
        from .engine import load_config
        try:
            ecfg = load_config(config_path).get("editor") or {}
        except Exception as e:                          # unreadable config: fall back to the defaults
            log.debug("could not read %s: %s", config_path, e)
            ecfg = {}
        host = ecfg.get("host") or "127.0.0.1"
        host = "localhost" if host in ("0.0.0.0", "::") else host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = int(ecfg.get("port") or 8765)
        return cls(f"http://{host}:{port}", lambda: load_token(config_path, create=False))

    def _request(self, path: str, body: dict | None = None) -> dict | None:
        token = self._token_fn()
        if not token:
            log.debug("no editor token yet")
            return None
        headers = {"X-Wigix-Token": token}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.update({"X-Requested-With": "wigix", "Content-Type": "application/json"})
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers,
                                     method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                out = json.loads(resp.read())
        except (OSError, ValueError, urllib.error.URLError) as e:   # HTTPError, timeouts and bad JSON all land here
            log.debug("%s failed: %s", path, e)
            return None
        return out if isinstance(out, dict) else None

    def status(self) -> dict | None:
        out = self._request("/api/status")
        log.debug("status: %s", "unreachable" if out is None else f"page={out.get('page_name')!r}")
        return out

    def control(self, action: str, **params) -> dict | None:
        return self._request("/api/control", {"action": action, **params})


# --- what the tray shows (no Qt) -----------------------------------------------------------------
def failing_integrations(status: dict | None) -> list[str]:
    ints = (status or {}).get("integrations") or {}
    return sorted(n for n, i in ints.items() if isinstance(i, dict) and i.get("error"))


def tray_state(status: dict | None) -> str:
    """One of STATES. Unreachable daemon and detached panel both read as disconnected."""
    if not status or not status.get("connected"):
        return "disconnected"
    if status.get("paused"):
        return "paused"
    if failing_integrations(status):
        return "attention"
    return "connected"


def tooltip(status: dict | None) -> str:
    if not status:
        return "wigix\nDaemon not reachable"
    lines = ["wigix", "Panel: " + ("connected" if status.get("connected") else "not connected")]
    if status.get("page_name"):
        lines.append(f"Page: {status['page_name']}")
    if status.get("paused"):
        lines.append("Display paused")
    bad = failing_integrations(status)
    if bad:
        shown = ", ".join(bad[:MAX_TOOLTIP_NAMES])
        more = len(bad) - MAX_TOOLTIP_NAMES
        lines.append(f"Errors: {shown}" + (f" (+{more} more)" if more > 0 else ""))
    return "\n".join(lines)


def nearest_preset(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return min(BRIGHTNESS_PRESETS, key=lambda p: abs(p - value))


@dataclass(frozen=True)
class MenuItem:
    """One menu entry. ``kind`` is "action", "label" (disabled text), "submenu" or "separator"."""
    kind: str
    text: str = ""
    key: str = ""                       # action id, see ``control_for``
    checked: bool | None = None         # None = not checkable
    children: tuple["MenuItem", ...] = ()


def status_line(status: dict | None) -> str:
    if not status:
        return "Daemon not reachable"
    if not status.get("connected"):
        return "Panel not connected"
    if status.get("paused"):
        return "Display paused"
    return f"Panel connected: {status.get('page_name', '')}".rstrip(": ")


def build_menu(status: dict | None, has_systemctl: bool = False, autostart: bool | None = None) -> tuple[MenuItem, ...]:
    """The menu for a status snapshot; equal specs mean the Qt menu does not need rebuilding."""
    items = [MenuItem("label", status_line(status)), MenuItem("action", "Open editor", "open_editor")]
    if status:
        pages = list(status.get("pages") or [])
        if pages:
            current = status.get("page_name")
            kids = [MenuItem("action", name, f"page:{name}", name == current) for name in pages]
            kids += [MenuItem("separator"), MenuItem("action", "Next page", "next_page")]
            items.append(MenuItem("submenu", "Page", children=tuple(kids)))
        near = nearest_preset(status.get("brightness"))
        items.append(MenuItem("submenu", "Brightness", children=tuple(
            MenuItem("action", f"{p} %", f"brightness:{p}", p == near) for p in BRIGHTNESS_PRESETS)))
        if "rotate" in status:
            items.append(MenuItem("action", "Auto-rotate pages", "toggle_rotate", bool(status["rotate"])))
        if "screensaver" in status:
            items.append(MenuItem("action", "Screensaver and dimming", "toggle_screensaver", bool(status["screensaver"])))
        paused = bool(status.get("paused"))
        items.append(MenuItem("action", "Resume display" if paused else "Pause display",
                              "resume" if paused else "pause"))
    items.append(MenuItem("separator"))
    if has_systemctl:
        items.append(MenuItem("action", "Restart daemon", "restart_daemon"))
        if autostart is not None:
            items.append(MenuItem("action", "Start at login", "toggle_autostart", autostart))
    items.append(MenuItem("action", "Quit tray", "quit"))
    return tuple(items)


def control_for(key: str) -> tuple[str, dict] | None:
    """(action, params) for /api/control, or None if the menu key is not a control action."""
    if key.startswith("page:"):
        return "page", {"name": key[5:]}
    if key.startswith("brightness:"):
        return "brightness", {"value": int(key[11:])}
    if key in ("toggle_rotate", "toggle_screensaver"):
        return key[7:], {}
    if key in ("next_page", "prev_page", "pause", "resume", "toggle_pause", "wake"):
        return key, {}
    return None


def should_notify(old: str | None, new: str) -> bool:
    """Notify when the state turns bad (not for the very first reading)."""
    return old is not None and old != new and new in ("attention", "disconnected")


# --- icons ---------------------------------------------------------------------------------------
def icon_resource(state: str, size: int):
    """The packaged PNG for a state and size, or None if it is not shipped."""
    res = resources.files("wigix").joinpath("tray_icons", f"wigix-{state}-{size}.png")
    return res if res.is_file() else None


def icon_sizes(state: str) -> list[int]:
    return [s for s in ICON_SIZES if icon_resource(state, s) is not None]


# --- Qt shell ------------------------------------------------------------------------------------
def populate_menu(menu, items, on_action) -> None:
    """Fill a QMenu from a menu spec. ``on_action(key)`` is called when an action is triggered."""
    from PyQt6.QtGui import QAction
    for it in items:
        if it.kind == "separator":
            menu.addSeparator()
        elif it.kind == "label":
            act = QAction(it.text, menu)
            act.setEnabled(False)
            menu.addAction(act)
        elif it.kind == "submenu":
            populate_menu(menu.addMenu(it.text), it.children, on_action)
        else:
            act = QAction(it.text, menu)
            if it.checked is not None:
                act.setCheckable(True)
                act.setChecked(it.checked)
            act.triggered.connect(lambda _=False, k=it.key: on_action(k))
            menu.addAction(act)


def _qt():
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets
    except ImportError:
        print(INSTALL_HINT, file=sys.stderr)
        raise SystemExit(1)
    return QtCore, QtGui, QtWidgets


def load_icons(QtGui) -> dict:
    """QIcon per state holding every packaged size."""
    icons = {}
    for state in STATES:
        icon = QtGui.QIcon()
        for size in icon_sizes(state):
            pm = QtGui.QPixmap()
            if pm.loadFromData(icon_resource(state, size).read_bytes()):
                icon.addPixmap(pm)
        icons[state] = icon
    return icons


def main(config_path, quiet: bool = False) -> int:
    QtCore, QtGui, QtWidgets = _qt()
    from .editor import editor_url

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("wigix")
    app.setDesktopFileName("wigix")
    app.setQuitOnLastWindowClosed(False)

    lock_dir = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.RuntimeLocation) \
        or QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.TempLocation)
    lock = QtCore.QLockFile(str(Path(lock_dir) / "wigix-tray.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        print("wigix tray is already running", file=sys.stderr)
        return 0

    # The panel (and the desktop's tray host) may not be up yet at login: wait a while before giving up.
    deadline = time.monotonic() + TRAY_WAIT_SECONDS
    while not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        if time.monotonic() > deadline:
            print("wigix tray: no system tray found. On GNOME install the AppIndicator extension "
                  "(see docs/tray.md).", file=sys.stderr)
            return 1
        app.processEvents()
        time.sleep(1)

    client = ControlClient.from_config(config_path)
    icons = load_icons(QtGui)
    has_systemctl = shutil.which("systemctl") is not None
    tray = QtWidgets.QSystemTrayIcon(icons["disconnected"])
    menu = QtWidgets.QMenu()
    tray.setContextMenu(menu)
    from . import autostart as _autostart
    ui = {"spec": None, "state": None, "busy": False, "status": None, "autostart": _autostart.daemon_enabled()}

    class Result(QtCore.QObject):
        done = QtCore.pyqtSignal(object)

    class Job(QtCore.QRunnable):
        """One network call off the UI thread; reports the new status (or None) through ``Result.done``."""

        def __init__(self, fn):
            super().__init__()
            self.fn, self.result = fn, Result()

        def run(self):
            try:
                out = self.fn()
            except Exception:
                log.exception("tray request failed")
                out = None
            self.result.done.emit(out)

    pool = QtCore.QThreadPool.globalInstance()
    live = set()                                        # keeps each Job's Result alive until it reports

    def submit(fn):
        job = Job(fn)
        live.add(job)

        def finished(status):
            live.discard(job)
            ui["busy"] = False
            apply(status)
        job.result.done.connect(finished)
        ui["busy"] = True
        pool.start(job)

    def apply(status):
        ui["status"] = status
        state = tray_state(status)
        if should_notify(ui["state"], state) and not quiet:
            tray.showMessage("wigix", tooltip(status).partition("\n")[2],
                             QtWidgets.QSystemTrayIcon.MessageIcon.Warning, 5000)
        if state != ui["state"]:
            tray.setIcon(icons[state])
            ui["state"] = state
        tray.setToolTip(tooltip(status))
        spec = build_menu(status, has_systemctl, ui.get("autostart"))
        if spec != ui["spec"]:
            menu.clear()
            populate_menu(menu, spec, on_action)
            ui["spec"] = spec

    def poll():
        if not ui["busy"]:
            submit(client.status)

    def control(action, **params):
        def call():
            out = client.control(action, **params)
            return out if out is not None else client.status()
        submit(call)

    def on_action(key):
        if key == "open_editor":
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(editor_url(config_path, *_host_port(client))))
        elif key == "toggle_autostart":
            from . import autostart as _auto
            on = bool(ui.get("autostart"))
            _auto.disable(say=lambda *_: None, stop_now=False) if on else _auto.enable(str(config_path), tray=None, say=lambda *_: None)
            ui["autostart"] = _auto.daemon_enabled()
            apply(ui.get("status"))
        elif key == "restart_daemon":
            subprocess.Popen(["systemctl", "--user", "restart", "wigix"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        elif key == "quit":
            app.quit()
        else:
            ctl = control_for(key)
            if ctl:
                control(ctl[0], **ctl[1])

    def on_activated(reason):
        R = QtWidgets.QSystemTrayIcon.ActivationReason
        if reason == R.MiddleClick:
            control("toggle_pause")
        elif reason == R.Trigger:                       # left click opens the menu too
            menu.popup(QtGui.QCursor.pos())

    tray.activated.connect(on_activated)
    apply(None)
    tray.show()
    timer = QtCore.QTimer()
    timer.timeout.connect(poll)
    timer.start(POLL_MS)
    poll()
    code = app.exec()
    lock.unlock()
    return code


def _host_port(client: ControlClient) -> tuple[str, int]:
    """(host, port) of the client's base URL, for editor_url."""
    from urllib.parse import urlsplit
    u = urlsplit(client.base_url)
    return u.hostname or "127.0.0.1", u.port or 8765
