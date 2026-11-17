# SPDX-License-Identifier: GPL-3.0-only
"""Screensaver on/off, page auto-rotation, theme presets, page dots and the tray/control switches."""
import pathlib
import time

import pytest

from wigix import settings, tray, widgets
from wigix.engine import Dashboard
from wigix.render import THEME, set_theme
from tests.test_editor import CHROME, TOKEN, XRW, post, serve  # noqa: F401

widgets.load_plugins()


def dash(extra=None, pages=3):
    cfg = {"pages": [{"name": n, "widgets": [{"type": "text", "text": n}]} for n in "ABCDE"[:pages]]}
    cfg.update(extra or {})
    return Dashboard(cfg)


# --- normalising the config --------------------------------------------------------------------
def test_rotate_spellings():
    assert settings.rotate_settings({})["enabled"] is False
    assert settings.rotate_settings({"page_seconds": 20}) == {"enabled": True, "seconds": 20.0, "pause_after_touch": 30.0}
    assert settings.rotate_settings({"rotate": True})["seconds"] == 15
    assert settings.rotate_settings({"rotate": {"seconds": 8}})["enabled"] is True
    assert settings.rotate_settings({"rotate": {"enabled": False, "seconds": 8}})["enabled"] is False
    assert settings.rotate_settings({"page_seconds": 20}, override=False)["enabled"] is False
    assert settings.rotate_settings({}, override=True)["enabled"] is True


def test_idle_and_screensaver_switches():
    on = {"idle": {"after": 60, "dim_to": 10}}
    assert settings.idle_settings({}) is None and settings.idle_settings({"idle": False}) is None
    assert settings.idle_settings(on) == on["idle"]
    assert settings.idle_settings({"idle": {"enabled": False, "after": 60}}) is None
    assert settings.idle_settings(on, override=False) is None            # tray switch off
    assert settings.idle_settings({}, override=True) == {}               # tray switch on, nothing configured
    assert settings.screensaver_config(on) == {"type": "screensaver"}
    assert settings.screensaver_config({"idle": {"screensaver": False}}) is None            # dim only
    assert settings.screensaver_config({"idle": {"screensaver": {"enabled": False}}}) is None
    assert settings.screensaver_config({"idle": {"screensaver": {"type": "screensaver", "text": "x"}}})["text"] == "x"
    assert settings.splash_seconds({}) == 1.5 and settings.splash_seconds({"splash": False}) is None
    assert settings.splash_seconds({"splash": {"enabled": False}}) is None and settings.splash_seconds({"splash": {"seconds": 3}}) == 3


def test_disabled_idle_never_dims_or_shows_a_screensaver():
    d = dash({"idle": {"enabled": False, "after": 1, "dim_to": 5}})
    d.overlays.last_touch = time.time() - 100
    d.overlays.update()
    assert not d.overlays.idle and d.overlays.brightness_override is None and d.overlays.frame() is None


def test_dim_only_when_screensaver_is_off():
    d = dash({"idle": {"after": 1, "dim_to": 5, "screensaver": False}})
    d.overlays.last_touch = time.time() - 100
    d.overlays.update()
    assert d.overlays.idle and d.overlays.brightness_override == 5 and d.overlays.frame() is None


def test_tray_can_switch_the_screensaver_off_and_on():
    d = dash({"idle": {"after": 1, "dim_to": 5}})
    d.overlays.last_touch = time.time() - 100
    d.overlays.update()
    assert d.overlays.frame() is not None and d.control_state()["screensaver"] is True
    st = d.control({"action": "screensaver", "value": False})
    assert st["screensaver"] is False and not d.overlays.idle
    d.overlays.last_touch = time.time() - 100
    d.overlays.update()
    assert d.overlays.frame() is None
    assert d.control({"action": "screensaver"})["screensaver"] is True       # no value = toggle
    with pytest.raises(ValueError):
        d.control({"action": "screensaver", "value": "yes"})


# --- rotation ----------------------------------------------------------------------------------
def rotate_at(d, t):
    d.overlays.last_touch = t - 1000
    d._rotate(t)


def test_rotation_advances_after_dwell_and_wraps():
    d = dash({"rotate": {"seconds": 10}})
    t = d._page_since
    rotate_at(d, t + 5)
    assert d.page_idx == 0
    rotate_at(d, t + 11)
    assert d.page_idx == 1
    rotate_at(d, t + 22)
    rotate_at(d, t + 33)
    assert d.page_idx == 0


def test_legacy_page_seconds_still_rotates():
    d = dash({"page_seconds": 5})
    t = d._page_since
    rotate_at(d, t + 6)
    assert d.page_idx == 1


def test_page_dwell_and_skipping_pages():
    d = dash({"rotate": {"seconds": 10}})
    d.cfg["pages"][0]["seconds"] = 3
    d.pages[0].cfg["seconds"] = 3
    d.pages[1].cfg["rotate"] = False
    t = d._page_since
    rotate_at(d, t + 4)                 # page A has its own 3 s dwell
    assert d.page_idx == 2              # B is left out of the rotation


def test_rotation_holds_during_touch_popup_fullscreen_and_idle():
    d = dash({"rotate": {"seconds": 10, "pause_after_touch": 30}})
    t = d._page_since
    d.overlays.last_touch = t + 5       # touched 6 s ago
    d._rotate(t + 11)
    assert d.page_idx == 0
    d.overlays.last_touch = t - 1000
    d.full = object()
    d._rotate(t + 30)
    assert d.page_idx == 0
    d.full = None
    d.overlays.idle = True
    d._rotate(t + 60)
    assert d.page_idx == 0
    d.overlays.idle = False
    d._rotate(t + 61)                   # the hold restarted the dwell timer
    assert d.page_idx == 0
    d._rotate(t + 80)
    assert d.page_idx == 1


def test_manual_navigation_restarts_the_dwell_and_the_tray_switch_wins():
    d = dash({"rotate": {"seconds": 10}})
    d.goto("next_page")
    rotate_at(d, d._page_since + 5)
    assert d.page_idx == 1
    assert d.control({"action": "rotate", "value": False})["rotate"] is False
    rotate_at(d, d._page_since + 99)
    assert d.page_idx == 1
    assert d.control({"action": "rotate"})["rotate"] is True                 # toggle back on
    d.reload(dict(d.cfg))                                                     # a config reload keeps the switch
    assert d.control_state()["rotate"] is True


def test_single_page_never_rotates():
    d = dash({"rotate": {"seconds": 1}}, pages=1)
    rotate_at(d, d._page_since + 50)
    assert d.page_idx == 0


# --- look ----------------------------------------------------------------------------------------
def test_theme_presets_resolve_and_explicit_colors_win():
    assert settings.resolve_theme("nord")["bg"] == settings.PRESETS["nord"]["bg"]
    t = settings.resolve_theme({"preset": "nord", "accent": "#ff0000"})
    assert t["accent"] == "#ff0000" and t["card"] == settings.PRESETS["nord"]["card"]
    assert settings.resolve_theme({"preset": "nope"}) == {}
    try:
        set_theme({"preset": "dracula"})
        assert THEME["accent"] == (0xbd, 0x93, 0xf9)
    finally:
        set_theme(None)


def test_page_dots_drawn_only_for_several_pages():
    y = 592 - 5
    d = dash()
    img = d.render_current()
    assert img.getpixel((494, y)) == tuple(THEME["accent"])          # three dots at 494, 508, 522: page A is current
    assert img.getpixel((508, y)) != tuple(THEME["accent"]) and img.getpixel((508, y)) != tuple(THEME["bg"])
    d.page_idx = 1
    assert d.render_current().getpixel((508, y)) == tuple(THEME["accent"])
    assert dash(pages=1).render_current().getpixel((494, y)) == tuple(THEME["bg"])
    assert dash({"page_dots": False}).render_current().getpixel((494, y)) == tuple(THEME["bg"])


# --- tray -------------------------------------------------------------------------------------------
def test_tray_menu_has_the_switches():
    st = {"connected": True, "paused": False, "pages": ["A", "B"], "page_name": "A", "brightness": 100,
          "rotate": True, "screensaver": False}
    items = {m.key: m for m in tray.build_menu(st)}
    assert items["toggle_rotate"].checked is True and items["toggle_screensaver"].checked is False
    assert tray.control_for("toggle_rotate") == ("rotate", {})
    assert tray.control_for("toggle_screensaver") == ("screensaver", {})
    assert "toggle_rotate" not in {m.key for m in tray.build_menu({"connected": True, "pages": []})}   # older daemons


# --- editor validation --------------------------------------------------------------------------
@pytest.fixture
def ed(tmp_path):
    url, cfg, srv = serve(tmp_path)
    yield url
    srv.shutdown()


@pytest.mark.parametrize("patch,ok", [
    ({"rotate": {"seconds": 12}}, True), ({"rotate": True}, True), ({"idle": False}, True),
    ({"idle": {"enabled": False, "after": 60}}, True), ({"theme": {"preset": "nord"}}, True), ({"theme": "nord"}, True),
    ({"rotate": {"seconds": "soon"}}, False), ({"rotate": 5}, False), ({"idle": {"dim_to": 150}}, False),
    ({"idle": {"after": 0}}, False), ({"theme": {"preset": "neon"}}, False), ({"theme": 7}, False),
])
def test_editor_validates_display_settings(ed, patch, ok):
    cfg = {"pages": [{"name": "P", "widgets": []}], **patch}
    code, body = post(ed, "/api/validate", {"config": cfg})
    assert (code == 200 and body.get("ok", True)) == ok, body


def test_editor_validates_page_seconds(ed):
    cfg = {"pages": [{"name": "P", "seconds": 0, "widgets": []}]}
    assert post(ed, "/api/validate", {"config": cfg})[1].get("ok") is False


def test_editor_meta_lists_presets(ed):
    import json
    from tests.test_editor import call
    assert "nord" in json.loads(call(ed + "/api/meta")[1])["presets"]


# --- the Settings panel in a browser --------------------------------------------------------------
def test_settings_panel_edits_rotation_and_screensaver(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    if not pathlib.Path(CHROME).exists():
        pytest.skip("google-chrome-stable not installed")
    url, cfg, srv = serve(tmp_path, "pages:\n- name: A\n  widgets: []\n- name: B\n  widgets: []\n"
                                    "idle: {after: 300, dim_to: 15}\npage_seconds: 20\n")
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            except Exception as e:
                pytest.skip(f"cannot launch Chrome: {e}")
            pg = browser.new_context(bypass_csp=True, viewport={"width": 1500, "height": 1000}).new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"{url}/?token={TOKEN[0]}")
            pg.wait_for_selector("#palette button")
            pg.click("#tab-settings")
            assert pg.is_checked("#rotate-on") and pg.input_value("#rot-secs") == "20"      # legacy page_seconds shows up
            assert pg.is_checked("#idle-on") and pg.input_value("#idle-after") == "5"        # 300 s shown as minutes
            pg.fill("#rot-secs", "7")
            pg.dispatch_event("#rot-secs", "change")
            pg.uncheck("#saver-on")                                                          # keep dimming, drop the saver
            pg.uncheck("#idle-on")                                                           # ...then switch idle off entirely
            pg.click(".preset[title=nord]")
            pg.click("#tab-inspector")
            pg.fill("#pdwell", "3")
            pg.dispatch_event("#pdwell", "change")
            pg.uncheck("#pinrot")                                                            # page A leaves the rotation
            assert pg.is_disabled("#pdwell")
            pg.keyboard.press("Control+s")
            pg.wait_for_function("document.querySelector('#msg').textContent.startsWith('saved')")
            browser.close()
        import yaml
        saved = yaml.safe_load(cfg.read_text())
        assert saved["rotate"] == {"enabled": True, "seconds": 7, "pause_after_touch": 30} and "page_seconds" not in saved
        assert saved["idle"]["enabled"] is False and saved["idle"]["screensaver"] is False and saved["idle"]["after"] == 300
        assert saved["theme"] == {"preset": "nord"} and saved["pages"][0] == {"name": "A", "widgets": [], "seconds": 3, "rotate": False}
        assert not errors, errors
    finally:
        srv.shutdown()


# --- btop-style tiles ----------------------------------------------------------------------------
def test_area_chart_and_frame_draw_something():
    from PIL import Image, ImageDraw
    from wigix.render import Canvas
    img = Image.new("RGB", (400, 200), THEME["bg"])
    cv = Canvas(ImageDraw.Draw(img), img)
    inner = cv.frame((0, 0, 400, 200), "Box", "right")
    assert inner[0] > 0 and inner[3] < 200
    now = time.time()
    cv.area((20, 40, 380, 180), [(now - 60 + i, 50 + i % 20) for i in range(60)], 60, vmax=100, color=(0, 200, 0), now=now)
    assert len(img.getcolors(100000)) > 6
    cv.area((20, 40, 380, 180), [], 60)                      # no data: just the plate, no crash


def test_polling_integration_keeps_a_history():
    from wigix.integrations.polling import PollingIntegration
    p = PollingIntegration("x", {})
    p.record("cpu", 5.0, t=10.0)
    p.record("cpu", None)
    p.record("cpu", 7)
    assert [v for _, v in p.series("cpu")] == [5.0, 7.0] and p.series("nope") == []


def test_machine_widgets_render_in_both_styles():
    from PIL import Image, ImageDraw
    from wigix.render import Canvas
    for style in ("btop", "card"):
        w = widgets.REGISTRY["system"]({"type": "system", "style": style}, {})
        w.update()
        w.update()
        img = Image.new("RGB", (493, 285), THEME["bg"])
        w.draw(Canvas(ImageDraw.Draw(img), img), (0, 0, 493, 285))
        assert len(img.getcolors(100000)) > 8


def test_air_widget_draws_graphs_with_the_weekly_average():
    from PIL import Image, ImageDraw
    from wigix.render import Canvas
    now = time.time()
    states = {"r": ("2.68", "pCi/L"), "ra": ("3.14", "pCi/L"), "t": ("64.6", "\u00b0F")}

    class HA:
        connected, error = True, None

        def state(self, e):
            return {"state": states[e][0], "attributes": {"unit_of_measurement": states[e][1]}}

        def history_series(self, e, hours, **kw):
            return [(now - hours * 3600 * (1 - i / 49), 2 + (i % 5) * 0.4) for i in range(50)]

    class Reg:
        def get(self, *a):
            return HA()

    w = widgets.REGISTRY["air"]({"type": "air", "radon": "r", "radon_avg": "ra", "temperature": "t"}, {"integrations": Reg()})
    w.update()
    assert set(w.series) == {"radon", "temperature"}
    img = Image.new("RGB", (488, 281), THEME["bg"])
    w.draw(Canvas(ImageDraw.Draw(img), img), (0, 0, 488, 281))
    assert len(img.getcolors(100000)) > 10
    w2 = widgets.REGISTRY["air"]({"type": "air", "radon": "r"}, {"integrations": type("R", (), {"get": lambda s, *a: (_ for _ in ()).throw(KeyError("x"))})()})
    w2.draw(Canvas(ImageDraw.Draw(img), img), (0, 0, 488, 281))        # no HA integration: a status card, no crash


def test_air_widget_retries_soon_when_ha_is_not_up_yet():
    class HA:
        connected = False

    class Reg:
        def get(self, *a):
            return HA()

    w = widgets.REGISTRY["air"]({"type": "air", "radon": "r"}, {"integrations": Reg()})
    w.update()
    assert w.interval == 5.0 and w.series == {}


def test_editor_drag_and_drop(tmp_path):
    """Palette -> preview adds a widget in the dropped cell; a widget dropped on another page's tab moves there; tabs
    reorder by dragging and rename by double click; the palette search filters."""
    sync_api = pytest.importorskip("playwright.sync_api")
    if not pathlib.Path(CHROME).exists():
        pytest.skip("google-chrome-stable not installed")
    url, cfg, srv = serve(tmp_path, "pages:\n- name: A\n  grid: {cols: 4, rows: 3, gap: 10}\n  widgets: []\n"
                                    "- name: B\n  grid: {cols: 4, rows: 3, gap: 10}\n  widgets: []\n")
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            except Exception as e:
                pytest.skip(f"cannot launch Chrome: {e}")
            pg = browser.new_context(bypass_csp=True, viewport={"width": 1500, "height": 1000}).new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"{url}/?token={TOKEN[0]}")
            pg.wait_for_selector("#palette button")
            assert pg.locator(".empty").count() == 1                              # empty-page hint
            pg.fill("#psearch", "clock")
            assert pg.locator("#palette button").count() == 1
            pg.fill("#psearch", "")
            fr = pg.locator("#frame").bounding_box()
            pg.drag_and_drop("#palette button[data-type=clock]", "#frame",
                             target_position={"x": fr["width"] * 0.8, "y": fr["height"] * 0.8})   # bottom-right cell
            pg.wait_for_selector(".wb")
            assert pg.locator(".empty").count() == 0
            pg.keyboard.press("Control+s")
            pg.wait_for_function("document.querySelector('#msg').textContent.startsWith('saved')")
            import yaml
            at = yaml.safe_load(cfg.read_text())["pages"][0]["widgets"][0]["at"]
            assert at[0] >= 2 and at[1] == 2, at                                 # the dropped cell, not the first free one

            wb = pg.locator(".wb").bounding_box()                                 # drag the widget onto tab B
            tb = pg.locator(".tab >> nth=1").bounding_box()
            pg.mouse.move(wb["x"] + wb["width"] / 2, wb["y"] + wb["height"] / 2)
            pg.mouse.down()
            pg.mouse.move(tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2, steps=10)
            pg.mouse.up()
            assert pg.locator(".tab.on").inner_text() == "B" and pg.locator(".wb").count() == 1

            pg.drag_and_drop(".tab >> nth=1", ".tab >> nth=0")                     # reorder the tabs: B first
            assert pg.locator(".tab >> nth=0").inner_text() == "B"
            pg.dblclick(".tab >> nth=0")                                          # rename
            pg.fill(".tab input", "Renamed")
            pg.keyboard.press("Enter")
            assert pg.locator(".tab >> nth=0").inner_text() == "Renamed"
            browser.close()
        assert not errors, errors
    finally:
        srv.shutdown()


def test_widget_list_resizes_and_collapses(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    if not pathlib.Path(CHROME).exists():
        pytest.skip("google-chrome-stable not installed")
    url, cfg, srv = serve(tmp_path)
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            except Exception as e:
                pytest.skip(f"cannot launch Chrome: {e}")
            pg = browser.new_context(bypass_csp=True, viewport={"width": 1600, "height": 900}).new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"{url}/?token={TOKEN[0]}")
            pg.wait_for_selector("#palette button")
            cols = lambda: pg.evaluate("getComputedStyle(document.querySelector('.pgrid')).gridTemplateColumns.split(' ').length")
            assert pg.evaluate("document.querySelector('aside.left').getBoundingClientRect().width") >= 310 and cols() >= 2   # 2+ wide by default
            sp = pg.locator("#lsplit").bounding_box()
            pg.mouse.move(sp["x"] + 4, 300)                                   # drag the splitter wider: more cards per row
            pg.mouse.down()
            pg.mouse.move(sp["x"] + 304, 300, steps=8)
            pg.mouse.up()
            assert pg.evaluate("document.querySelector('aside.left').getBoundingClientRect().width") > 560 and cols() >= 4
            pg.dblclick("#lsplit")                                            # double-click resets
            assert 310 <= pg.evaluate("document.querySelector('aside.left').getBoundingClientRect().width") <= 330
            pg.click("#pcol")
            assert pg.locator("#palette details[open]").count() == 0
            pg.click("#pexp")
            assert pg.locator("#palette details[open]").count() >= 2
            pg.reload()                                                       # the collapsed/width choice is remembered per browser
            pg.wait_for_selector("#palette button")
            assert pg.locator("#palette details[open]").count() >= 2
            browser.close()
        assert not errors, errors
    finally:
        srv.shutdown()


def test_clicking_a_list_entry_opens_settings_and_drag_places_it_with_them(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    if not pathlib.Path(CHROME).exists():
        pytest.skip("google-chrome-stable not installed")
    url, cfg, srv = serve(tmp_path, "pages:\n- name: A\n  grid: {cols: 4, rows: 3, gap: 10}\n  widgets: []\n")
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            except Exception as e:
                pytest.skip(f"cannot launch Chrome: {e}")
            pg = browser.new_context(bypass_csp=True, viewport={"width": 1600, "height": 900}).new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"{url}/?token={TOKEN[0]}")
            pg.wait_for_selector("#palette button")
            pg.click("#palette button[data-type=text]")
            assert pg.locator(".wb").count() == 0                              # nothing was placed
            assert pg.locator(".pickcard").count() == 1
            field = pg.locator(".pickcard >> xpath=.//label[normalize-space()='text']/following-sibling::*[1]")
            field.fill("hello from the list")
            field.dispatch_event("change")
            fr = pg.locator("#frame").bounding_box()
            pg.drag_and_drop("#palette button[data-type=text]", "#frame",
                             target_position={"x": fr["width"] * 0.1, "y": fr["height"] * 0.9})
            pg.wait_for_selector(".wb")
            pg.keyboard.press("Control+s")
            pg.wait_for_function("document.querySelector('#msg').textContent.startsWith('saved')")
            browser.close()
        import yaml
        w = yaml.safe_load(cfg.read_text())["pages"][0]["widgets"][0]
        assert w["type"] == "text" and w["text"] == "hello from the list" and w["at"][:2] == [0, 2]
        assert not errors, errors
    finally:
        srv.shutdown()


def test_sensor_graphs_widget_draws_and_handles_missing_sensors():
    from PIL import Image, ImageDraw
    from wigix.render import Canvas
    now = time.time()

    class HA:
        connected, error = True, None

        def state(self, e):
            if e == "sensor.gone":
                return None
            return {"state": {"t": "78.2", "h": "61", "b": "88"}[e], "attributes": {"unit_of_measurement": "°F" if e == "t" else "%"}}

        def history_series(self, e, hours, **kw):
            return [(now - hours * 3600 * (1 - i / 29), 70 + i % 7) for i in range(30)]

    class Reg:
        def get(self, *a):
            return HA()

    w = widgets.REGISTRY["sensor_graphs"]({"type": "sensor_graphs", "name": "Frog", "battery": "b",
                                           "sensors": [{"entity": "t", "label": "Warm"}, {"entity": "h"}, {"entity": "sensor.gone"}]},
                                          {"integrations": Reg()})
    w.update()
    img = Image.new("RGB", (322, 378), THEME["bg"])
    w.draw(Canvas(ImageDraw.Draw(img), img), (0, 0, 322, 378))
    assert len(img.getcolors(100000)) > 10 and set(w.series) == {"t", "h", "sensor.gone"}
