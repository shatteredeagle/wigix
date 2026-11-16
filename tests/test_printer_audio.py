# SPDX-License-Identifier: GPL-3.0-only
"""Bambu printer (merged MQTT reports) and audio visualizer (fake capture process). No broker, sound card or hosts."""
import json
import threading
import time

import numpy as np
import pytest

from wigix import widgets
from wigix.engine import Page
from wigix.integrations import Registry
from wigix.integrations.bambu import Bambu
from wigix.widgets import audio
from wigix.widgets.bambu import fmt_remaining

widgets.load_plugins()

SERIAL = "00M00A000000000"
FULL = {"print": {"gcode_state": "RUNNING", "mc_percent": 42, "mc_remaining_time": 72, "layer_num": 12,
                  "total_layer_num": 200, "nozzle_temper": 215.0, "nozzle_target_temper": 220,
                  "bed_temper": 60.0, "bed_target_temper": 60, "chamber_temper": 31, "subtask_name": "benchy",
                  "print_error": 0, "wifi_signal": "-45dBm", "spd_lvl": 2}}


def printer(**extra):
    r = Registry({"integrations": {"p": {"type": "bambu", "host": "printer.lan", "serial": SERIAL,
                                         "access_code": "12345678", **extra}}})
    return r, r.get("p", "bambu")


def page(r, wtype="printer", cols=1, rows=1, **cfg):
    return Page({"grid": {"cols": cols, "rows": rows}, "widgets": [{"type": wtype, "at": [0, 0, 1, 1], **cfg}]}, {"integrations": r})


# --- Bambu integration ----------------------------------------------------------------------
def test_bambu_config_and_topics():
    _, i = printer()
    assert isinstance(i, Bambu) and i.port == 8883 and i.access_code == "12345678"
    assert i.topic_report == f"device/{SERIAL}/report" and i.topic_request == f"device/{SERIAL}/request"
    assert i.data is None and not i.connected


def test_bambu_access_code_from_file(tmp_path):
    f = tmp_path / "code"
    f.write_text("87654321\n")
    assert printer(access_code=None, access_code_file=str(f))[1].access_code == "87654321"


def test_bambu_partial_messages_merge():
    _, i = printer()
    i.handle_message(json.dumps({"print": {"mc_percent": 5}}))               # a delta before any full state
    assert i.data is None
    i.handle_message(json.dumps(FULL).encode())
    assert i.data["mc_percent"] == 42 and i.data["subtask_name"] == "benchy"
    i.handle_message(json.dumps({"print": {"mc_percent": 43, "layer_num": 13}}))
    assert i.data["mc_percent"] == 43 and i.data["layer_num"] == 13
    assert i.data["total_layer_num"] == 200 and i.data["nozzle_temper"] == 215.0   # untouched keys survive
    i.handle_message(json.dumps({"info": {"command": "get_version"}}))       # other report kinds are ignored
    i.handle_message(b"not json")
    i.handle_message(json.dumps([1, 2]))
    assert i.data["mc_percent"] == 43


def test_bambu_publishes_pushall_on_connect():
    _, i = printer()

    class Client:
        def __init__(self):
            self.subs, self.pubs = [], []

        def subscribe(self, t):
            self.subs.append(t)

        def publish(self, t, payload):
            self.pubs.append((t, json.loads(payload)))

    class Reason:
        def __init__(self, fail):
            self.is_failure = fail

        def __str__(self):
            return "Not authorized" if self.is_failure else "Success"

    c = Client()
    i._client = c
    i._on_connect(c, None, None, Reason(True))
    assert not i.connected and "Not authorized" in i.error and not c.pubs
    i._on_connect(c, None, None, Reason(False))
    assert i.connected and i.error is None and c.subs == [i.topic_report]
    assert c.pubs == [(i.topic_request, {"pushing": {"sequence_id": "0", "command": "pushall"}})]
    i._on_disconnect(c, None, None, Reason(True))
    assert not i.connected and i.error


def test_bambu_start_requires_host_and_serial():
    r = Registry({"integrations": {"p": {"type": "bambu"}}})
    i = r.get("p", "bambu")
    i.start()
    assert i.error and i._thread is None
    i.stop()


def test_fmt_remaining():
    assert [fmt_remaining(m) for m in (37, 72, 60, 0, None, "x", -1, 1500)] == ["37m", "1h 12m", "1h 00m", "0m", "", "", "", "1d 1h"]


# --- printer widget -------------------------------------------------------------------------
def colors(img):
    return {c for _, c in img.getcolors(1 << 20)}


def test_printer_widget_states_render_differently():
    r, i = printer()
    i.connected = True
    imgs = {}
    for state in ("RUNNING", "PAUSE", "FAILED", "FINISH", "IDLE"):
        i.handle_message({"print": {**FULL["print"], "gcode_state": state}})
        imgs[state] = page(r).render().tobytes()
    assert len(set(imgs.values())) == 5
    i.handle_message({"print": {"nozzle_temper": 150.0, "print_error": 0x03004001}})        # heating + error text
    assert page(r).render().tobytes() != imgs["IDLE"]
    assert page(r).render(only=page(r).items[0][0]).size == (1016, 592)


def test_printer_widget_survives_missing_keys_and_garbage():
    r, i = printer()
    i.connected = True
    for part in ({"gcode_state": "RUNNING"}, {"gcode_state": "WEIRD", "mc_percent": None, "layer_num": "x",
                                              "nozzle_temper": "hot", "mc_remaining_time": None, "print_error": "bad"}):
        i._state.clear()
        i.handle_message({"print": part})
        p = page(r)
        assert len(colors(p.render())) > 3
        p.render(only=p.items[0][0])


def test_printer_widget_offline_and_missing_integration():
    r, i = printer()
    assert len(colors(page(r).render())) > 3                    # loading
    i.handle_message(FULL)
    i.error = "disconnected"
    offline = page(r).render().tobytes()
    i.connected = True
    i.error = None
    assert page(r).render().tobytes() != offline                # connected state differs from the offline card
    assert len(colors(page(Registry({})).render())) > 3         # "no integration"


# --- audio: pure spectrum function ----------------------------------------------------------
def sine(freq, n=audio.WINDOW, amp=0.8):
    return amp * np.sin(2 * np.pi * freq * np.arange(n) / audio.RATE)


@pytest.mark.parametrize("freq", [100, 440, 1000, 5000, 12000])
def test_spectrum_peak_in_expected_bar(freq):
    bars = 32
    out = audio.spectrum_bars(sine(freq), bars)
    edges = np.geomspace(40.0, 16000.0, bars + 1)
    expect = int(np.searchsorted(edges, freq) - 1)
    assert abs(int(out.argmax()) - expect) <= 1
    assert out.max() > 0.8 and out.shape == (bars,)
    assert out[(expect + 8) % bars] < out.max() * 0.5           # far-away bars are much quieter


def test_spectrum_silence_and_edge_cases():
    assert not audio.spectrum_bars(np.zeros(audio.WINDOW), 16).any()
    assert not audio.spectrum_bars([], 8).any()
    out = audio.spectrum_bars(sine(1000, amp=1.0), 8)
    assert 0.0 <= out.min() and out.max() <= 1.0
    assert audio.spectrum_bars(np.random.default_rng(1).uniform(-1, 1, 2048), 64).shape == (64,)


def test_gradient_color_runs_accent_to_crit():
    from wigix.render import THEME
    assert audio.gradient_color(0) == THEME["accent"] and audio.gradient_color(1) == THEME["crit"]
    assert audio.gradient_color(-5) == THEME["accent"] and audio.gradient_color(9) == THEME["crit"]


# --- audio widget with a fake capture process -----------------------------------------------
class FakeProc:
    """Stands in for subprocess.Popen: streams a sine (or silence) as s16le until terminated."""
    instances = []

    def __init__(self, cmd, freq=1000, **_kw):
        self.cmd, self.freq, self.dead, self.waited, self.pos = cmd, freq, threading.Event(), False, 0
        self.stdout = self
        FakeProc.instances.append(self)

    def read(self, n):
        if self.dead.wait(0.005):
            return b""
        t = np.arange(self.pos, self.pos + n // 2)
        self.pos += n // 2
        wave = np.zeros(len(t)) if not self.freq else 0.8 * np.sin(2 * np.pi * self.freq * t / audio.RATE)
        return (wave * 32767).astype("<i2").tobytes()

    def poll(self):
        return -15 if self.dead.is_set() else None

    def terminate(self):
        self.dead.set()

    kill = terminate

    def wait(self, timeout=None):
        self.waited = True
        return 0


@pytest.fixture
def fake_audio(monkeypatch):
    FakeProc.instances = []
    monkeypatch.setattr(audio.shutil, "which", lambda n: f"/usr/bin/{n}" if n == "parec" else None)
    monkeypatch.setattr(audio.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd, **kw))
    return FakeProc


def make_widget(**cfg):
    return audio.AudioVisualizer(cfg, {})


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_audio_draws_sine_peak_and_stops_cleanly(fake_audio):
    p = Page({"grid": {"cols": 1, "rows": 1}, "widgets": [{"type": "audio_visualizer", "at": [0, 0, 1, 1], "bars": 32}]}, {})
    w = p.items[0][0]
    p.render()
    assert wait_for(lambda: w._last_frame > 0 and w._buf.any())
    for _ in range(10):
        img = p.render()
        time.sleep(0.02)
    assert abs(int(w.levels.argmax()) - int(np.searchsorted(np.geomspace(40, 16000, 33), 1000) - 1)) <= 1
    assert len(colors(img)) > 6
    assert len(fake_audio.instances) == 1 and fake_audio.instances[0].cmd[0] == "parec"
    w.stop()
    assert fake_audio.instances[0].dead.is_set() and fake_audio.instances[0].waited and w._proc is None


def test_audio_device_option_and_pw_record_fallback(fake_audio, monkeypatch):
    assert "--device=my.sink" in make_widget(device="my.sink").command()
    assert "--device=@DEFAULT_MONITOR@" in make_widget().command()
    monkeypatch.setattr(audio.shutil, "which", lambda n: "/usr/bin/pw-record" if n == "pw-record" else None)
    cmd = make_widget().command()
    assert cmd[0] == "pw-record" and "stream.capture.sink=true" in cmd and cmd[-1] == "-"
    assert make_widget(device="alsa_output.x").command()[-3:] == ["--target", "alsa_output.x", "-"]


def test_audio_stops_when_not_drawn_and_restarts(fake_audio, monkeypatch):
    w = make_widget()
    from wigix.render import Canvas
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (400, 300))
    cv = Canvas(ImageDraw.Draw(img), img)
    w.draw(cv, (0, 0, 400, 300))
    assert wait_for(lambda: w._proc is not None)
    first = fake_audio.instances[0]
    monkeypatch.setattr(audio, "IDLE_STOP", 0.05)
    time.sleep(0.1)                                             # nobody draws for longer than IDLE_STOP
    w.update()
    assert w._proc is None and first.dead.is_set() and first.waited
    w._last_start = 0.0
    w.draw(cv, (0, 0, 400, 300))                                # the next draw restarts it
    assert wait_for(lambda: len(fake_audio.instances) == 2)
    w.stop()
    assert all(p.dead.is_set() for p in fake_audio.instances)


def test_audio_reader_exits_on_its_own_when_idle(fake_audio, monkeypatch):
    monkeypatch.setattr(audio, "IDLE_STOP", 0.05)
    w = make_widget()
    from wigix.render import Canvas
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (400, 300))
    w.draw(Canvas(ImageDraw.Draw(img), img), (0, 0, 400, 300))
    assert wait_for(lambda: fake_audio.instances and fake_audio.instances[0].dead.is_set())
    assert wait_for(lambda: w._proc is None)


def test_audio_silence_gives_flat_bars(fake_audio, monkeypatch):
    monkeypatch.setattr(audio.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd, freq=0, **kw))
    p = Page({"grid": {"cols": 1, "rows": 1}, "widgets": [{"type": "audio_visualizer", "at": [0, 0, 1, 1]}]}, {})
    w = p.items[0][0]
    p.render()
    assert wait_for(lambda: w._last_frame > 0)
    p.render()
    assert not w.levels.any()
    w.stop()


@pytest.mark.parametrize("style", ["bars", "wave", "line", "nonsense"])
def test_audio_styles_render(fake_audio, style):
    p = Page({"grid": {"cols": 2, "rows": 2}, "widgets": [{"type": "audio_visualizer", "at": [0, 0, 1, 1], "style": style, "bars": 16}]}, {})
    w = p.items[0][0]
    p.render()
    assert wait_for(lambda: w._last_frame > 0)
    p.render()
    p.render(only=w)
    w.stop()


def test_audio_missing_binary_placeholder(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda n: None)
    called = []
    monkeypatch.setattr(audio.subprocess, "Popen", lambda *a, **k: called.append(1))
    p = Page({"grid": {"cols": 1, "rows": 1}, "widgets": [{"type": "audio_visualizer", "at": [0, 0, 1, 1]}]}, {})
    img = p.render()
    w = p.items[0][0]
    assert w.error == "parec missing" and not called and w._proc is None
    assert len(colors(img)) > 3
    w.update()
    w.stop()


def test_audio_spawn_failure_is_reported(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda n: "/usr/bin/parec")

    def boom(*a, **k):
        raise OSError("no such device")
    monkeypatch.setattr(audio.subprocess, "Popen", boom)
    p = Page({"grid": {"cols": 1, "rows": 1}, "widgets": [{"type": "audio_visualizer", "at": [0, 0, 1, 1]}]}, {})
    p.render()
    assert "no such device" in p.items[0][0].error


def test_audio_widget_fields_and_fps():
    assert audio.AudioVisualizer.fps == 15
    for cls in (audio.AudioVisualizer, widgets.REGISTRY["printer"]):
        assert cls.fields and all({"name", "type", "default"} <= set(f) for f in cls.fields)
