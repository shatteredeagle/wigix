# SPDX-License-Identifier: GPL-3.0-only
"""Tests for the image, video and camera widgets (and Home Assistant's camera_image)."""
import io
import os
import shutil
import subprocess
import time

import pytest
from PIL import Image, ImageDraw

from fake_http import FakeHTTP
from wigix import widgets
from wigix.engine import Page
from wigix.integrations import Registry
from wigix.integrations.home_assistant import HomeAssistant
from wigix.widgets import camera as camera_mod
from wigix.widgets import video as video_mod
from wigix.widgets.image import fit_tile
from wigix.widgets.video import FfmpegStream

widgets.load_plugins()
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
CTX = {"integrations": Registry({}), "run_action": lambda *a: None}


def wait(cond, timeout=8.0):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.03)
    return False


def jpeg(color=(200, 30, 30), size=(160, 90)):
    im = Image.new("RGB", size, color)
    ImageDraw.Draw(im).rectangle((10, 10, 60, 40), fill=(30, 200, 30))
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def make(wtype, cfg, at=(0, 0, 1, 1), grid=(1, 1), ctx=CTX):
    page = Page({"grid": {"cols": grid[0], "rows": grid[1]}, "widgets": [{"type": wtype, "at": list(at), **cfg}]}, ctx)
    return page, page.items[0][0]


def distinct(img):
    return len(img.getcolors(1_000_000))


@pytest.fixture
def gif(tmp_path):
    path = tmp_path / "a.gif"
    frames = [Image.new("RGB", (40, 30), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=[50, 50, 50], loop=0)
    return path


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x90:rate=10",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


# --- image ------------------------------------------------------------------------------------------
def test_image_static_file_and_fit_modes(tmp_path):
    path = tmp_path / "p.png"
    Image.new("RGB", (200, 50), (250, 0, 0)).save(path)
    for fit in ("cover", "contain", "stretch", "bogus"):
        page, w = make("image", {"source": str(path), "fit": fit, "rounded": False}, grid=(4, 3))
        w.update()
        assert w.fps is None                                   # static: no fast redraws
        assert page.render().getpixel((130, 100)) == (250, 0, 0)
    # tile sizes and letterboxing
    im = Image.new("RGBA", (200, 50), (250, 0, 0, 255))
    for fit in ("cover", "contain", "stretch"):
        assert fit_tile(im, (100, 100), fit, (0, 0, 255)).size == (100, 100)
    contain = fit_tile(im, (100, 100), "contain", (0, 0, 255))
    assert contain.getpixel((50, 2)) == (0, 0, 255) and contain.getpixel((50, 50)) == (250, 0, 0)
    assert fit_tile(im, (100, 100), "cover", (0, 0, 255)).getpixel((1, 1)) == (250, 0, 0)


def test_image_animated_gif_advances_and_sets_fps(gif):
    page, w = make("image", {"source": str(gif), "rounded": False})
    w.update()
    assert w.fps == 10 and len(w._loaded.frames) == 3
    assert [w._loaded.index_at(t) for t in (0.0, 0.06, 0.12, 0.16)] == [0, 1, 2, 0]   # frames advance, then loop
    seen = set()
    for i in range(3):
        w._t0 = time.monotonic() - i * 0.05 - 0.01
        seen.add(page.render().getpixel((500, 300)))
    assert len(seen) == 3                                       # a different color on each frame


def test_image_reloads_changed_file(tmp_path):
    path = tmp_path / "p.png"
    Image.new("RGB", (20, 20), (250, 0, 0)).save(path)
    page, w = make("image", {"source": str(path), "rounded": False})
    w.update()
    assert page.render().getpixel((130, 100)) == (250, 0, 0)
    Image.new("RGB", (20, 20), (0, 250, 0)).save(path)
    os.utime(path, (time.time() + 5, time.time() + 5))
    w.update()
    assert page.render().getpixel((500, 300)) == (0, 250, 0)


def test_image_url_fetched_in_update_not_draw():
    srv = FakeHTTP({("GET", "/pic.jpg"): (200, jpeg(), {"Content-Type": "image/jpeg"})})
    page, w = make("image", {"source": srv.url + "/pic.jpg", "refresh": 60})
    page.render()
    assert srv.requests == []                                   # draw() never touches the network
    w.update()
    w.update()                                                  # within `refresh`: no second download
    assert len(srv.requests) == 1
    assert distinct(page.render()) > 3
    srv.stop()


def test_image_placeholders_never_raise(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    srv = FakeHTTP({("GET", "/x"): (500, "boom")})
    for src in ("", str(tmp_path / "missing.png"), str(bad), srv.url + "/x", "http://127.0.0.1:1/x"):
        page, w = make("image", {"source": src})
        w.update()
        assert distinct(page.render()) > 3                      # a status card, no exception
    srv.stop()


# --- home assistant ---------------------------------------------------------------------------------
def test_ha_camera_image_fetches_proxy_and_raises():
    data = jpeg()
    srv = FakeHTTP({("GET", "/api/camera_proxy/camera.porch"): (200, data, {"Content-Type": "image/jpeg"})})
    ha = HomeAssistant("ha", {"url": srv.url, "token": "tok"})
    assert ha.camera_image("camera.porch") == data
    assert srv.requests[0].headers["authorization"] == "Bearer tok"
    with pytest.raises(Exception):
        ha.camera_image("camera.missing")                       # 404 from the server
    srv.stop()


# --- camera -----------------------------------------------------------------------------------------
def test_camera_snapshot_headers_stale_and_offline():
    srv = FakeHTTP({("GET", "/snap.jpg"): (200, jpeg(), {"Content-Type": "image/jpeg"})})
    page, w = make("camera", {"name": "Porch", "snapshot_url": srv.url + "/snap.jpg", "token": "abc",
                              "headers": {"X-Test": "1"}, "refresh": 2}, grid=(4, 3))
    assert distinct(page.render()) > 3                          # placeholder before the first frame
    w.update()
    assert srv.requests[0].headers["authorization"] == "Bearer abc" and srv.requests[0].headers["x-test"] == "1"
    fresh = page.render()
    w._image_time -= 10                                         # older than 3x refresh
    stale = page.render()
    assert fresh.tobytes() != stale.tobytes()                   # the LIVE badge turned into "stale"
    srv.stop()
    w.error = "boom"
    assert page.render() is not None


def test_camera_tap_goes_fullscreen_unless_on_tap_configured():
    calls = []
    ctx = {"integrations": Registry({}), "run_action": lambda *a: calls.append(a)}
    _, w = make("camera", {"snapshot_url": "http://example.com/s.jpg"}, ctx=ctx)
    assert w.on_tap(1, 1) is True and calls[0][0] == "fullscreen" and calls[0][1] is w
    _, w2 = make("camera", {"snapshot_url": "http://example.com/s.jpg", "on_tap": "next_page"}, ctx=ctx)
    assert w2.on_tap(1, 1) is False and len(calls) == 1


def test_camera_source_validation():
    for cfg, text in (({}, "no source"), ({"snapshot_url": "http://x/y", "stream_url": "rtsp://x/y"}, "only one")):
        page, w = make("camera", cfg)
        assert distinct(page.render()) > 3
    _, w = make("camera", {"frigate": True})
    with pytest.raises(ValueError):
        w.update()                                              # no url/camera: reported as a widget error


def test_camera_frigate_recent_event_label_and_fullscreen_rates():
    now = time.time()
    snap = jpeg((20, 20, 200))                                  # blue, so the red label stands out
    srv = FakeHTTP({("GET", "/api/front/latest.jpg"): (200, snap, {"Content-Type": "image/jpeg"}),
                    ("GET", "/api/events"): [{"label": "person", "start_time": now - 5}]})
    cfg = {"frigate": True, "url": srv.url, "camera": "front", "rounded": False}
    page, w = make("camera", cfg)
    w.update()
    assert srv.requests[1].query == {"camera": ["front"], "limit": ["1"]}
    with_label = page.render()
    srv.routes[("GET", "/api/events")] = [{"label": "person", "start_time": now - 300}]
    w.update()
    without = page.render()
    assert with_label.tobytes() != without.tobytes()
    assert with_label.getpixel((24, 556))[0] > 150 > without.getpixel((24, 556))[0]   # the red label pill
    # the base URL can also be given directly in `frigate:`
    _, w2 = make("camera", {"frigate": srv.url, "camera": "front"})
    w2.update()
    assert w2._image is not None
    # fullscreen asks for faster snapshots
    assert w.interval == w.refresh
    w.draw_full(_canvas(), (10, 10, 1006, 582))
    assert w.interval <= 1.0
    srv.stop()


def test_camera_home_assistant_source():
    srv = FakeHTTP({("GET", "/api/camera_proxy/camera.porch"): (200, jpeg(), {"Content-Type": "image/jpeg"})})
    reg = Registry({"integrations": {"ha": {"type": "home_assistant", "url": srv.url, "token": "t"}}})
    ctx = {"integrations": reg, "run_action": lambda *a: None}
    page, w = make("camera", {"ha_camera": "camera.porch"}, ctx=ctx)
    w.update()
    assert w._image is not None and w.label == "camera.porch"
    assert distinct(page.render()) > 3
    _, w2 = make("camera", {"ha_camera": "camera.porch"})       # no HA integration configured
    with pytest.raises(KeyError):
        w2.update()
    srv.stop()


# --- video / ffmpeg ---------------------------------------------------------------------------------
def test_video_without_ffmpeg_or_file_shows_placeholder(monkeypatch, tmp_path):
    page, w = make("video", {"source": str(tmp_path / "x.mp4")})
    assert distinct(page.render()) > 3
    monkeypatch.setattr(video_mod.shutil, "which", lambda name: None)
    page, w = make("video", {"source": str(tmp_path / "x.mp4")})
    page.render()
    assert w.stream.status == "ffmpeg missing"
    page, w = make("camera", {"stream_url": "rtsp://cam.example.com/live"})
    assert distinct(page.render()) > 3 and w._stream.status == "ffmpeg missing"
    assert make("video", {"source": ""})[0].render() is not None


@needs_ffmpeg
def test_video_plays_loops_and_cleans_up(clip):
    page, w = make("video", {"source": str(clip), "fps": 99, "rounded": False}, grid=(4, 3))
    assert w.fps == 15                                          # capped
    t0 = time.monotonic()
    page.render()
    assert time.monotonic() - t0 < 1.0                          # draw() does not wait for ffmpeg
    assert wait(lambda: w.stream.frame() is not None)
    first = w.stream.frame()[1]
    assert wait(lambda: w.stream.frame()[1] > first)            # frames keep arriving
    assert distinct(page.render()) > 10                         # testsrc is colorful
    proc, started = w.stream.proc, time.monotonic()
    assert wait(lambda: time.monotonic() - started > 1.5)       # the clip lasts 1 s: it must loop, not end
    assert w.stream.status is None and proc.poll() is None and w.stream.frame()[1] > started
    w.stream.stop()
    assert w.stream.proc.poll() is not None                     # reaped, no zombie
    assert w.stream._thread is None or not w.stream._thread.is_alive()


@needs_ffmpeg
def test_video_fit_modes_output_widget_size(clip):
    for fit in ("cover", "contain", "stretch"):
        s = FfmpegStream(str(clip), fit=fit, fps=10, loop=False)
        s.touch((120, 80))
        assert wait(lambda: s.frame() is not None)
        assert s.frame()[0].size == (120, 80)
        s.stop()
    s = FfmpegStream(str(clip), fit="contain", fps=10, loop=False, bg=(0, 0, 255))
    s.touch((160, 160))
    assert wait(lambda: s.frame() is not None)
    assert s.frame()[0].getpixel((80, 2)) == (0, 0, 255)        # letterbox bar uses the background
    s.stop()


@needs_ffmpeg
def test_video_resize_restarts_for_fullscreen(clip):
    page, w = make("video", {"source": str(clip)}, grid=(4, 3))
    page.render()
    assert wait(lambda: w.stream.frame() is not None)
    assert w.stream.frame()[0].size == (w.box[2] - w.box[0], w.box[3] - w.box[1])
    page.render(only=w)                                         # fullscreen box
    assert wait(lambda: w.stream.frame()[0].size == (996, 572))
    w.stream.stop()


@needs_ffmpeg
def test_video_stops_when_hidden_and_restarts_on_draw(clip):
    s = FfmpegStream(str(clip), fps=10, loop=True, idle=0.6)
    s.touch((64, 48))
    assert wait(lambda: s.frame() is not None)
    proc = s.proc
    assert wait(lambda: proc.poll() is not None, 5)             # not touched again: killed
    assert wait(lambda: not s._thread.is_alive())
    seen = s.frame()[1]
    s.touch((64, 48))                                           # visible again
    assert wait(lambda: s.frame()[1] > seen) and s.proc is not proc
    s.stop()
    assert s.proc.poll() is not None


@needs_ffmpeg
def test_video_non_looping_file_ends_and_keeps_last_frame(clip):
    s = FfmpegStream(str(clip), fps=10, loop=False)
    s.touch((64, 48))
    assert wait(lambda: s.status == "ended")
    assert s.frame() is not None and s.proc.poll() is not None
    s.stop()


@needs_ffmpeg
def test_video_stream_reconnects_after_failure(monkeypatch, tmp_path):
    s = FfmpegStream("http://127.0.0.1:1/none.mjpg", fps=5)    # nothing listens: ffmpeg exits at once
    s.touch((64, 48))
    assert wait(lambda: s.status == "reconnecting…")
    s.stop()
    assert s.proc.poll() is not None


@needs_ffmpeg
def test_camera_stream_source_draws_frames(clip):
    # a local file stands in for an RTSP source: same ffmpeg path, no network
    page, w = make("camera", {"name": "Garage", "stream_url": str(clip), "rounded": False}, grid=(4, 3))
    assert w.fps == camera_mod.STREAM_FPS
    page.render()
    assert wait(lambda: w._stream.frame() is not None)
    assert distinct(page.render()) > 10
    w.draw_full(_canvas(), (10, 10, 1006, 582))
    assert w.fps == camera_mod.FULL_FPS
    w._stream.stop()


def _canvas():
    from wigix.render import Canvas
    img = Image.new("RGB", (1016, 592))
    return Canvas(ImageDraw.Draw(img), img)


def test_widgets_declare_editor_fields():
    for name in ("image", "video", "camera"):
        cls = widgets.REGISTRY[name]
        assert cls.fields and all({"name", "type", "default"} <= set(f) for f in cls.fields)
