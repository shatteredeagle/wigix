# SPDX-License-Identifier: GPL-3.0-only
"""Pi-hole, AdGuard, UniFi, Open-Meteo, iCal and MPRIS integrations with their widgets (fake HTTP / monkeypatching only)."""
import base64
import datetime as dt
import subprocess
import time
import zoneinfo

import pytest
from PIL import Image

from fake_http import FakeHTTP
from wigix import widgets
from wigix.engine import Page
from wigix.integrations import Registry, mpris as mpris_mod
from wigix.widgets import calendar_agenda, weather

widgets.load_plugins()


def reg(cfg):
    return Registry({"integrations": cfg})


def _with(name, integ):
    """A registry holding an already-built integration (so tests can set its data directly)."""
    r = reg({})
    r.items[name] = integ
    return r


def render(wtype, r, at=(0, 0, 1, 1), grid=(1, 1), **cfg):
    """Render one widget through a Page and return (image, widget)."""
    page = Page({"grid": {"cols": grid[0], "rows": grid[1]}, "widgets": [{"type": wtype, "at": list(at), **cfg}]},
                {"integrations": r})
    return page.render(), page.items[0][0]


def drawn(img):
    return len(img.getcolors(100000)) > 6


@pytest.fixture
def srv():
    s = FakeHTTP()
    yield s
    s.stop()


# --- Pi-hole -------------------------------------------------------------------------------------------

def pihole_v6(srv, password="hunter2"):
    """A v6 server whose sessions can be invalidated by changing state['sid']."""
    state = {"sid": "sid-1", "blocking": "enabled", "logins": 0}

    def auth(req):
        if req.json().get("password") != password:
            return 401, {"session": {"valid": False, "sid": None, "message": "password incorrect"}}
        state["logins"] += 1
        return {"session": {"valid": True, "sid": state["sid"], "validity": 300}}

    def need_sid(fn):
        return lambda req: fn(req) if req.headers.get("sid") == state["sid"] else (401, {"error": {"message": "unauthorized"}})

    def blocking(req):
        if req.method == "POST":
            state["blocking"] = "enabled" if req.json()["blocking"] else "disabled"
        return {"blocking": state["blocking"], "timer": None}

    srv.routes.update({
        ("POST", "/api/auth"): auth,
        ("GET", "/api/stats/summary"): need_sid(lambda req: {
            "queries": {"total": 12345, "blocked": 3086, "percent_blocked": 25.0},
            "clients": {"active": 14, "total": 20}, "gravity": {"domains_being_blocked": 98765}}),
        ("*", "/api/dns/blocking"): need_sid(blocking)})
    return state


def test_pihole_v6_login_and_normalised_data(srv):
    pihole_v6(srv)
    i = reg({"p": {"type": "pihole", "url": srv.url, "password": "hunter2"}}).get("p", "pihole")
    i.refresh()
    assert i.connected
    assert i.data == {"kind": "pihole", "queries": 12345, "blocked": 3086, "percent_blocked": 25.0, "enabled": True,
                      "domains_blocked": 98765, "clients": 14}
    assert srv.requests[0].json() == {"password": "hunter2"}
    assert srv.requests[1].headers["sid"] == "sid-1"


def test_pihole_v6_reauth_on_401_and_bad_password(srv):
    state = pihole_v6(srv)
    i = reg({"p": {"type": "pihole", "url": srv.url, "password": "hunter2"}}).get("p", "pihole")
    i.refresh()
    state["sid"] = "sid-2"                         # the old session expired on the server
    i.refresh()
    assert i.connected and state["logins"] == 2 and i.sid == "sid-2"
    bad = reg({"p": {"type": "pihole", "url": srv.url, "password": "nope"}}).get("p", "pihole")
    bad.refresh()
    assert bad.data is None and bad.auth_failed and "authentication failed" in bad.error


def test_pihole_error_keeps_previous_data(srv):
    pihole_v6(srv)
    i = reg({"p": {"type": "pihole", "url": srv.url, "password": "hunter2"}}).get("p", "pihole")
    i.refresh()
    srv.routes[("GET", "/api/stats/summary")] = (500, {"error": "boom"})
    i.refresh()
    assert i.error and i.data["queries"] == 12345 and not i.connected


def test_pihole_v6_without_password_sends_no_session(srv):
    pihole_v6(srv)["sid"] = None                   # open instance: no password, no sid header expected
    srv.routes[("GET", "/api/stats/summary")] = {"queries": {"total": 5, "blocked": 1, "percent_blocked": 20}}
    srv.routes[("GET", "/api/dns/blocking")] = {"blocking": "disabled"}
    i = reg({"p": {"type": "pihole", "url": srv.url}}).get("p", "pihole")
    i.refresh()
    assert i.data["queries"] == 5 and i.data["enabled"] is False and i.data["domains_blocked"] == 0
    assert not any(r.path == "/api/auth" for r in srv.requests)


def test_pihole_v5_token(srv):
    srv.routes[("GET", "/admin/api.php")] = lambda req: (
        {"dns_queries_today": 2000, "ads_blocked_today": 500, "ads_percentage_today": 25.0, "status": "enabled",
         "domains_being_blocked": 90000, "unique_clients": 7}
        if req.query.get("auth") == ["tok"] else [])      # v5 answers a bad token with an empty list
    ok = reg({"p": {"type": "pihole", "url": srv.url, "version": 5, "api_token": "tok"}}).get("p", "pihole")
    ok.refresh()
    assert ok.data["queries"] == 2000 and ok.data["blocked"] == 500 and ok.data["enabled"] and not ok.can_toggle
    bad = reg({"p": {"type": "pihole", "url": srv.url, "version": 5, "api_token": "x"}}).get("p", "pihole")
    bad.refresh()
    assert bad.data is None and "api_token" in bad.error
    with pytest.raises(NotImplementedError):
        ok.set_blocking(False)


def test_pihole_set_blocking(srv):
    state = pihole_v6(srv)
    i = reg({"p": {"type": "pihole", "url": srv.url, "password": "hunter2"}}).get("p", "pihole")
    i.refresh()
    i.set_blocking(False)
    post = [r for r in srv.requests if r.method == "POST" and r.path == "/api/dns/blocking"][0]
    assert post.json()["blocking"] is False
    assert state["blocking"] == "disabled" and i.data["enabled"] is False        # refreshed after the action


# --- AdGuard Home --------------------------------------------------------------------------------------

def adguard(srv):
    want = "Basic " + base64.b64encode(b"admin:pw").decode()
    guard = lambda body: lambda req: body if req.headers.get("authorization") == want else (401, "Unauthorized")
    srv.routes.update({("GET", "/control/stats"): guard({"num_dns_queries": 1000, "num_blocked_filtering": 150,
                                                         "num_replaced_safebrowsing": 5, "num_replaced_parental": 5,
                                                         "avg_processing_time": 0.002}),
                       ("GET", "/control/status"): guard({"protection_enabled": True, "running": True, "version": "v0.107"})})


def test_adguard_basic_auth_and_normalisation(srv):
    adguard(srv)
    i = reg({"a": {"type": "adguard", "url": srv.url, "username": "admin", "password": "pw"}}).get("a", "adguard")
    i.refresh()
    assert i.data["kind"] == "adguard" and i.data["queries"] == 1000 and i.data["blocked"] == 160
    assert i.data["percent_blocked"] == pytest.approx(16.0) and i.data["enabled"] is True
    bad = reg({"a": {"type": "adguard", "url": srv.url, "username": "admin", "password": "wrong"}}).get("a", "adguard")
    bad.refresh()
    assert bad.data is None and bad.error


def test_adguard_zero_queries_and_missing_keys(srv):
    srv.routes.update({("GET", "/control/stats"): {"avg_processing_time": 0}, ("GET", "/control/status"): {"running": True}})
    i = reg({"a": {"type": "adguard", "url": srv.url}}).get("a", "adguard")
    i.refresh()
    assert i.data["queries"] == 0 and i.data["percent_blocked"] == 0.0 and i.data["enabled"] is False


def dns_page(r, **cfg):
    return render("dns_stats", r, **cfg)


def test_dns_widget_renders_for_both_backends(srv):
    pihole_v6(srv)
    srv2 = FakeHTTP()
    adguard(srv2)
    try:
        for cfg, kind in (({"p": {"type": "pihole", "url": srv.url, "password": "hunter2"}}, "pihole"),
                          ({"a": {"type": "adguard", "url": srv2.url, "username": "admin", "password": "pw"}}, "adguard")):
            r = reg(cfg)
            r.get(None, kind).refresh()
            img, _ = dns_page(r)                           # single instance: no `integration:` needed
            assert drawn(img)
    finally:
        srv2.stop()


def test_dns_widget_integration_lookup(srv):
    pihole_v6(srv)
    r = reg({"p": {"type": "pihole", "url": srv.url, "password": "hunter2"},
             "a": {"type": "adguard", "url": srv.url, "username": "admin", "password": "pw"}})
    r.items["p"].refresh()
    _, w = dns_page(r, integration="p")
    assert w.integ is r.items["p"]
    _, w = dns_page(r)                                     # ambiguous without a name
    with pytest.raises(KeyError):
        w.integ
    img, _ = dns_page(r)                                   # ... which renders as a placeholder, not an exception
    assert len(img.getcolors(100000)) > 3


def test_dns_widget_edge_data_and_placeholders(srv):
    r = reg({"p": {"type": "pihole", "url": "http://127.0.0.1:1", "password": "x"}})
    assert len(dns_page(r)[0].getcolors(100000)) > 3                                   # offline/loading
    assert len(dns_page(reg({}))[0].getcolors(100000)) > 3                             # nothing configured
    r.items["p"].data = {"queries": 0, "blocked": 0, "percent_blocked": 0, "enabled": False}   # paused, minimal keys
    assert drawn(dns_page(r, at=(0, 0, 1, 1))[0])
    r.items["p"].data = {"queries": 4_200_000, "blocked": 1_000_000, "percent_blocked": 100, "enabled": True}
    assert drawn(render("dns_stats", r, at=(0, 0, 2, 2), grid=(4, 3))[0])


def test_dns_tap_toggle_is_opt_in(srv):
    state = pihole_v6(srv)
    r = reg({"p": {"type": "pihole", "url": srv.url, "password": "hunter2"}})
    r.items["p"].refresh()
    _, off = dns_page(r)
    assert off.on_tap(5, 5) is False                       # default: taps do nothing (no accidental DNS outage)
    _, on = dns_page(r, tap_toggle=True)
    assert on.on_tap(5, 5) is True
    for _ in range(50):                                    # the toggle runs on a thread
        if state["blocking"] == "disabled":
            break
        time.sleep(0.05)
    assert state["blocking"] == "disabled"


def test_dns_tap_toggle_ignored_for_v5_and_adguard(srv):
    adguard(srv)
    r = reg({"a": {"type": "adguard", "url": srv.url, "username": "admin", "password": "pw"}})
    r.items["a"].refresh()
    assert dns_page(r, tap_toggle=True)[1].on_tap(1, 1) is False


# --- UniFi ---------------------------------------------------------------------------------------------

HEALTH = {"data": [
    {"subsystem": "wan", "status": "ok", "wan_ip": "203.0.113.7", "rx_bytes-r": 1_250_000, "tx_bytes-r": 125_000,
     "num_adopted": 1, "num_disconnected": 0},
    {"subsystem": "lan", "status": "ok", "num_adopted": 2, "num_disconnected": 1},
    {"subsystem": "wlan", "status": "ok", "num_adopted": 3, "num_disconnected": 0},
    {"subsystem": "vpn", "status": "unknown"}]}
STA = {"data": [{"mac": "aa", "is_wired": True}, {"mac": "bb", "is_wired": False}, {"mac": "cc"}, {"mac": "dd", "is_wired": False}]}


def unifi(srv):
    state = {"token": "t1", "logins": 0}

    def login(req):
        body = req.json()
        if body["password"] != "pw":
            return 403, {"error": "bad credentials"}
        state["logins"] += 1
        return 200, {"unique_id": "x"}, {"Set-Cookie": f"TOKEN={state['token']}; Path=/", "X-CSRF-Token": f"csrf-{state['logins']}"}

    def authed(body):
        return lambda req: body if f"TOKEN={state['token']}" in req.headers.get("cookie", "") else (401, {"error": "no"})

    srv.routes.update({("POST", "/api/auth/login"): login,
                       ("GET", "/proxy/network/api/s/default/stat/health"): authed(HEALTH),
                       ("GET", "/proxy/network/api/s/default/stat/sta"): authed(STA)})
    return state


def test_unifi_login_and_normalisation(srv):
    state = unifi(srv)
    i = reg({"u": {"type": "unifi", "url": srv.url, "username": "me", "password": "pw"}}).get("u", "unifi")
    i.refresh()
    assert i.data == {"wan": {"status": "ok", "ip": "203.0.113.7", "rx_bps": 10_000_000.0, "tx_bps": 1_000_000.0},
                      "clients": 4, "wifi_clients": 3, "wired_clients": 1, "devices_up": 6, "devices_down": 1}
    assert state["logins"] == 1
    assert srv.requests[1].headers["x-csrf-token"] == "csrf-1"        # token from the login reply is echoed back
    i.refresh()
    assert state["logins"] == 1                                       # the cookie session is reused


def test_unifi_relogin_on_401(srv):
    state = unifi(srv)
    i = reg({"u": {"type": "unifi", "url": srv.url, "username": "me", "password": "pw", "site": "default"}}).get("u", "unifi")
    i.refresh()
    state["token"] = "t2"                                             # console restarted: old cookie is dead
    i.refresh()
    assert i.connected and state["logins"] == 2


def test_unifi_bad_password_and_error_keeps_data(srv):
    state = unifi(srv)
    bad = reg({"u": {"type": "unifi", "url": srv.url, "username": "me", "password": "x"}}).get("u", "unifi")
    bad.refresh()
    assert bad.data is None and bad.error
    i = reg({"u": {"type": "unifi", "url": srv.url, "username": "me", "password": "pw"}}).get("u", "unifi")
    i.refresh()
    srv.routes[("GET", "/proxy/network/api/s/default/stat/sta")] = (500, {})
    i.refresh()
    assert i.error and i.data["clients"] == 4


def test_unifi_missing_wan_and_empty_site(srv):
    unifi(srv)
    srv.routes[("GET", "/proxy/network/api/s/default/stat/health")] = {"data": []}
    srv.routes[("GET", "/proxy/network/api/s/default/stat/sta")] = {"data": []}
    i = reg({"u": {"type": "unifi", "url": srv.url, "username": "me", "password": "pw"}}).get("u", "unifi")
    i.refresh()
    assert i.data["wan"]["status"] == "unknown" and i.data["clients"] == 0 and i.data["devices_up"] == 0
    assert drawn(render("unifi", _with("u", i))[0])


def test_unifi_widget_renders_sizes_and_states(srv):
    unifi(srv)
    r = reg({"u": {"type": "unifi", "url": srv.url, "username": "me", "password": "pw"}})
    r.items["u"].refresh()
    assert drawn(render("unifi", r)[0])
    assert drawn(render("unifi", r, at=(0, 0, 2, 2), grid=(4, 3))[0])
    r.items["u"].data["wan"]["status"] = "error"                      # critical colour path
    assert drawn(render("unifi", r)[0])
    assert len(render("unifi", reg({}))[0].getcolors(100000)) > 3


# --- Open-Meteo ----------------------------------------------------------------------------------------

FORECAST = {"current": {"temperature_2m": 7.4, "apparent_temperature": 5.1, "relative_humidity_2m": 81, "weather_code": 61,
                        "wind_speed_10m": 14.2, "is_day": 1},
            "daily": {"time": ["2026-11-16", "2026-11-17", "2026-11-18"], "weather_code": [61, 3, 0],
                      "temperature_2m_max": [9.1, 8.0, 11.5], "temperature_2m_min": [3.2, 2.0, 4.0]}}


def test_open_meteo_params_and_data(srv):
    srv.routes[("GET", "/v1/forecast")] = FORECAST
    i = reg({"w": {"type": "open_meteo", "latitude": 48.8566, "longitude": 2.3522, "base_url": srv.url}}).get("w", "open_meteo")
    assert i.interval == 600 and float(i.cfg.get("interval", i.interval)) == 600
    i.refresh()
    q = srv.requests[0].query
    assert q["latitude"] == ["48.8566"] and q["temperature_unit"] == ["celsius"] and q["forecast_days"] == ["3"]
    assert i.data["current"]["temp"] == 7.4 and i.data["units"] == {"temp": "°C", "wind": "km/h"}
    assert [d["code"] for d in i.data["daily"]] == [61, 3, 0] and i.data["daily"][0]["hi"] == 9.1


def test_open_meteo_imperial_and_error(srv):
    srv.routes[("GET", "/v1/forecast")] = FORECAST
    i = reg({"w": {"type": "open_meteo", "latitude": 1, "longitude": 2, "units": "imperial", "base_url": srv.url + "/"}}).get("w", "open_meteo")
    i.refresh()
    q = srv.requests[0].query
    assert q["temperature_unit"] == ["fahrenheit"] and q["wind_speed_unit"] == ["mph"] and i.data["units"]["temp"] == "°F"
    srv.routes[("GET", "/v1/forecast")] = (429, {"reason": "limit"})
    i.refresh()
    assert i.error and i.data is not None


def test_open_meteo_missing_daily(srv):
    srv.routes[("GET", "/v1/forecast")] = {"current": {"temperature_2m": 1.0}}
    i = reg({"w": {"type": "open_meteo", "latitude": 1, "longitude": 2, "base_url": srv.url}}).get("w", "open_meteo")
    i.refresh()
    assert i.data["daily"] == [] and i.data["current"]["code"] is None
    assert drawn(render("weather", _with("w", i))[0])


def test_wmo_descriptions_and_unknown_code():
    assert weather.describe(0) == ("Clear", "sun")
    assert weather.describe(95)[1] == "thunder"
    assert weather.describe(1234) == ("Code 1234", None)           # no icon, still readable text
    assert weather.describe(None) == ("Unknown", None)


@pytest.mark.parametrize("code", [0, 2, 3, 45, 63, 75, 95, 1234, None])
def test_weather_widget_renders_every_icon_kind(srv, code):
    data = {"current": {"temp": 7.4, "feels_like": 5, "humidity": 81, "wind": 14.2, "code": code, "is_day": True},
            "daily": [{"date": "2026-11-16", "code": code, "hi": 9.1, "lo": 3.2},
                      {"date": "bad-date", "code": 3, "hi": None, "lo": None},
                      {"date": "2026-11-18", "code": 0, "hi": 11.5, "lo": 4}],
            "units": {"temp": "°C", "wind": "km/h"}}
    r = reg({"w": {"type": "open_meteo", "latitude": 1, "longitude": 2}})
    r.items["w"].data = data
    for at, grid in (((0, 0, 1, 1), (4, 3)), ((0, 0, 2, 2), (4, 3)), ((0, 0, 1, 1), (1, 1))):
        assert drawn(render("weather", r, at=at, grid=grid)[0])


def test_weather_placeholders():
    assert len(render("weather", reg({}))[0].getcolors(100000)) > 3
    r = reg({"w": {"type": "open_meteo", "latitude": 1, "longitude": 2, "base_url": "http://127.0.0.1:1"}})
    r.items["w"].refresh()
    assert r.items["w"].error and len(render("weather", r)[0].getcolors(100000)) > 3


# --- iCal ----------------------------------------------------------------------------------------------

NY = zoneinfo.ZoneInfo("America/New_York")
NOW = dt.datetime(2026, 11, 16, 10, 0, tzinfo=NY)                   # a Monday

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:over@example.com
DTSTAMP:20261101T000000Z
DTSTART:20261116T130000Z
DTEND:20261116T140000Z
SUMMARY:Already over
END:VEVENT
BEGIN:VEVENT
UID:standup@example.com
DTSTAMP:20261101T000000Z
DTSTART:20261116T190000Z
DTEND:20261116T193000Z
SUMMARY:Standup
LOCATION:Room 4
END:VEVENT
BEGIN:VEVENT
UID:holiday@example.com
DTSTAMP:20261101T000000Z
DTSTART;VALUE=DATE:20261117
DTEND;VALUE=DATE:20261118
SUMMARY:Public holiday
END:VEVENT
BEGIN:VEVENT
UID:trip@example.com
DTSTAMP:20261101T000000Z
DTSTART;VALUE=DATE:20261115
DTEND;VALUE=DATE:20261118
SUMMARY:Conference
END:VEVENT
BEGIN:VEVENT
UID:weekly@example.com
DTSTAMP:20261101T000000Z
DTSTART;TZID=Europe/Paris:20261104T180000
DTEND;TZID=Europe/Paris:20261104T190000
RRULE:FREQ=WEEKLY;BYDAY=WE
SUMMARY:Weekly sync
END:VEVENT
BEGIN:VEVENT
UID:floating@example.com
DTSTAMP:20261101T000000Z
DTSTART:20261119T090000
DTEND:20261119T100000
SUMMARY:Floating
END:VEVENT
BEGIN:VEVENT
UID:far@example.com
DTSTAMP:20261101T000000Z
DTSTART:20261224T090000Z
SUMMARY:Too far ahead
END:VEVENT
BEGIN:VEVENT
UID:untitled@example.com
DTSTAMP:20261101T000000Z
DTSTART:20261120T150000Z
END:VEVENT
END:VCALENDAR
"""


def cal(tmp_path=None, srv=None, **cfg):
    base = {"type": "ical", "timezone": "America/New_York", **cfg}
    i = reg({"c": base}).get("c", "ical")
    i._now = lambda: NOW
    return i


def test_ical_file_expands_and_normalises(tmp_path):
    f = tmp_path / "c.ics"
    f.write_text(ICS)
    i = cal(file=str(f))
    i.refresh()
    assert i.connected, i.error
    titles = [e["title"] for e in i.data]
    assert titles == ["Conference", "Standup", "Public holiday", "Weekly sync", "Floating", "(no title)"]
    by = {e["title"]: e for e in i.data}
    assert "Already over" not in by and "Too far ahead" not in by            # ended earlier today / beyond `days`
    standup = by["Standup"]
    assert standup["start"] == dt.datetime(2026, 11, 16, 14, 0, tzinfo=NY) and not standup["all_day"]   # UTC -> local
    assert standup["location"] == "Room 4" and by["Floating"]["start"] == dt.datetime(2026, 11, 19, 9, 0, tzinfo=NY)
    assert by["Public holiday"]["all_day"] and by["Public holiday"]["end"] == dt.datetime(2026, 11, 18, tzinfo=NY)
    assert by["Conference"]["start"] < NOW and by["Conference"]["all_day"]    # multi-day, began yesterday
    assert by["Weekly sync"]["start"] == dt.datetime(2026, 11, 18, 12, 0, tzinfo=NY)   # 18:00 Paris, a recurrence


def test_ical_days_option_limits_window(tmp_path):
    f = tmp_path / "c.ics"
    f.write_text(ICS)
    i = cal(file=str(f), days=2)
    i.refresh()
    assert [e["title"] for e in i.data] == ["Conference", "Standup", "Public holiday"]


def test_ical_url_and_url_file_and_secret_not_leaked(srv, tmp_path):
    srv.routes[("GET", "/private-secret-token.ics")] = ICS
    uf = tmp_path / "url"
    uf.write_text(srv.url + "/private-secret-token.ics\n")
    i = cal(url_file=str(uf))
    i.refresh()
    assert i.connected and len(i.data) == 6
    srv.routes[("GET", "/private-secret-token.ics")] = (404, "gone")
    i.refresh()
    assert i.error == "calendar download failed (404)" and "secret-token" not in i.error and i.data   # keeps old data
    dead = cal(url="http://127.0.0.1:1/private-secret-token.ics")
    dead.refresh()
    assert dead.error and "secret-token" not in dead.error


def test_ical_misconfigured_and_garbage(tmp_path):
    i = cal()
    i.refresh()
    assert "no calendar configured" in i.error
    f = tmp_path / "bad.ics"
    f.write_text("this is not a calendar")
    bad = cal(file=str(f))
    bad.refresh()
    assert bad.error and bad.data is None
    f.write_text("BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n")
    empty = cal(file=str(f))
    empty.refresh()
    assert empty.data == [] and empty.connected


def agenda(i, **cfg):
    r = reg({})
    r.items["c"] = i
    return render("calendar_agenda", r, **cfg)


def test_calendar_widget_labels_and_layouts(tmp_path, monkeypatch):
    f = tmp_path / "c.ics"
    f.write_text(ICS)
    i = cal(file=str(f))
    i.refresh()
    monkeypatch.setattr(calendar_agenda, "_now", lambda tz: NOW)
    w = agenda(i)[1]
    today = NOW.date()
    ev = {e["title"]: e for e in i.data}
    assert w._when(ev["Standup"], today, "%H:%M") == "Today 14:00"
    assert w._when(ev["Public holiday"], today, "%H:%M") == "Tomorrow all day"
    assert w._when(ev["Conference"], today, "%H:%M") == "Today all day"
    assert w._when(ev["Floating"], today, "%I:%M %p") == "Thu 09:00 AM"
    assert calendar_agenda.day_label(dt.date(2026, 12, 3), today) == "Dec 3"
    for kwargs in ({}, {"max_events": 1}, {"max_events": 20}):
        assert drawn(agenda(i, **kwargs)[0])
    assert drawn(render("calendar_agenda", _with("c", i), at=(0, 0, 2, 2), grid=(4, 3))[0])
    assert drawn(render("calendar_agenda", _with("c", i), at=(0, 0, 4, 1), grid=(4, 3))[0])


def test_calendar_widget_empty_and_missing(tmp_path):
    i = cal()
    i.data = []
    assert drawn(agenda(i)[0])                                              # "nothing planned" state
    assert len(render("calendar_agenda", reg({}))[0].getcolors(100000)) > 3
    i.data, i.error = None, "boom"
    assert len(agenda(i)[0].getcolors(100000)) > 3


# --- MPRIS ---------------------------------------------------------------------------------------------

SEP = mpris_mod.SEP


def player(monkeypatch, out=None, rc=0, err="", installed=True):
    """Patch playerctl: returns the list of recorded argument vectors."""
    calls = []
    monkeypatch.setattr(mpris_mod.shutil, "which", lambda n: "/usr/bin/playerctl" if installed else None)

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, out if cmd[1] == "metadata" else "", err)
    monkeypatch.setattr(mpris_mod.subprocess, "run", run)
    return calls


def line(status="Playing", artist="Some Artist", title="A Song", album="An Album", art="", pos=65_000_000, length=215_000_000, name="spotify"):
    return SEP.join([status, artist, title, album, art, str(pos), str(length), name]) + "\n"


def media(**cfg):
    return reg({"m": {"type": "mpris", **cfg}}).get("m", "mpris")


def test_mpris_poll_parses_metadata(monkeypatch):
    calls = player(monkeypatch, line())
    i = media()
    i.refresh()
    assert i.data == {"status": "playing", "artist": "Some Artist", "title": "A Song", "album": "An Album", "art_url": "",
                      "position": 65.0, "length": 215.0, "player": "spotify"}
    assert calls[0][1] == "metadata" and "-p" not in calls[0]
    player_calls = player(monkeypatch, line())
    media(player="mpv").refresh()
    assert player_calls[0][:3] == ["/usr/bin/playerctl", "-p", "mpv"]


def test_mpris_missing_playerctl_and_no_player(monkeypatch):
    player(monkeypatch, installed=False)
    i = media()
    i.refresh()
    assert i.data is None and "playerctl not found" in i.error
    player(monkeypatch, out="", rc=1, err="No players found\n")
    i.refresh()
    assert i.error is None and i.data["status"] == "stopped"
    player(monkeypatch, out="", rc=1, err="Connection to player failed\nmore\n")
    i.refresh()
    assert i.error == "Connection to player failed" and i.data["status"] == "stopped"      # keeps the previous data


def test_mpris_edge_values(monkeypatch):
    player(monkeypatch, "Paused" + SEP + SEP + "Only Title\n")                  # short line, empty position/length
    i = media()
    i.refresh()
    assert i.data["status"] == "paused" and i.data["title"] == "Only Title" and i.data["position"] == 0 and i.data["length"] == 0


def test_mpris_art_file_and_http_cached_once(monkeypatch, srv, tmp_path):
    png = tmp_path / "cover art.png"
    Image.new("RGB", (600, 600), (200, 30, 30)).save(png)
    player(monkeypatch, line(art="file://" + str(png).replace(" ", "%20")))
    i = media()
    i.refresh()
    assert i.art_image is not None and max(i.art_image.size) <= 256
    first = i.art_image
    i.refresh()
    assert i.art_image is first                                                  # same track: not reloaded
    import io
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 30, 200)).save(buf, "PNG")
    srv.routes[("GET", "/cover.png")] = (200, buf.getvalue(), {"Content-Type": "image/png"})
    player(monkeypatch, line(art=srv.url + "/cover.png"))
    i.refresh()
    i.refresh()
    assert i.art_image is not first and len([r for r in srv.requests if r.path == "/cover.png"]) == 1
    player(monkeypatch, line(art="file:///nonexistent/cover.png"))
    i.refresh()
    assert i.art_image is None and i.connected                                  # a broken cover never breaks the poll
    player(monkeypatch, line(art="data:image/png;base64,xx"))
    i.refresh()
    assert i.art_image is None


def test_mpris_controls_run_playerctl(monkeypatch):
    calls = player(monkeypatch, line())
    i = media()
    i.refresh()
    i.play_pause(); i.next(); i.previous()
    assert [c[1] for c in calls[1::2]] == ["play-pause", "next", "previous"]    # each action is followed by a refresh


def test_media_widget_renders_and_tap_zones(monkeypatch, tmp_path):
    png = tmp_path / "c.png"
    Image.new("RGB", (300, 300), (40, 160, 90)).save(png)
    calls = player(monkeypatch, line(art="file://" + str(png)))
    r = reg({"m": {"type": "mpris"}})
    i = r.items["m"]
    i.refresh()
    for at, grid in (((0, 0, 1, 1), (4, 3)), ((0, 0, 2, 2), (4, 3)), ((0, 0, 4, 1), (4, 3))):
        assert drawn(render("media_player", r, at=at, grid=grid)[0])
    img, w = render("media_player", r, at=(0, 0, 3, 1), grid=(3, 1))             # three equal columns: 0..338, 338..677, 677..1016
    for x, action in ((100, "previous"), (500, "play-pause"), (900, "next")):
        n = len(calls)
        assert w.on_tap(x, 100) is True
        assert calls[n][1] == action
    i.data["status"] = "paused"                                                  # play glyph + zero length
    i.data["length"] = 0
    assert drawn(render("media_player", r)[0])
    i.data = dict(mpris_mod.EMPTY)
    assert drawn(render("media_player", r)[0])


def test_media_widget_placeholders(monkeypatch):
    player(monkeypatch, installed=False)
    r = reg({"m": {"type": "mpris"}})
    r.items["m"].refresh()
    img, w = render("media_player", r)
    assert len(img.getcolors(100000)) > 3
    assert w.on_tap(10, 10) is True and "playerctl" in r.items["m"].error      # failing control does not raise
    assert len(render("media_player", reg({}))[0].getcolors(100000)) > 3
    assert render("media_player", reg({}))[1].on_tap(10, 10) is False


def test_all_new_widgets_declare_form_fields():
    for name in ("dns_stats", "unifi", "weather", "calendar_agenda", "media_player"):
        cls = widgets.REGISTRY[name]
        assert cls.__doc__ and cls.fields and all({"name", "type"} <= set(f) for f in cls.fields)
