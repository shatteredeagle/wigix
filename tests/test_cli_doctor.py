import errno
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import wigix.cli
from wigix import doctor, init
from wigix.doctor import FAIL, PASS, WARN, Probe
from wigix.engine import Dashboard, load_config

ROOT = Path(__file__).resolve().parents[1]


class FakeUSBError(OSError):
    def __init__(self, code):
        super().__init__(code, os.strerror(code))


def test_python_check_fails_on_old_python(monkeypatch):
    assert doctor.check_python().status == PASS
    monkeypatch.setattr(sys, "version_info", (3, 8, 0, "final", 0))
    r = doctor.check_python()
    assert r.status == FAIL and "3.10" in r.detail


def test_libusb_missing(monkeypatch):
    import usb.backend.libusb1 as libusb1
    monkeypatch.setattr(libusb1, "get_backend", lambda *a, **k: None)
    r = doctor.check_libusb()
    assert r.status == FAIL and "apt install" in r.fix and "pacman" in r.fix


def test_pyusb_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "usb.core", None)
    assert doctor.check_pyusb().status == FAIL


def test_fonts_fallback_warns(monkeypatch):
    class Default:  # Pillow's built-in font has no .path
        pass
    import wigix.render as render
    monkeypatch.setattr(render, "font", lambda size, bold=False: Default())
    r = doctor.check_fonts()
    assert r.status == WARN and "DejaVu" in r.fix


@pytest.mark.parametrize("found,status,needle", [
    ([(0xEF01, 1001)], PASS, "ef01"),
    ([(0xEF02, 1002)], FAIL, "update mode"),
    ([], FAIL, "no 28da:ef01"),
])
def test_device_present(found, status, needle):
    r = doctor.check_device_present(found)
    assert r.status == status and needle in r.detail
    if status == FAIL:
        assert r.fix


def test_find_devices_never_raises(monkeypatch):
    import usb.core
    def boom(**kw):
        raise RuntimeError("no backend")
    monkeypatch.setattr(usb.core, "find", boom)
    assert doctor.find_devices() == []


def test_permission_denied_prints_rule():
    r = doctor.check_permission(Probe(error=FakeUSBError(errno.EACCES), attempted=True))
    assert r.status == FAIL
    assert 'idProduct}=="ef01"' in r.fix and "udevadm control --reload" in r.fix
    assert doctor.check_permission(Probe(attempted=True)).status == PASS
    assert doctor.check_permission(Probe(), present=False).status == WARN


def test_busy_says_stop_service():
    r = doctor.check_busy(Probe(error=FakeUSBError(errno.EBUSY), attempted=True))
    assert r.status == FAIL and "systemctl --user stop wigix" in r.fix
    assert doctor.check_busy(Probe(attempted=True)).status == PASS


def test_handshake_results():
    ok = doctor.check_handshake(Probe(firmware="1.0.2", name="x", brightness=40, attempted=True))
    assert ok.status == PASS and "1.0.2" in ok.detail and "40%" in ok.detail
    bad = doctor.check_handshake(Probe(error=RuntimeError("timeout"), attempted=True))
    assert bad.status == FAIL and "replug" in bad.fix
    assert doctor.check_handshake(Probe(error=FakeUSBError(errno.EBUSY), attempted=True)).status == WARN


def test_probe_device_captures_errors(monkeypatch):
    import wigix.device
    def boom():
        raise FakeUSBError(errno.EACCES)
    monkeypatch.setattr(wigix.device, "WigiDash", boom)
    p = doctor.probe_device(timeout=2)
    assert p.error.errno == errno.EACCES


def test_probe_device_does_not_hang(monkeypatch):
    import threading
    import wigix.device
    gate = threading.Event()
    monkeypatch.setattr(wigix.device, "WigiDash", lambda: gate.wait(5))
    p = doctor.probe_device(timeout=0.1)
    gate.set()
    assert isinstance(p.error, TimeoutError)


def test_config_missing_and_invalid(tmp_path):
    r, cfg = doctor.check_config(tmp_path / "nope.yaml")
    assert r.status == FAIL and "wigix init" in r.fix and cfg is None
    bad = tmp_path / "bad.yaml"
    bad.write_text("pages:\n  - name: x\n    widgets:\n      - {type: nonsense, at: [0, 0, 1, 1]}\n")
    r, cfg = doctor.check_config(bad)
    assert r.status == FAIL and "nonsense" in r.detail
    empty = tmp_path / "empty.yaml"
    empty.write_text("fps: 2\n")
    assert doctor.check_config(empty)[0].status == FAIL


def test_token_files(tmp_path):
    good, loose = tmp_path / "good", tmp_path / "loose"
    good.write_text("x")
    loose.write_text("x")
    os.chmod(good, 0o600)
    os.chmod(loose, 0o644)
    cfg = {"integrations": {"a": {"token_file": str(good)}, "b": {"api_token_file": str(loose)},
                            "c": {"token_file": str(tmp_path / "missing")}}}
    res = {r.name.split()[-1]: r for r in doctor.check_tokens(cfg)}
    assert res[str(good)].status == PASS
    assert res[str(loose)].status == WARN and "chmod 600" in res[str(loose)].fix
    assert res[str(tmp_path / "missing")].status == FAIL
    assert doctor.check_tokens(None) == []


def _fake_systemctl(monkeypatch, answers):
    monkeypatch.setattr(doctor, "_systemctl", lambda *a: answers.get(a[0]))


def test_service_states(monkeypatch):
    _fake_systemctl(monkeypatch, {})
    assert doctor.check_service().status == WARN
    _fake_systemctl(monkeypatch, {"is-enabled": (0, "enabled"), "is-active": (0, "active")})
    assert doctor.check_service().status == PASS
    _fake_systemctl(monkeypatch, {"is-enabled": (1, "not-found")})
    r = doctor.check_service()
    assert r.status == WARN and "init --systemd" in r.fix
    _fake_systemctl(monkeypatch, {"is-enabled": (0, "enabled"), "is-active": (3, "inactive")})
    assert "not active" in doctor.check_service().detail


def test_systemctl_absent_or_hanging(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda t: None)
    assert doctor._systemctl("is-active", "wigix") is None
    monkeypatch.setattr(doctor.shutil, "which", lambda t: "/bin/systemctl")
    def hang(*a, **k):
        raise subprocess.TimeoutExpired("systemctl", 5)
    monkeypatch.setattr(doctor.subprocess, "run", hang)
    assert doctor._systemctl("is-active", "wigix") is None


def test_optional_tools(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda t: "/bin/x" if t == "ffmpeg" else None)
    res = {r.name: r for r in doctor.check_optional_tools()}
    assert res["optional: ffmpeg"].status == PASS
    assert res["optional: playerctl"].status == WARN and "media_player" in res["optional: playerctl"].detail


def _patch_world(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "find_devices", lambda: [])
    monkeypatch.setattr(doctor, "_systemctl", lambda *a: None)
    monkeypatch.setattr(doctor.shutil, "which", lambda t: None)


def test_run_without_hardware_does_not_crash_and_exits_1(monkeypatch, tmp_path, capsys):
    _patch_world(monkeypatch, tmp_path)
    assert doctor.run(tmp_path / "none.yaml") == 1
    out = capsys.readouterr().out
    assert "[FAIL] WigiDash present" in out and "fix:" in out


def test_exit_zero_without_fail(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks", lambda c: [doctor.Result("a", PASS), doctor.Result("b", WARN, "x", "y")])
    assert doctor.run("x") == 0


def test_json_shape(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks", lambda c: [doctor.Result("a", PASS, "d"), doctor.Result("b", FAIL, "d", "f")])
    assert doctor.run("x", as_json=True) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert data["results"][1] == {"name": "b", "status": "FAIL", "detail": "d", "fix": "f"}


# ---------------------------------------------------------------- init

def test_init_writes_valid_config(tmp_path, capsys):
    out = tmp_path / "sub" / "dashboard.yaml"
    assert init.run(out) == 0
    cfg = load_config(out)
    assert cfg["editor"] == {"port": 8765}
    assert len(cfg["pages"]) == 2
    types = {w["type"] for p in cfg["pages"] for w in p["widgets"]}
    assert {"clock", "system", "checks", "text"} <= types
    Dashboard(cfg, str(out))   # builds every page


def test_init_refuses_to_overwrite_then_backs_up(tmp_path, capsys):
    out = tmp_path / "d.yaml"
    out.write_text("keep: me\n")
    assert init.run(out) == 1
    assert out.read_text() == "keep: me\n"
    assert init.run(out, force=True) == 0
    assert (tmp_path / "d.yaml.bak").read_text() == "keep: me\n"
    assert "pages" in yaml.safe_load(out.read_text())


def test_init_udev_prints_without_writing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(init, "DEFAULT_CONFIG", tmp_path / "default.yaml")
    assert init.run(udev=True) == 0
    out = capsys.readouterr().out
    assert "ef01" in out and "ef02" in out and "udevadm control --reload" in out


def test_udev_rule_matches_contrib_file():
    rule_lines = [ln for ln in (ROOT / "contrib/70-wigix.rules").read_text().splitlines() if ln and not ln.startswith("#")]
    assert rule_lines == [ln for ln in init.UDEV_RULE.splitlines() if not ln.startswith("#")]
    assert all('TAG+="uaccess"' in ln and 'MODE="0660"' in ln for ln in rule_lines)


def test_unit_generation(monkeypatch):
    monkeypatch.setattr(init.shutil, "which", lambda t: "/opt/my tools/bin/wigix")
    text = init.unit_text()
    assert 'ExecStart="/opt/my tools/bin/wigix" run -c %h/.config/wigix/dashboard.yaml' in text
    assert "WantedBy=default.target" in text
    monkeypatch.setattr(init.shutil, "which", lambda t: None)
    assert f'"{sys.executable}" -m wigix.cli run' in init.unit_text()


def test_install_unit_and_no_systemctl_without_enable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(init, "UNIT_DIR", tmp_path / "units")
    monkeypatch.setattr(init, "DEFAULT_CONFIG", tmp_path / "d.yaml")
    monkeypatch.setattr(init, "install_unit", lambda force=False, _o=init.install_unit: _o(tmp_path / "units", force))
    called = []
    monkeypatch.setattr(init.subprocess, "run", lambda *a, **k: called.append(a))
    assert init.run(systemd=True) == 0
    assert (tmp_path / "units" / "wigix.service").exists()
    assert "systemctl --user enable --now wigix" in capsys.readouterr().out
    assert called == []
    assert init.run(systemd=True) == 1   # unit exists, no --force


def test_enable_runs_systemctl(monkeypatch):
    calls = []
    monkeypatch.setattr(init.subprocess, "run", lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    assert init.enable_unit() == 0
    assert calls[-1] == ["systemctl", "--user", "enable", "--now", "wigix"]


# ---------------------------------------------------------------- cli

def test_cli_version_and_devices(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["wigix", "--version"])
    with pytest.raises(SystemExit) as e:
        wigix.cli.main()
    assert e.value.code == 0 and capsys.readouterr().out.startswith("wigix ")
    monkeypatch.setattr(doctor, "find_devices", lambda: [(0xEF01, 1005)])
    monkeypatch.setattr(sys, "argv", ["wigix", "devices"])
    with pytest.raises(SystemExit) as e:
        wigix.cli.main()
    assert e.value.code == 0 and "28da:ef01" in capsys.readouterr().out


def test_cli_doctor_json(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks", lambda c: [doctor.Result("a", PASS)])
    monkeypatch.setattr(sys, "argv", ["wigix", "doctor", "--json"])
    with pytest.raises(SystemExit) as e:
        wigix.cli.main()
    assert e.value.code == 0 and json.loads(capsys.readouterr().out)["ok"] is True
