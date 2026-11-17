# SPDX-License-Identifier: GPL-3.0-only
import time

import pytest
from PIL import Image, ImageDraw

from fake_ha import TOKEN, FakeHA
from wigix import widgets
from wigix.engine import Page
from wigix.integrations import Registry, normalize
from wigix.render import Canvas

widgets.load_plugins()


def S(eid, state, **attrs):
    return {"entity_id": eid, "state": state, "attributes": attrs, "last_changed": "2026-11-16T12:00:00+00:00"}


STATES = [
    S("sensor.temp", "21.4999", unit_of_measurement="°C", friendly_name="Living Temp"),
    S("light.lamp", "on", friendly_name="Lamp", brightness=128),
    S("binary_sensor.leak", "on", device_class="moisture", friendly_name="Basement Leak"),
    S("binary_sensor.door", "off", device_class="door", friendly_name="Door"),
]


def wait(cond, timeout=5.0):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def fake():
    f = FakeHA(STATES)
    f.url = f.start()
    yield f
    f.stop()


def make(fake, token=TOKEN):
    reg = Registry({"integrations": {"ha": {"type": "home_assistant", "url": fake.url, "token": token}}})
    reg.start()
    return reg


def test_connects_and_loads_states(fake):
    reg = make(fake)
    ha = reg.get(None, "home_assistant")
    assert wait(lambda: ha.connected)
    assert ha.state("sensor.temp")["state"] == "21.4999"
    assert {e["id"] for e in ha.entities()} >= {"light.lamp", "sensor.temp"}
    reg.stop()


def test_live_state_changes(fake):
    reg = make(fake)
    ha = reg.get("ha", "home_assistant")
    assert wait(lambda: ha.connected)
    fake.push("sensor.temp", "30", unit_of_measurement="°C")
    assert wait(lambda: ha.state("sensor.temp")["state"] == "30")
    reg.stop()


def test_bad_token_reports_error_and_does_not_retry(fake):
    reg = make(fake, token="wrong")
    ha = reg.get("ha", "home_assistant")
    assert wait(lambda: ha.error is not None)
    assert not ha.connected and "authentication" in ha.error
    time.sleep(1.5)
    assert ha._stop.is_set()          # gave up instead of hammering HA
    reg.stop()


def test_reconnects_after_server_drop(fake):
    reg = make(fake)
    ha = reg.get("ha", "home_assistant")
    assert wait(lambda: ha.connected)
    fake.drop_clients()
    assert wait(lambda: not ha.connected)
    assert wait(lambda: ha.connected, timeout=8)
    fake.push("light.lamp", "off")
    assert wait(lambda: ha.state("light.lamp")["state"] == "off")
    reg.stop()


def test_history_thinned_and_cached(fake):
    fake.history["sensor.temp"] = [i % 7 for i in range(500)] + ["unavailable"]
    reg = make(fake)
    ha = reg.get("ha", "home_assistant")
    assert wait(lambda: ha.connected)
    h = ha.history("sensor.temp", 24, max_points=100)
    assert len(h) == 100 and all(isinstance(v, float) for v in h)
    fake.history["sensor.temp"] = [1]
    assert ha.history("sensor.temp", 24, max_points=100) == h      # served from cache
    reg.stop()


def test_normalize_legacy_block():
    assert normalize({"home_assistant": {"url": "http://x"}})["ha"]["type"] == "home_assistant"
    assert normalize({"integrations": {"a": {"type": "home_assistant"}}, "home_assistant": {"url": "u"}}).keys() == {"a"}


def test_registry_lookup_rules():
    reg = Registry({"integrations": {"a": {"type": "home_assistant", "token": "t"}, "b": {"type": "home_assistant", "token": "t"}}})
    with pytest.raises(KeyError):
        reg.get(None, "home_assistant")       # ambiguous
    assert reg.get("a", "home_assistant").name == "a"
    with pytest.raises(ValueError):
        Registry({"integrations": {"x": {"type": "nope"}}})


def test_registry_reuses_unchanged_connection():
    cfg = {"integrations": {"ha": {"type": "home_assistant", "url": "http://x", "token": "t"}}}
    a = Registry(cfg)
    b = Registry(cfg, reuse=a)
    assert b.items["ha"] is a.items["ha"]
    changed = {"integrations": {"ha": {"type": "home_assistant", "url": "http://y", "token": "t"}}}
    assert Registry(changed, reuse=a).items["ha"] is not a.items["ha"]


# --- widgets -------------------------------------------------------------------------------
def render(fake_reg, widget_cfg, grid=None):
    ctx = {"integrations": fake_reg}
    page = Page({"grid": grid or {"cols": 1, "rows": 1, "gap": 10}, "widgets": [dict(widget_cfg, at=[0, 0, 1, 1])]}, ctx)
    img = page.render()
    return page, img


def nonblank(img):
    return len(img.convert("RGB").getcolors(maxcolors=100000)) > 6


@pytest.mark.parametrize("cfg", [
    {"type": "ha_entity", "entity": "sensor.temp", "precision": 1, "color_rules": [{"above": 20, "color": "#ff0000"}]},
    {"type": "ha_entity", "entity": "sensor.missing"},
    {"type": "ha_list", "title": "T", "entities": ["sensor.temp", "light.lamp", "sensor.missing"]},
    {"type": "ha_toggle", "entity": "light.lamp"},
    {"type": "ha_button", "label": "Go", "service": "scene.turn_on", "entity": "scene.x"},
    {"type": "ha_gauge", "entity": "sensor.temp", "min": 0, "max": 40},
    {"type": "ha_alerts", "title": "Alerts"},
    {"type": "ha_graph", "entity": "sensor.temp"},
])
def test_widgets_render(fake, cfg):
    reg = make(fake)
    assert wait(lambda: reg.get(None, "home_assistant").connected)
    page, img = render(reg, cfg)
    assert nonblank(img)
    reg.stop()


def test_widgets_render_while_offline():
    reg = Registry({"integrations": {"ha": {"type": "home_assistant", "url": "http://127.0.0.1:1", "token": "t"}}})
    for cfg in ({"type": "ha_entity", "entity": "sensor.temp"}, {"type": "ha_alerts"}, {"type": "ha_list", "entities": ["a.b"]}):
        page, img = render(reg, cfg)
        assert nonblank(img)           # "connecting…" placeholder, no crash


def test_widgets_without_integration_show_message():
    page, img = render(Registry({}), {"type": "ha_entity", "entity": "sensor.temp"})
    assert nonblank(img)


def test_toggle_calls_service(fake):
    reg = make(fake)
    ha = reg.get(None, "home_assistant")
    assert wait(lambda: ha.connected)
    calls = []
    ha._rest.post = lambda url, json=None, timeout=None: calls.append((url, json)) or type("R", (), {"raise_for_status": lambda s: None})()
    page, _ = render(reg, {"type": "ha_toggle", "entity": "light.lamp"})
    w = page.items[0][0]
    assert w.on_tap(0, 0) is True
    assert calls[0][0].endswith("/api/services/light/toggle") and calls[0][1] == {"entity_id": "light.lamp"}
    reg.stop()


def test_alerts_scan_finds_problem_sensors(fake):
    reg = make(fake)
    assert wait(lambda: reg.get(None, "home_assistant").connected)
    page, _ = render(reg, {"type": "ha_alerts"})
    assert list(page.items[0][0].alerts()) == ["binary_sensor.leak"]       # the door sensor is not a problem class
    reg.stop()


def test_entity_tap_toggles_graph_unless_action_configured(fake):
    fake.history["sensor.temp"] = [1, 2, 3, 4]
    reg = make(fake)
    ha = reg.get(None, "home_assistant")
    assert wait(lambda: ha.connected)
    page, flat = render(reg, {"type": "ha_entity", "entity": "sensor.temp"})
    w = page.items[0][0]
    assert w.on_tap(0, 0) is True and w._graph
    w.update()
    assert w.values == [1.0, 2.0, 3.0, 4.0]
    assert page.render().tobytes() != flat.tobytes()          # graph view differs from the value view
    w.cfg["on_tap"] = "next_page"
    assert w.on_tap(0, 0) is False                             # the configured action wins
    reg.stop()


def test_history_series_has_timestamps_and_keeps_the_newest_point(fake):
    fake.history["sensor.temp"] = [i % 7 for i in range(500)] + ["unavailable"]
    reg = make(fake)
    ha = reg.get("ha", "home_assistant")
    assert wait(lambda: ha.connected)
    pts = ha.history_series("sensor.temp", 24, max_points=100)
    assert len(pts) == 100 and all(b[0] >= a[0] for a, b in zip(pts, pts[1:]))
    assert pts[-1][1] == float(499 % 7)                              # the "unavailable" row is dropped, newest kept
    reg.stop()
