import json
import threading
import time
import urllib.error
import urllib.request

import pytest
from PIL import Image

from wigidash import widgets
from wigidash.engine import Dashboard, Page
from wigidash.editor import Editor, make_handler
from wigidash.render import THEME, set_theme, theme_override
from http.server import ThreadingHTTPServer

widgets.load_plugins()


def dash(extra=None):
    cfg = {"pages": [
        {"name": "A", "grid": {"cols": 2, "rows": 1, "gap": 10},
         "widgets": [{"type": "text", "text": "one", "at": [0, 0, 1, 1], "on_tap": "next_page"},
                     {"type": "text", "text": "two", "at": [1, 0, 1, 1], "on_tap": "fullscreen"}]},
        {"name": "B", "widgets": [{"type": "text", "text": "b"}]}]}
    cfg.update(extra or {})
    return Dashboard(cfg)


def test_theme_global_and_override_are_thread_safe():
    set_theme({"card": "#112233"})
    assert THEME["card"] == (0x11, 0x22, 0x33)
    seen = {}

    def other():
        seen["card"] = THEME["card"]          # other thread must not see the override

    with theme_override({"card": "#ff0000"}):
        assert THEME["card"] == (255, 0, 0)
        t = threading.Thread(target=other); t.start(); t.join()
    assert seen["card"] == (0x11, 0x22, 0x33)
    set_theme({})
    assert THEME["card"] == (28, 32, 40)


def test_widget_theme_override_changes_render():
    d = dash()
    page = d.pages[0]
    base = page.render().getpixel((40, 40))
    page.items[0][0].cfg["theme"] = {"card": "#ff0000"}
    assert page.render().getpixel((40, 40)) != base


def test_tap_actions_pages_and_fullscreen():
    d = dash()
    page = d.current()
    left, right = page.box([0, 0, 1, 1]), page.box([1, 0, 1, 1])
    d.handle_tap(left[0] + 20, left[1] + 20)
    assert d.page_idx == 1
    d.page_idx, d._last_action = 0, 0
    d.handle_tap(right[0] + 20, right[1] + 20)
    assert d.full is page.items[1][0]
    img = d.render_current()                     # only the fullscreen widget is drawn
    assert img.getpixel((20, 20)) == THEME["card"]
    d._last_action = 0
    d.handle_tap(300, 300)                       # any tap closes it
    assert d.full is None


def test_fullscreen_cleared_when_page_changes():
    d = dash()
    d.full = d.pages[0].items[1][0]
    d.page_idx = 1
    d.render_current()
    assert d.full is None


def test_goto_named_page():
    d = dash()
    d.run_action({"page": "B"})
    assert d.page_idx == 1
    d.run_action([{"page": "A"}])
    assert d.page_idx == 0


def test_command_actions_are_off_by_default(tmp_path):
    marker = tmp_path / "ran"
    d = dash()
    d.run_action({"command": f"touch {marker}"})
    time.sleep(0.3)
    assert not marker.exists()
    d2 = dash({"security": {"allow_commands": True}})
    d2.run_action({"command": f"touch {marker}"})
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.exists()


def test_button_widget_with_icon(tmp_path):
    icon = tmp_path / "i.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(icon)
    page = Page({"grid": {"cols": 1, "rows": 1, "gap": 10}, "widgets": [
        {"type": "button", "label": "Go", "icon": str(icon), "at": [0, 0, 1, 1]}]}, {})
    img = page.render()
    assert (255, 0, 0) in {p for _, p in img.getcolors(maxcolors=100000)}


# --- editor API hardening ------------------------------------------------------------------
@pytest.fixture
def editor(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("pages:\n- name: P\n  widgets:\n  - {type: text, text: hi}\n")
    ed = Editor(str(cfg))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ed))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", cfg
    srv.shutdown()


def call(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_editor_rejects_writes_without_custom_header(editor):
    url, cfg = editor
    cur = json.loads(call(url + "/api/config")[1])
    assert call(url + "/api/config", "PUT", cur)[0] == 403
    assert call(url + "/api/config", "PUT", cur, {"X-Requested-With": "wigidash"})[0] == 200


def test_editor_rejects_foreign_host_header(editor):
    url, _ = editor
    assert call(url + "/api/config", headers={"Host": "evil.example.com"})[0] == 403
    assert call(url + "/api/config", headers={"Host": "localhost:8765"})[0] == 200


def test_editor_rejects_invalid_config_and_keeps_backup(editor):
    url, cfg = editor
    h = {"X-Requested-With": "wigidash"}
    bad = {"pages": [{"name": "P", "widgets": [{"type": "no_such_widget"}]}]}
    code, body = call(url + "/api/config", "PUT", bad, h)
    assert code == 400 and b"no_such_widget" in body
    out_of_grid = {"pages": [{"name": "P", "grid": {"cols": 2, "rows": 1}, "widgets": [{"type": "text", "at": [1, 0, 2, 1]}]}]}
    assert call(url + "/api/config", "PUT", out_of_grid, h)[0] == 400
    good = {"pages": [{"name": "P", "widgets": [{"type": "text", "text": "new"}]}]}
    assert call(url + "/api/config", "PUT", good, h)[0] == 200
    assert (cfg.parent / "c.yaml.bak").exists()


def test_brightness_applied_on_connect_and_on_change():
    class Dev:
        def __init__(self):
            self.sets = []

        @property
        def brightness(self):
            return self.sets[-1] if self.sets else None

        @brightness.setter
        def brightness(self, v):
            self.sets.append(v)

    d = dash({"brightness": 40})
    dev = Dev()
    d._apply_brightness(dev)
    d._apply_brightness(dev)
    assert dev.sets == [40]                       # only once per value
    d.cfg["brightness"] = 70
    d._apply_brightness(dev)
    assert dev.sets == [40, 70]
    d._apply_brightness(None)                     # no device: no crash
    dash()._apply_brightness(Dev())               # no brightness configured: nothing set
