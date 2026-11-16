"""Inside an AppImage the units must point at the .AppImage file, never at the vanishing mount point."""
from pathlib import Path

import pytest

from wigix import autostart, doctor, init
from wigix.doctor import PASS

APPIMAGE = "/home/x/Apps/wigix-0.1.0-x86_64.AppImage"


@pytest.fixture
def appimage(monkeypatch):
    monkeypatch.setenv("APPIMAGE", APPIMAGE)
    monkeypatch.setenv("APPDIR", "/tmp/.mount_wigixAbCdEf")
    monkeypatch.setattr(init.shutil, "which", lambda n: "/tmp/.mount_wigixAbCdEf/usr/bin/" + n)


def test_unit_uses_the_appimage_file(appimage):
    assert init.exec_start() == f'"{APPIMAGE}" run -c %h/.config/wigix/dashboard.yaml'
    assert f"ExecStart=\"{APPIMAGE}\" run" in init.unit_text()
    assert ".mount_" not in init.unit_text()


def test_unit_escapes_systemd_specifiers(monkeypatch):
    monkeypatch.setenv("APPIMAGE", '/home/x/50%/my "app"/$w.AppImage')
    assert init.exec_start().startswith('"/home/x/50%%/my \\"app\\"/$$w.AppImage" run')


def test_tray_desktop_entry_uses_the_appimage_file(appimage):
    text = init.tray_desktop_text()
    assert f'Exec="{APPIMAGE}" tray\n' in text
    assert ".mount_" not in text


def test_tray_desktop_entry_escapes_field_codes(monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/home/x/50%/a$b.AppImage")
    assert 'Exec="/home/x/50%%/a\\$b.AppImage" tray' in init.tray_desktop_text()


def test_without_appimage_the_path_lookup_is_unchanged(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(init.shutil, "which", lambda n: "/usr/bin/" + n)
    assert init.exec_start() == '"/usr/bin/wigix" run -c %h/.config/wigix/dashboard.yaml'


def test_autostart_enable_writes_the_appimage_path(appimage, tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "have_systemctl", lambda: True)
    monkeypatch.setattr(autostart, "_run", lambda *a, **k: None)
    monkeypatch.setattr(init, "enable_unit", lambda: 0)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg = tmp_path / ".config" / "wigix" / "dashboard.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("pages: []\n")
    units, auto = tmp_path / "units", tmp_path / "autostart"
    assert autostart.enable(str(cfg), tray=True, unit_dir=units, autostart_dir=auto, say=lambda m: None) == 0
    unit = (units / "wigix.service").read_text()
    assert f'ExecStart="{APPIMAGE}" run -c %h/.config/wigix/dashboard.yaml' in unit
    assert f'Exec="{APPIMAGE}" tray' in (auto / "wigix-tray.desktop").read_text()


def test_doctor_notes_the_appimage_and_the_udev_commands(appimage):
    (r,) = doctor.check_appimage()
    assert r.status == PASS and APPIMAGE in r.detail
    assert "udevadm control --reload" in r.fix and init.UDEV_PATH in r.fix


def test_doctor_is_silent_outside_an_appimage(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert doctor.check_appimage() == []
