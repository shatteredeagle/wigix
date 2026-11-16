"""Security hardening: secret scrubbing, header scoping, TLS defaults, auth backoff, download limits, ffmpeg input, doctor."""
import io
import os
import pathlib
import ssl
import time

import pytest
import requests
import urllib3
from PIL import Image

from fake_http import FakeHTTP
from wigix import doctor, widgets
from wigix.integrations import Registry, ical as ical_mod, mpris as mpris_mod, net
from wigix.integrations.bambu import Bambu
from wigix.integrations.polling import PollingIntegration
from wigix.widgets import camera as camera_mod, image as image_mod, video as video_mod

widgets.load_plugins()
ROOT = pathlib.Path(__file__).resolve().parent.parent


def reg(cfg):
    return Registry({"integrations": cfg})


@pytest.fixture
def srv():
    s = FakeHTTP()
    yield s
    s.stop()


# --- 1. scrubbing ------------------------------------------------------------------------------------------
def test_scrub_query_userinfo_and_nested_text():
    assert net.scrub("401 for url: https://h.lan/api?auth=SECRET&x=1") == "401 for url: https://h.lan/api"
    assert net.scrub("failed https://admin:hunter2@h.lan:8080/p") == "failed https://h.lan:8080/p"
    assert net.scrub("Max retries exceeded with url: /admin/api.php?summaryRaw=&auth=SECRET (Caused by X)") == \
        "Max retries exceeded with url: /admin/api.php (Caused by X)"
    assert net.scrub("is it the wrong token?) maybe") == "is it the wrong token?) maybe"      # a plain question mark stays
    inner = requests.ConnectionError("HTTPSConnectionPool(host='h', port=443): url: /x?token=SECRET")
    outer = RuntimeError(f"/v1?key=SECRET2: {inner}")
    text = net.describe_error(outer)
    assert "SECRET" not in text and "token=" not in text and "key=" not in text


def test_polling_refresh_stores_scrubbed_error():
    class Boom(PollingIntegration):
        type_name = "boom"

        def poll(self):
            raise requests.HTTPError("500 Server Error for url: http://u:p@h.lan/a?auth=TOK")

    i = Boom("b", {})
    i.refresh()
    assert i.error == "500 Server Error for url: http://h.lan/a"


def test_pihole_v5_token_never_leaks(srv):
    srv.routes[("GET", "/admin/api.php")] = (500, {"error": "boom"})
    i = reg({"p": {"type": "pihole", "url": srv.url, "version": 5, "api_token": "TOPSECRET"}}).get("p", "pihole")
    i.refresh()
    assert i.error and "TOPSECRET" not in i.error and "auth=" not in i.error
    srv.stop()                                                    # connection refused: urllib3 quotes the URL too
    i.refresh()
    assert i.error and "TOPSECRET" not in i.error and "auth=" not in i.error


def test_http_integration_scrubs_query_in_path(srv):
    srv.routes[("GET", "/v1/x")] = (500, {})
    i = reg({"a": {"type": "http", "base_url": srv.url, "paths": ["/v1/x?key=SECRET"]}}).get("a", "http")
    i.refresh()
    assert i.error and "SECRET" not in i.error


def test_bambu_and_ha_errors_scrubbed():
    b = Bambu("p", {"host": "h", "serial": "s"})
    b._on_disconnect(None, None, None, "lost https://u:pw@h/x?code=SECRET")
    assert "SECRET" not in b.error and "pw" not in b.error
    ha = reg({"ha": {"type": "home_assistant", "url": "http://h"}}).get("ha", "home_assistant")
    ha._on_error(None, RuntimeError("bad https://h/api?token=SECRET"))
    assert "SECRET" not in ha.error


# --- 2. headers only to base_url ---------------------------------------------------------------------------
def test_http_headers_only_sent_to_base_url(srv):
    other = FakeHTTP({("GET", "/elsewhere"): {"v": 2}})
    try:
        srv.routes[("GET", "/mine")] = {"v": 1}
        i = reg({"a": {"type": "http", "base_url": srv.url, "token": "tok", "headers": {"X-Api-Key": "k"},
                       "paths": ["/mine", other.url + "/elsewhere"]}}).get("a", "http")
        i.refresh()
        assert i.data == {"/mine": {"v": 1}, other.url + "/elsewhere": {"v": 2}}
        assert srv.requests[0].headers["authorization"] == "Bearer tok" and srv.requests[0].headers["x-api-key"] == "k"
        assert "authorization" not in other.requests[0].headers and "x-api-key" not in other.requests[0].headers
    finally:
        other.stop()


def test_http_origin_compares_scheme_host_and_port():
    from wigix.integrations.http import _origin
    assert _origin("https://A.example/x") == _origin("https://a.example:443/y")
    assert _origin("https://a.example") != _origin("http://a.example")
    assert _origin("https://a.example") != _origin("https://a.example:8443")
    assert _origin("https://a.example") != _origin("https://b.example")


# --- 3. TLS ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("itype,cfg", [("proxmox", {"token_id": "u@pam!n", "token": "t"}),
                                      ("unraid", {"api_key": "k"}), ("unifi", {})])
def test_tls_verifies_by_default_and_accepts_ca_path(monkeypatch, itype, cfg):
    calls = []
    monkeypatch.setattr(urllib3, "disable_warnings", lambda *a, **k: calls.append(a))

    def verify_of(i):
        return i.verify if hasattr(i, "verify") else i.s.verify

    base = {"type": itype, "url": "https://h.lan", **cfg}
    assert verify_of(reg({"x": base}).get("x", itype)) is True and not calls
    ca = verify_of(reg({"x": {**base, "verify": "~/pve-ca.pem"}}).get("x", itype))
    assert ca == os.path.expanduser("~/pve-ca.pem") and not calls
    assert verify_of(reg({"x": {**base, "verify": False}}).get("x", itype)) is False and calls    # only when explicit


def test_tls_verify_value_forms():
    assert net.tls_verify(None) is True and net.tls_verify("") is True
    assert net.tls_verify("true") is True and net.tls_verify(True) is True
    assert net.tls_verify("/etc/ca.pem") == "/etc/ca.pem"
    assert net.tls_verify(False, default=True) is False


def test_certificate_error_gets_a_helpful_message():
    verify = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate")
    wrapped = requests.exceptions.SSLError(urllib3.exceptions.MaxRetryError(None, "https://h/x?t=1", verify))

    class Boom(PollingIntegration):
        type_name = "boom"

        def poll(self):
            raise wrapped

    i = Boom("b", {})
    i.refresh()
    assert i.error == net.TLS_HINT and "verify: false" in i.error and "ca.pem" in i.error


def test_docker_context_uses_ca_file_or_disables_checks(tmp_path):
    assert net.ssl_context(False).verify_mode == ssl.CERT_NONE
    assert net.ssl_context(True).verify_mode == ssl.CERT_REQUIRED


# --- 4. auth failure backoff --------------------------------------------------------------------------------
def test_unifi_bad_password_stops_retrying(srv):
    srv.routes[("POST", "/api/auth/login")] = (401, {"error": "no"})
    i = reg({"u": {"type": "unifi", "url": srv.url, "username": "me", "password": "x", "verify": False}}).get("u", "unifi")
    i.refresh()
    assert i.auth_failed and i.error == net.AUTH_FAILED
    i.refresh()
    i.refresh()
    assert len(srv.requests) == 1                                  # no more login attempts
    i._stop.clear()
    i.start()
    time.sleep(0.2)
    assert len(srv.requests) == 1 and not i._thread.is_alive()


def test_unifi_network_error_is_not_an_auth_failure():
    i = reg({"u": {"type": "unifi", "url": "http://127.0.0.1:9", "username": "me", "password": "x"}}).get("u", "unifi")
    i.refresh()
    assert i.error and not i.auth_failed


def test_pihole_v6_rejected_password_stops_retrying(srv):
    srv.routes[("POST", "/api/auth")] = (401, {"session": {"valid": False, "message": "password incorrect"}})
    i = reg({"p": {"type": "pihole", "url": srv.url, "password": "nope"}}).get("p", "pihole")
    i.refresh()
    i.refresh()
    assert i.auth_failed and i.error == net.AUTH_FAILED and len(srv.requests) == 1


def test_adguard_401_stops_retrying(srv):
    srv.routes[("GET", "/control/stats")] = (401, {})
    i = reg({"a": {"type": "adguard", "url": srv.url, "username": "u", "password": "p"}}).get("a", "adguard")
    i.refresh()
    i.refresh()
    assert i.auth_failed and i.error == net.AUTH_FAILED and len(srv.requests) == 1
    srv.routes[("GET", "/control/stats")] = (500, {})
    j = reg({"a": {"type": "adguard", "url": srv.url, "username": "u", "password": "p"}}).get("a", "adguard")
    j.refresh()
    assert j.error and not j.auth_failed                           # a server error is transient


def test_bambu_not_authorized_stops_reconnecting():
    import paho.mqtt.client as mqtt
    b = Bambu("p", {"host": "h", "serial": "s"})

    class Client:
        disconnected = False

        def disconnect(self):
            self.disconnected = True

    c = Client()
    b._on_connect(c, None, None, mqtt.convert_connack_rc_to_reason_code(5))
    assert b.auth_failed and b.error == net.AUTH_FAILED and c.disconnected and b._stop.is_set()
    b._on_disconnect(c, None, None, mqtt.convert_connack_rc_to_reason_code(5))
    assert b.error == net.AUTH_FAILED                              # the later disconnect does not overwrite it

    t = Bambu("p", {"host": "h", "serial": "s"})
    t._on_connect(Client(), None, None, mqtt.convert_connack_rc_to_reason_code(3))      # server unavailable: transient
    assert not t.auth_failed and not t._stop.is_set() and "connect failed" in t.error


# --- 5. download and read limits ----------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, chunks, headers=None):
        self.chunks, self.headers, self.read = chunks, headers or {}, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        for c in self.chunks:
            self.read += len(c)
            yield c


def test_fetch_limited_stops_reading(monkeypatch):
    resp = FakeResponse(iter([b"x" * 1000] * 1000))
    monkeypatch.setattr(requests, "get", lambda url, **kw: resp if kw.get("stream") else pytest.fail("not streamed"))
    with pytest.raises(ValueError, match="too large"):
        net.fetch_limited("http://h/x", 5000)
    assert resp.read <= 6000                                       # gave up right after the limit, not after 1 MB
    big = FakeResponse(iter([]), {"Content-Length": "999999"})
    monkeypatch.setattr(requests, "get", lambda url, **kw: big)
    with pytest.raises(ValueError, match="too large"):
        net.fetch_limited("http://h/x", 10)
    assert big.read == 0
    monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResponse([b"ab", b"cd"]))
    assert net.fetch_limited("http://h/x", 10) == b"abcd"


def test_image_widget_streams_and_caps(monkeypatch):
    monkeypatch.setattr(image_mod, "MAX_BYTES", 4000)
    resp = FakeResponse(iter([b"x" * 1000] * 10000))
    monkeypatch.setattr(requests, "get", lambda url, **kw: resp if kw.get("stream") else pytest.fail("not streamed"))
    w = image_mod.ImageWidget({"source": "http://h/pic.png?token=SECRET"}, {})
    w.update()
    assert w._loaded is None and resp.read <= 5000
    assert w.error and "SECRET" not in w.error and "too large" in w.error


def test_image_decompression_bomb_is_an_error_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    p = tmp_path / "big.png"
    Image.new("RGB", (30, 30)).save(p)                              # 900 px > 2 * 100
    w = image_mod.ImageWidget({"source": str(p)}, {})
    w.update()
    assert w._loaded is None and w.error and "exceeds" in w.error


def test_camera_snapshot_cap_and_scrubbed_errors(monkeypatch, srv):
    monkeypatch.setattr(camera_mod, "MAX_SNAPSHOT_BYTES", 1000)
    srv.routes[("GET", "/snap.jpg")] = (200, b"\xff" * 5000, {"Content-Type": "image/jpeg"})
    w = camera_mod.CameraWidget({"snapshot_url": srv.url + "/snap.jpg?token=SECRET"}, {})
    with pytest.raises(ValueError, match="too large"):
        w.update()
    srv.routes[("GET", "/snap.jpg")] = (500, {})
    with pytest.raises(RuntimeError) as e:
        w.update()
    assert "SECRET" not in str(e.value) and "token=" not in str(e.value)


def test_camera_decompression_bomb(monkeypatch, srv):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    buf = io.BytesIO()
    Image.new("RGB", (40, 40)).save(buf, "PNG")
    srv.routes[("GET", "/s.png")] = (200, buf.getvalue(), {"Content-Type": "image/png"})
    w = camera_mod.CameraWidget({"snapshot_url": srv.url + "/s.png"}, {})
    with pytest.raises(Image.DecompressionBombError):
        w.update()


def _mpris(monkeypatch):
    monkeypatch.setattr(mpris_mod.shutil, "which", lambda n: None)
    return reg({"m": {"type": "mpris"}}).get("m", "mpris")


def test_mpris_refuses_non_regular_and_big_files(monkeypatch, tmp_path):
    m = _mpris(monkeypatch)
    m._update_art("file:///dev/zero")                               # would read forever if it were opened
    assert m.art_image is None
    m._update_art("file:///dev/null")
    assert m.art_image is None
    big = tmp_path / "big.jpg"
    big.write_bytes(b"0" * 100)
    monkeypatch.setattr(mpris_mod, "MAX_ART_BYTES", 50)
    m._update_art(big.as_uri())
    assert m.art_image is None
    monkeypatch.setattr(mpris_mod, "MAX_ART_BYTES", 8 * 2**20)
    ok = tmp_path / "ok.png"
    Image.new("RGB", (20, 20), (9, 9, 9)).save(ok)
    m._update_art(ok.as_uri())
    assert m.art_image is not None


def test_mpris_stats_before_reading(monkeypatch, tmp_path):
    m = _mpris(monkeypatch)
    f = tmp_path / "x.png"
    f.write_bytes(b"x")
    monkeypatch.setattr(pathlib.Path, "read_bytes", lambda self: pytest.fail("must not read_bytes unchecked"))
    m._update_art(f.as_uri())                                       # not an image, but never reaches read_bytes


def test_mpris_http_art_is_capped(monkeypatch):
    m = _mpris(monkeypatch)
    resp = FakeResponse(iter([b"x" * 1000] * 100000))
    monkeypatch.setattr(requests, "get", lambda url, **kw: resp if kw.get("stream") else pytest.fail("not streamed"))
    monkeypatch.setattr(mpris_mod, "MAX_ART_BYTES", 3000)
    m._update_art("http://h/cover.jpg")
    assert m.art_image is None and resp.read <= 4000


ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//t//t//EN
BEGIN:VEVENT
UID:a
DTSTAMP:20250101T000000Z
DTSTART:{start}
DTEND:{end}
SUMMARY:Often
RRULE:FREQ=MINUTELY
END:VEVENT
END:VCALENDAR
"""


def _ical(tmp_path, **cfg):
    now = ical_mod.dt.datetime.now(ical_mod.dt.timezone.utc)
    f = tmp_path / "c.ics"
    f.write_text(ICS.format(start=now.strftime("%Y%m%dT%H%M%SZ"),
                            end=(now + ical_mod.dt.timedelta(seconds=30)).strftime("%Y%m%dT%H%M%SZ")))
    return reg({"c": {"type": "ical", "file": str(f), "timezone": "UTC", **cfg}}).get("c", "ical")


def test_ical_caps_expansion(tmp_path):
    i = _ical(tmp_path, days=366)
    start = time.monotonic()
    i.refresh()
    assert i.error is None and len(i.data) <= ical_mod.MAX_EVENTS
    assert time.monotonic() - start < 20


@pytest.mark.parametrize("days,expected", [(-5, 1), (0, 1), (100000, ical_mod.MAX_DAYS), ("junk", 7)])
def test_ical_days_clamped(tmp_path, monkeypatch, days, expected):
    i = _ical(tmp_path, days=days)
    seen = []
    real = ical_mod.dt.timedelta
    monkeypatch.setattr(ical_mod.dt, "timedelta", lambda **kw: seen.append(kw.get("days")) or real(**kw))
    i.refresh()
    assert expected in seen


def test_ical_download_error_hides_url(monkeypatch):
    def boom(url, **kw):
        raise requests.ConnectionError(f"cannot reach {url}")

    monkeypatch.setattr(requests, "get", boom)
    i = reg({"c": {"type": "ical", "url": "https://u:p@cal.example/private?secret=ABC"}}).get("c", "ical")
    i.refresh()
    assert i.error == "calendar download failed (ConnectionError)"


# --- 6. ffmpeg input ----------------------------------------------------------------------------------------
def _argv(source):
    return video_mod.FfmpegStream(source)._argv((160, 90))


def test_ffmpeg_argv_has_whitelist_and_prefixed_local_path(tmp_path):
    f = tmp_path / "a.mp4"
    argv = _argv(str(f))
    assert argv[argv.index("-protocol_whitelist") + 1] == "file,http,https,tcp,tls,udp,rtp,rtsp,rtmp,crypto"
    assert "pipe" not in argv[argv.index("-protocol_whitelist") + 1].split(",")
    assert argv.index("-protocol_whitelist") < argv.index("-i")
    assert argv[argv.index("-i") + 1] == "file:" + str(f)
    net = _argv("rtsp://cam.lan/stream")
    assert net[net.index("-i") + 1] == "rtsp://cam.lan/stream" and "-rtsp_transport" in net


def test_ffmpeg_relative_and_home_paths_become_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _argv("clip.mp4")[_argv("clip.mp4").index("-i") + 1] == "file:" + str(tmp_path / "clip.mp4")
    home = _argv("~/x.mp4")
    assert home[home.index("-i") + 1] == "file:" + os.path.expanduser("~/x.mp4")


@pytest.mark.parametrize("source", ["-f lavfi", "--help", "-i", "concat:file:///etc/passwd", "concat:a|b",
                                    "subfile,,start,0,end,0,,:/etc/passwd:x", "data:text/plain,hi",
                                    "pipe:0", "ftp://h/x", "gopher://h/", "file:relative.mp4", ""])
def test_ffmpeg_rejects_dangerous_sources(source):
    s = video_mod.FfmpegStream(source)
    if source.startswith("subfile"):                                # no scheme: just an odd relative file name
        assert s.kind == "file" and "-" not in _argv(source)[_argv(source).index("-i") + 1][:1]
        return
    assert s.kind is None
    s.touch((160, 90))
    assert s.status == "source not allowed" and s.proc is None and s._thread is None


def test_ffmpeg_dangerous_source_shows_placeholder():
    w = video_mod.VideoWidget({"source": "concat:file:///etc/passwd"}, {})
    s = w._ensure_stream()
    s.touch((160, 90))
    assert s.status == "source not allowed" and "source not allowed" in video_mod.BAD_SOURCE


def test_ffmpeg_file_url_is_a_local_path(tmp_path):
    f = tmp_path / "a b.mp4"
    argv = _argv(f.as_uri())
    assert argv[argv.index("-i") + 1] == "file:" + str(f)


# --- 7. doctor ---------------------------------------------------------------------------------------------
def test_doctor_checks_every_file_key(tmp_path):
    names = ["password_file", "api_key_file", "url_file", "access_code_file", "token_file"]
    cfg = {"integrations": {f"i{n}": {n: str(tmp_path / n)} for n in names},
           "pages": [{"widgets": [{"type": "x", "key_file": str(tmp_path / "w_file")}]}]}
    for n in names + ["w_file"]:
        (tmp_path / n).write_text("s")
        os.chmod(tmp_path / n, 0o644 if n == "access_code_file" else 0o600)
    res = {r.name.split()[-1]: r for r in doctor.check_tokens(cfg)}
    assert len(res) == 6
    assert res[str(tmp_path / "access_code_file")].status == doctor.WARN
    assert res[str(tmp_path / "url_file")].status == doctor.PASS


def test_doctor_config_file_permissions(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text("x")
    secret = {"integrations": {"a": {"type": "home_assistant", "token": "abc"}}}
    clean = {"integrations": {"a": {"type": "home_assistant", "token_file": "~/t", "token_env": "HA"}}}
    os.chmod(p, 0o644)
    (r,) = doctor.check_config_permissions(p, secret)
    assert r.status == doctor.WARN and "token" in r.detail and "chmod 600" in r.fix
    (r,) = doctor.check_config_permissions(p, {"a": {"api_key": "k"}, "b": {"password": "pw"}, "c": {"access_code": 12345678}})
    assert r.status == doctor.WARN and "api_key" in r.detail and "access_code" in r.detail
    (r,) = doctor.check_config_permissions(p, clean)
    assert r.status == doctor.PASS and "no inline secrets" in r.detail
    os.chmod(p, 0o600)
    (r,) = doctor.check_config_permissions(p, secret)
    assert r.status == doctor.PASS
    assert doctor.check_config_permissions(p, None) == []


# --- 8. packaging ------------------------------------------------------------------------------------------
def test_compose_dockerfile_and_ci_hardening():
    compose = (ROOT / "packaging" / "docker-compose.yml").read_text()
    live = "\n".join(ln for ln in compose.splitlines() if not ln.lstrip().startswith("#"))
    assert '"127.0.0.1:8765:8765"' in live
    assert "devices:" not in live and "privileged" not in live
    assert "device_cgroup_rules" in live and "c 189:* rmw" in live and "group_add" in live
    assert "/dev/bus/usb:/dev/bus/usb:ro" in live
    docker = (ROOT / "packaging" / "Dockerfile").read_text()
    assert "\nUSER wigix" in docker and "useradd" in docker
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "permissions:\n  contents: read" in ci
