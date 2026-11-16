"""The tray's Qt-free model, its control client against a real editor + Dashboard, icons and a Qt smoke test."""
import os
import threading
from http.server import ThreadingHTTPServer

import pytest

from wigix import tray, widgets
from wigix.editor import Editor, make_handler
from wigix.engine import Dashboard, load_config

widgets.load_plugins()
PAGES = "pages:\n- {name: A, widgets: [{type: text}]}\n- {name: B, widgets: [{type: text}]}\n- {name: C, widgets: [{type: text}]}\n"


def st(**kw):
    base = {"running": True, "connected": True, "page": 0, "page_name": "A", "pages": ["A"], "paused": False,
            "brightness": 80, "idle": False, "integrations": {}}
    return {**base, **kw}


# --- model ---------------------------------------------------------------------------------------
def test_state_rules():
    assert tray.tray_state(None) == "disconnected"
    assert tray.tray_state(st(connected=False)) == "disconnected"
    assert tray.tray_state(st(paused=True)) == "paused"
    assert tray.tray_state(st(integrations={"ha": {"connected": False, "error": "boom"}})) == "attention"
    assert tray.tray_state(st(integrations={"ha": {"connected": True, "error": None}})) == "connected"
    assert tray.tray_state(st()) == "connected"
    bad = {"ha": {"error": "x"}}
    assert tray.tray_state(st(paused=True, integrations=bad)) == "paused"
    assert tray.tray_state(st(connected=False, integrations=bad)) == "disconnected"


def test_tooltip_and_truncation():
    assert "not reachable" in tray.tooltip(None)
    t = tray.tooltip(st(page_name="Home"))
    assert "Panel: connected" in t and "Page: Home" in t and "Errors" not in t
    assert "not connected" in tray.tooltip(st(connected=False))
    ints = {n: {"error": "e"} for n in "abcde"}
    t = tray.tooltip(st(integrations=ints))
    assert "Errors: a, b, c (+2 more)" in t and "d" not in t.split("Errors:")[1]
    assert "Display paused" in tray.tooltip(st(paused=True))


def test_menu_one_page_and_three_pages():
    one = tray.build_menu(st(), has_systemctl=False)
    texts = [m.text for m in one]
    assert texts[:2] == ["Panel connected: A", "Open editor"] and "Restart daemon" not in texts
    assert one[-1].key == "quit" and one[0].kind == "label"
    three = tray.build_menu(st(pages=["A", "B", "C"], page_name="B"), has_systemctl=True)
    page = next(m for m in three if m.text == "Page")
    assert [(c.text, c.checked) for c in page.children if c.key.startswith("page:")] == [
        ("A", False), ("B", True), ("C", False)]
    assert page.children[-1].key == "next_page"
    assert any(m.key == "restart_daemon" for m in three)
    assert tray.build_menu(st(pages=["A", "B", "C"], page_name="B"), True) == three      # stable for rebuild checks


def test_menu_when_daemon_is_down():
    m = tray.build_menu(None, has_systemctl=True)
    assert [x.key for x in m if x.kind == "action"] == ["open_editor", "restart_daemon", "quit"]
    assert m[0].text == "Daemon not reachable"


def test_brightness_nearest_preset():
    assert tray.nearest_preset(80) == 75 and tray.nearest_preset(0) == 10 and tray.nearest_preset(100) == 100
    assert tray.nearest_preset(None) is None and tray.nearest_preset(True) is None
    sub = next(m for m in tray.build_menu(st(brightness=30)) if m.text == "Brightness")
    assert [c.text for c in sub.children if c.checked] == ["25 %"]
    sub = next(m for m in tray.build_menu(st(brightness=None)) if m.text == "Brightness")
    assert not any(c.checked for c in sub.children)


def test_pause_resume_labels_and_control_keys():
    def pause_item(s):
        return next(m for m in tray.build_menu(s) if m.text.endswith("display"))
    assert (pause_item(st()).text, pause_item(st()).key) == ("Pause display", "pause")
    assert (pause_item(st(paused=True)).text, pause_item(st(paused=True)).key) == ("Resume display", "resume")
    assert tray.control_for("page:B") == ("page", {"name": "B"})
    assert tray.control_for("brightness:25") == ("brightness", {"value": 25})
    assert tray.control_for("pause") == ("pause", {}) and tray.control_for("quit") is None


def test_should_notify():
    assert not tray.should_notify(None, "disconnected")
    assert tray.should_notify("connected", "attention") and tray.should_notify("paused", "disconnected")
    assert not tray.should_notify("attention", "attention") and not tray.should_notify("disconnected", "connected")


# --- client against a real editor + daemon -------------------------------------------------------
@pytest.fixture
def daemon(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(PAGES)
    cfg.chmod(0o600)
    editor = Editor(str(cfg), Dashboard(load_config(cfg), str(cfg)))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(editor))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", editor, cfg
    srv.shutdown()


def test_client_status_and_controls(daemon):
    url, editor, _ = daemon
    c = tray.ControlClient(url, editor.token)
    s = c.status()
    assert s["pages"] == ["A", "B", "C"] and s["paused"] is False and s["connected"] is False
    assert c.control("next_page")["page_name"] == "B"
    assert c.control("page", name="C")["page_name"] == "C"
    assert c.control("pause")["paused"] is True and tray.tray_state(c.status()) == "disconnected"
    assert c.control("resume")["paused"] is False
    assert c.control("brightness", value=40)["brightness"] == 40
    assert c.control("brightness", value=500) is None and c.control("bogus") is None


def test_client_bad_token_and_daemon_down(daemon):
    url, editor, _ = daemon
    assert tray.ControlClient(url, "wrong").status() is None
    assert tray.ControlClient(url, "wrong").control("pause") is None
    assert tray.ControlClient(url, lambda: None).status() is None            # no token file yet
    assert tray.ControlClient("http://127.0.0.1:1", editor.token).status() is None


def test_client_from_config_reads_port_and_token_file(daemon, tmp_path):
    url, editor, cfg = daemon
    port = url.rsplit(":", 1)[1]
    cfg.write_text(f"editor: {{port: {port}}}\n" + PAGES)
    c = tray.ControlClient.from_config(cfg)
    assert c.base_url == url and c.status()["pages"] == ["A", "B", "C"]


# --- icons ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("state", tray.STATES)
def test_icons_exist_for_every_state_and_size(state):
    for size in tray.ICON_SIZES:
        res = tray.icon_resource(state, size)
        assert res is not None and res.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert tray.icon_sizes(state) == list(tray.ICON_SIZES)
    assert tray.icon_resource(state, 5) is None


# --- Qt smoke test -------------------------------------------------------------------------------
def test_qt_menu_from_spec(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    widgets_mod = pytest.importorskip("PyQt6.QtWidgets")
    QtGui = pytest.importorskip("PyQt6.QtGui")
    app = widgets_mod.QApplication.instance() or widgets_mod.QApplication([])
    icons = tray.load_icons(QtGui)
    assert all(len(i.availableSizes()) == len(tray.ICON_SIZES) for i in icons.values())
    menu = widgets_mod.QMenu()
    got = []
    spec = tray.build_menu(st(pages=["A", "B"], page_name="B", paused=True), has_systemctl=True)
    tray.populate_menu(menu, spec, got.append)
    top = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert top == ["Display paused", "Open editor", "Page", "Brightness",
                   "Resume display", "Restart daemon", "Quit tray"]
    assert not menu.actions()[0].isEnabled()
    page_menu = next(a for a in menu.actions() if a.text() == "Page").menu()
    checked = [a.text() for a in page_menu.actions() if a.isChecked()]
    assert checked == ["B"]
    next(a for a in page_menu.actions() if a.text() == "Next page").trigger()
    next(a for a in menu.actions() if a.text() == "Resume display").trigger()
    assert got == ["next_page", "resume"]
    assert app is not None and os.environ["QT_QPA_PLATFORM"] == "offscreen"
