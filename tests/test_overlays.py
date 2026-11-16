import time

from PIL import Image

from wigix import widgets
from wigix.engine import Dashboard
from wigix.render import THEME

widgets.load_plugins()


class FakeHA:
    type_name = "home_assistant"
    connected = True

    def __init__(self):
        self.states = {}

    def state(self, eid):
        return {"state": self.states[eid]} if eid in self.states else None


class FakeRegistry:
    def __init__(self, ha):
        self.ha = ha

    def get(self, name, type_name):
        return self.ha

    def start(self):
        pass


def dash(extra=None):
    cfg = {"pages": [{"name": "A", "widgets": [{"type": "text", "text": "hello"}]}]}
    cfg.update(extra or {})
    d = Dashboard(cfg)
    d.ha = FakeHA()
    d.integrations = FakeRegistry(d.ha)
    return d


def test_no_overlay_by_default():
    d = dash()
    d.overlays.update()
    assert d.overlays.frame() is None and d.overlays.brightness_override is None


def test_splash_shows_then_expires():
    d = dash({"splash": {"seconds": 0.2}})
    d.overlays.device_connected({"firmware": "FW-1.0"})
    img = d.overlays.frame()
    assert img is not None and img.getcolors(100000)
    time.sleep(0.3)
    assert d.overlays.frame() is None
    d2 = dash({"splash": False})
    d2.overlays.device_connected({})
    assert d2.overlays.frame() is None


def test_idle_dims_shows_screensaver_and_touch_only_wakes():
    d = dash({"idle": {"after": 10, "dim_to": 15, "off_after": 100}})
    o = d.overlays
    o.last_touch = time.time() - 11
    o.update()
    assert o.idle and o.brightness_override == 15
    assert o.frame() is not None and o.fps() == 8.0          # screensaver drifting
    o.last_touch = time.time() - 101
    o.update()
    assert o.brightness_override == 0
    assert o.on_touch(True) is True                          # first touch wakes and must be swallowed
    assert not o.idle and o.brightness_override is None
    assert o.on_touch(True) is False                         # normal touches after that
    o.update()
    assert o.frame() is None


def test_idle_without_screensaver_only_dims():
    d = dash({"idle": {"after": 1, "dim_to": 5, "screensaver": False}})
    d.overlays.last_touch = time.time() - 5
    d.overlays.update()
    assert d.overlays.idle and d.overlays.brightness_override == 5 and d.overlays.frame() is None


def test_brightness_override_beats_config():
    class Dev:
        sets = []

        @property
        def brightness(self):
            return None

        @brightness.setter
        def brightness(self, v):
            self.sets.append(v)

    d = dash({"brightness": 80, "idle": {"after": 1, "dim_to": 10}})
    dev = Dev()
    d._apply_brightness(dev)
    d.overlays.last_touch = time.time() - 5
    d.overlays.update()
    d._apply_brightness(dev)
    d.overlays.on_touch(True)
    d._apply_brightness(dev)
    assert dev.sets == [80, 10, 80]


def test_trigger_fires_on_rising_edge_only_with_cooldown_and_shows_popup():
    d = dash({"triggers": [{"name": "bell", "when": {"entity": "binary_sensor.bell", "state": "on"},
                            "show": {"type": "text", "text": "DING"}, "seconds": 5, "cooldown": 60}]})
    o = d.overlays
    d.ha.states["binary_sensor.bell"] = "off"
    o.update()
    assert not o.popup_active()
    d.ha.states["binary_sensor.bell"] = "on"
    o.update()
    assert o.popup_active() and o.frame() is not None
    o.update()                                                # still on: no refire
    o.close_popup()
    d.ha.states["binary_sensor.bell"] = "off"
    o.update()
    d.ha.states["binary_sensor.bell"] = "on"
    o.update()
    assert not o.popup_active()                               # within the cooldown


def test_trigger_numeric_and_action_and_wake(monkeypatch):
    d = dash({"idle": {"after": 1}, "triggers": [{"when": {"entity": "sensor.t", "above": 30}, "action": "next_page"}]})
    d.cfg["pages"].append({"name": "B", "widgets": [{"type": "text", "text": "b"}]})
    d.overlays.last_touch = time.time() - 5
    d.overlays.update()
    assert d.overlays.idle
    d.ha.states["sensor.t"] = "31"
    d.overlays.update()
    assert not d.overlays.idle                                # trigger woke the screen


def test_trigger_ignores_disconnected_integration():
    d = dash({"triggers": [{"when": {"entity": "binary_sensor.bell", "state": "on"}, "show": {"type": "text"}}]})
    d.ha.connected = False
    d.ha.states["binary_sensor.bell"] = "on"
    d.overlays.update()
    assert not d.overlays.popup_active()
    d.ha.connected = True
    d.overlays.update()
    assert d.overlays.popup_active()


def test_popup_expires_and_unknown_type_is_ignored():
    d = dash()
    d.overlays.show_popup({"type": "no_such_widget"})
    assert not d.overlays.popup_active()
    d.overlays.show_popup({"type": "text", "text": "x"}, seconds=0.05)
    assert d.overlays.popup_active()
    time.sleep(0.1)
    d.overlays.update()
    assert not d.overlays.popup_active()


def test_popup_action_and_tap_dismisses_it():
    d = dash()
    d.run_action({"popup": {"type": "text", "text": "hi"}, "seconds": 30})
    assert d.overlays.popup_active()
    d._last_action = 0
    d.handle_tap(100, 100)
    assert not d.overlays.popup_active()


def test_screensaver_widget_draws_and_styles():
    from wigix.engine import Page
    for style in ("bounce", "clock"):
        page = Page({"grid": {"cols": 1, "rows": 1}, "widgets": [{"type": "screensaver", "style": style, "at": [0, 0, 1, 1]}]}, {})
        assert len(page.render().getcolors(100000)) > 2
