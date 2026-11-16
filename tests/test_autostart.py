# SPDX-License-Identifier: GPL-3.0-only
import subprocess
from pathlib import Path

import pytest

from wigix import autostart, init, tray


class Recorder:
    """Fake subprocess.run: records commands, answers from a table keyed by the command prefix."""

    def __init__(self, answers=None):
        self.cmds, self.answers = [], answers or {}

    def __call__(self, cmd, **kw):
        self.cmds.append(tuple(cmd))
        for prefix, (rc, out) in self.answers.items():
            if tuple(cmd[:len(prefix)]) == prefix:
                return subprocess.CompletedProcess(cmd, rc, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    rec = Recorder({("systemctl", "--user", "is-enabled"): (0, "enabled\n"),
                    ("systemctl", "--user", "is-active"): (0, "active\n"),
                    ("loginctl", "show-user"): (0, "no\n")})
    monkeypatch.setattr(autostart.subprocess, "run", rec)
    monkeypatch.setattr(init.subprocess, "run", rec)
    monkeypatch.setattr(autostart.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(init.shutil, "which", lambda n: "/usr/bin/" + n)
    cfg = tmp_path / "home" / ".config" / "wigix" / "dashboard.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("pages: []\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return rec, cfg, tmp_path / "units", tmp_path / "autostart"


def test_enable_installs_unit_enables_it_and_adds_the_tray(env):
    rec, cfg, units, auto = env
    lines = []
    assert autostart.enable(str(cfg), tray=True, unit_dir=units, autostart_dir=auto, say=lines.append) == 0
    unit = (units / "wigix.service").read_text()
    assert "ExecStart=" in unit and "%h/.config/wigix/dashboard.yaml" in unit      # %h-relative config path
    assert ("systemctl", "--user", "enable", "--now", "wigix") in rec.cmds
    assert (auto / "wigix-tray.desktop").exists()
    assert not any(c[:2] == ("loginctl", "enable-linger") for c in rec.cmds)       # boot start only when asked
    assert any("--boot" in l for l in lines)                                        # and it tells you how


def test_enable_with_boot_turns_on_linger_and_reports_failure(env):
    rec, cfg, units, auto = env
    autostart.enable(str(cfg), boot=True, tray=False, unit_dir=units, autostart_dir=auto, say=lambda *_: None)
    assert any(c[:2] == ("loginctl", "enable-linger") for c in rec.cmds)
    assert not (auto / "wigix-tray.desktop").exists()
    rec.answers[("loginctl", "enable-linger")] = (1, "")
    lines = []
    autostart.enable(str(cfg), boot=True, tray=False, unit_dir=units, autostart_dir=auto, say=lines.append)
    assert any("sudo loginctl enable-linger" in l for l in lines)


def test_enable_needs_a_config_and_systemd(env, monkeypatch, tmp_path):
    rec, cfg, units, auto = env
    lines = []
    assert autostart.enable(str(tmp_path / "missing.yaml"), unit_dir=units, autostart_dir=auto, say=lines.append) == 1
    assert "wigix init" in lines[0] and not (units / "wigix.service").exists()
    monkeypatch.setattr(autostart.shutil, "which", lambda n: None)
    lines.clear()
    assert autostart.enable(str(cfg), unit_dir=units, autostart_dir=auto, say=lines.append) == 1
    assert "systemd" in lines[0]


def test_tray_is_skipped_without_pyqt(env, monkeypatch):
    rec, cfg, units, auto = env
    monkeypatch.setattr(autostart, "have_pyqt", lambda: False)
    lines = []
    autostart.enable(str(cfg), unit_dir=units, autostart_dir=auto, say=lines.append)
    assert not (auto / "wigix-tray.desktop").exists() and any("wigix[tray]" in l for l in lines)


def test_disable_keeps_the_daemon_running_when_asked_and_removes_the_tray(env):
    rec, cfg, units, auto = env
    auto.mkdir()
    (auto / "wigix-tray.desktop").write_text("x")
    autostart.disable(unit_dir=units, autostart_dir=auto, say=lambda *_: None, stop_now=False)
    assert ("systemctl", "--user", "disable", "wigix.service") in rec.cmds and not (auto / "wigix-tray.desktop").exists()
    autostart.disable(unit_dir=units, autostart_dir=auto, say=lambda *_: None)
    assert ("systemctl", "--user", "disable", "--now", "wigix.service") in rec.cmds


def test_status_reports_everything(env):
    rec, cfg, units, auto = env
    units.mkdir()
    (units / "wigix.service").write_text("x")
    st = autostart.status(units, auto)
    assert st.unit_installed and st.unit_enabled and st.unit_active and not st.tray_installed and st.linger is False
    text = "\n".join(st.lines())
    assert "starts at login       : yes" in text and "starts at boot        : no" in text


def test_tray_menu_shows_the_login_toggle_only_with_systemd_and_a_known_state():
    on = tray.build_menu({"connected": True}, has_systemctl=True, autostart=True)
    item = next(m for m in on if m.key == "toggle_autostart")
    assert item.checked is True and item.text == "Start at login"
    assert next(m for m in tray.build_menu({"connected": True}, True, False) if m.key == "toggle_autostart").checked is False
    assert not any(m.key == "toggle_autostart" for m in tray.build_menu({"connected": True}, True, None))
    assert not any(m.key == "toggle_autostart" for m in tray.build_menu({"connected": True}, False, True))
