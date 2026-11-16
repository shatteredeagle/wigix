#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-only
"""Render the README / docs screenshots from generic fake data. No real services, no network.

    .venv/bin/python tools/make_screenshots.py            # dashboards + editor
    .venv/bin/python tools/make_screenshots.py --no-editor

Starts the test fakes (tests/fake_ha.py, tests/fake_http.py) with made-up data ("pve.lan", "Living Room", ...),
renders full-screen dashboards (1016x592) with the real engine into docs/img/*.png, then (unless --no-editor)
starts the web editor against a demo config and screenshots it with Playwright. A widget whose module does not
exist yet is skipped and listed at the end, so the script keeps working while the widget set grows.

`Demo` is also used by tools/gallery.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import pathlib
import socket
import sys
import tempfile
import threading
import time
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from fake_ha import TOKEN, FakeHA  # noqa: E402
from fake_http import FakeHTTP, FakeUnixHTTP  # noqa: E402
from wigix import widgets  # noqa: E402
from wigix.engine import Dashboard  # noqa: E402

IMG = ROOT / "docs" / "img"
SKIPPED: list[str] = []


# ------------------------------------------------------------------------------------------------ fake data
def S(eid, state, **attrs):
    ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=12)
    return {"entity_id": eid, "state": str(state), "attributes": attrs, "last_changed": ago.isoformat()}


def wave(n, base, amp, period, phase=0.0, noise=0.0, jit=0.15):
    out = []
    for i in range(n):
        v = base + amp * math.sin(i / period * 2 * math.pi + phase) + amp * jit * math.sin(i * 1.7 + phase)
        v += noise * math.sin(i * 12.9898 + phase) * math.cos(i * 4.1414)
        out.append(v)
    return out


HA_STATES = [
    S("sensor.living_room_temperature", 21.6, unit_of_measurement="°C", friendly_name="Living Room", device_class="temperature"),
    S("sensor.living_room_humidity", 46, unit_of_measurement="%", friendly_name="Humidity", device_class="humidity"),
    S("sensor.outdoor_temperature", 7.2, unit_of_measurement="°C", friendly_name="Outside", device_class="temperature"),
    S("sensor.bedroom_temperature", 19.4, unit_of_measurement="°C", friendly_name="Bedroom", device_class="temperature"),
    S("sensor.office_co2", 640, unit_of_measurement="ppm", friendly_name="Office CO2"),
    S("sensor.power_consumption", 412, unit_of_measurement="W", friendly_name="Power", device_class="power"),
    S("sensor.solar_production", 1.8, unit_of_measurement="kW", friendly_name="Solar", device_class="power"),
    S("sensor.freezer_temperature", -18.4, unit_of_measurement="°C", friendly_name="Freezer", device_class="temperature"),
    S("light.living_room", "on", friendly_name="Living Room Light", brightness=190),
    S("light.desk_lamp", "on", friendly_name="Desk Lamp", brightness=255),
    S("light.hallway", "off", friendly_name="Hallway"),
    S("switch.coffee_maker", "off", friendly_name="Coffee Maker"),
    S("switch.garden_pump", "on", friendly_name="Garden Pump"),
    S("binary_sensor.front_door", "on", device_class="door", friendly_name="Front Door"),
    S("binary_sensor.garage_door", "off", device_class="garage_door", friendly_name="Garage Door"),
    S("binary_sensor.basement_leak", "on", device_class="moisture", friendly_name="Basement Leak"),
    S("binary_sensor.hallway_smoke", "off", device_class="smoke", friendly_name="Hallway Smoke"),
    S("binary_sensor.sump_pump", "on", device_class="problem", friendly_name="Sump Pump"),
    S("binary_sensor.garden_gate", "on", device_class="safety", friendly_name="Garden Gate Open"),
    S("lock.back_door", "locked", friendly_name="Back Door Lock"),
]

HA_HISTORY = {
    "sensor.living_room_temperature": [round(v, 2) for v in wave(120, 21.0, 1.2, 60, jit=0.03)],
    "sensor.outdoor_temperature": [round(v, 2) for v in wave(120, 6.0, 4.0, 120, 3.0, jit=0.03)],
    "sensor.power_consumption": [round(max(80, v)) for v in wave(120, 380, 160, 40, 1.0, 40)],
    "sensor.office_co2": [round(v) for v in wave(120, 700, 180, 50, 2.0, 25)],
}

DOCKER = [
    {"Id": c * 64, "Names": ["/" + n], "Image": img, "State": st, "Status": s}
    for c, (n, img, st, s) in zip("abcdefghijklmnop", [
        ("traefik", "traefik:v3", "running", "Up 12 days"),
        ("plex", "plexinc/pms-docker", "running", "Up 12 days (healthy)"),
        ("sonarr", "linuxserver/sonarr", "running", "Up 4 days"),
        ("radarr", "linuxserver/radarr", "running", "Up 4 days"),
        ("nextcloud", "nextcloud:29", "running", "Up 9 days (healthy)"),
        ("postgres", "postgres:16", "running", "Up 12 days (healthy)"),
        ("grafana", "grafana/grafana", "running", "Up 6 days"),
        ("vaultwarden", "vaultwarden/server", "running", "Up 12 days"),
        ("paperless", "paperless-ngx", "restarting", "Restarting (1) 12 seconds ago"),
        ("backup-job", "restic/restic", "exited", "Exited (1) 3 hours ago"),
        ("gitea", "gitea/gitea", "running", "Up 12 days"),
        ("pihole", "pihole/pihole", "running", "Up 12 days (unhealthy)"),
    ])
]


def prom_series(query, hours=6, step=300):
    n = int(hours * 3600 / step)
    table = {
        "cpu_hist": (24, 16, 0.5, 0.0, 6), "mem_hist": (61, 6, 0.8, 1.0, 1.5), "net_hist": (48, 20, 0.35, 2.0, 5),
        "temp_hist": (52, 6, 0.4, 0.5, 1.0), "load_hist": (1.4, 0.9, 0.6, 1.5, 0.2), "req_hist": (180, 90, 0.5, 2.5, 20),
        "disk_hist": (4.5, 3.0, 0.4, 0.7, 1.0),
    }
    base, amp, per, ph, noise = table.get(query, (50, 20, 0.5, 0.0, 3))
    return wave(n, base, amp, n * per, ph, noise)


PROM_INSTANT = {"cpu": 23.8, "mem": 61.4, "load": 1.42, "up": 27, "temp": 52.3, "req": 184, "disk": 71.0}


def make_cover(path):
    img = Image.new("RGB", (300, 300))
    px = img.load()
    for y in range(300):
        for x in range(300):
            t = (x + y) / 600
            px[x, y] = (int(40 + 190 * t), int(30 + 60 * (1 - t)), int(150 + 90 * (1 - t)))
    d = ImageDraw.Draw(img)
    d.ellipse((75, 75, 225, 225), outline=(255, 255, 255), width=6)
    d.ellipse((125, 125, 175, 175), fill=(255, 255, 255))
    img.save(path)


def ics(now: dt.datetime) -> str:
    def f(d, h, m):
        return (d + dt.timedelta(hours=h, minutes=m)).strftime("%Y%m%dT%H%M%S")

    def day(d):
        return d.strftime("%Y%m%d")
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    soon = (now + dt.timedelta(minutes=95)).replace(minute=0, second=0) + dt.timedelta(minutes=30 if now.minute >= 30 else 0)
    ev = [
        ("Team standup", f"DTSTART:{soon.strftime('%Y%m%dT%H%M00')}\nDTEND:{(soon + dt.timedelta(minutes=30)).strftime('%Y%m%dT%H%M00')}\nLOCATION:Room 4"),
        ("Dinner with Sam", f"DTSTART:{(soon + dt.timedelta(hours=3)).strftime('%Y%m%dT%H%M00')}\nDTEND:{(soon + dt.timedelta(hours=5)).strftime('%Y%m%dT%H%M00')}"),
        ("Dentist", f"DTSTART:{f(today + dt.timedelta(days=1), 9, 15)}\nDTEND:{f(today + dt.timedelta(days=1), 10, 0)}"),
        ("Garbage collection", f"DTSTART;VALUE=DATE:{day(today + dt.timedelta(days=2))}\nDTEND;VALUE=DATE:{day(today + dt.timedelta(days=3))}"),
        ("Book club", f"DTSTART:{f(today + dt.timedelta(days=3), 18, 0)}\nDTEND:{f(today + dt.timedelta(days=3), 20, 0)}"),
        ("Parents visit", f"DTSTART;VALUE=DATE:{day(today + dt.timedelta(days=5))}\nDTEND;VALUE=DATE:{day(today + dt.timedelta(days=7))}"),
    ]
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//wigix demo//EN"]
    for i, (title, body) in enumerate(ev):
        out += ["BEGIN:VEVENT", f"UID:demo{i}@example.com", "DTSTAMP:20260101T000000Z", f"SUMMARY:{title}", *body.split("\n"), "END:VEVENT"]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def forecast(today: dt.date):
    return {"current": {"temperature_2m": 8.4, "apparent_temperature": 6.1, "relative_humidity_2m": 78, "weather_code": 3,
                        "wind_speed_10m": 14.0, "is_day": 1},
            "daily": {"time": [(today + dt.timedelta(days=i)).isoformat() for i in range(3)], "weather_code": [3, 61, 1],
                      "temperature_2m_max": [10.2, 8.5, 12.1], "temperature_2m_min": [4.1, 3.6, 5.0]}}


class Demo:
    """All fakes running at once. ``demo.integrations`` is a ready-made ``integrations:`` block pointing at them."""

    def __init__(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="wigix-demo-"))
        self.ha = FakeHA(HA_STATES)
        self.ha.history.update(HA_HISTORY)
        self.ha.url = self.ha.start()
        self.http = FakeHTTP()
        self.sock = FakeUnixHTTP(self.tmp / "docker.sock", {("GET", "/containers/json"): DOCKER})
        self._routes()
        (self.tmp / "cover.png").parent.mkdir(exist_ok=True)
        make_cover(self.tmp / "cover.png")
        (self.tmp / "calendar.ics").write_text(ics(dt.datetime.now()))
        self._patch_system()
        self._patch_mpris()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(5)
        self.tcp_ok = f"127.0.0.1:{self.listener.getsockname()[1]}"
        self.integrations = self._integrations()

    # --- fakes --------------------------------------------------------------------------------------
    def _routes(self):
        u = self.http.url
        r = self.http.routes
        # JSON API (http integration)
        r[("GET", "/v1/stats")] = {"queue": {"active": 3, "waiting": 12}, "uptime": 86400 * 9, "version": "2.4.1"}
        r[("GET", "/v1/jobs")] = {"jobs": [{"name": "nightly-backup", "value": 98}, {"name": "photo-sync", "value": 64},
                                          {"name": "index-rebuild", "value": 31}, {"name": "thumbnails", "value": 7}]}
        r[("*", "/ok")] = {"ok": True}
        # node_exporter (the `node` widget): counters that move on every scrape
        scrapes = {"n": 0}

        def metrics(req):
            scrapes["n"] += 1
            n = scrapes["n"]
            return (200, f"""node_cpu_seconds_total{{cpu="0",mode="idle"}} {n * 3.0}
node_cpu_seconds_total{{cpu="0",mode="user"}} {n * 0.7}
node_cpu_seconds_total{{cpu="0",mode="system"}} {n * 0.3}
node_memory_MemTotal_bytes 17179869184
node_memory_MemAvailable_bytes 7000000000
node_load1 0.9
node_filesystem_size_bytes{{mountpoint="/"}} 500000000000
node_filesystem_avail_bytes{{mountpoint="/"}} 180000000000
node_network_receive_bytes_total{{device="eth0"}} {n * 2_000_000 + int(900_000 * (n % 3))}
node_network_transmit_bytes_total{{device="eth0"}} {n * 400_000}
""", {"Content-Type": "text/plain"})
        r[("GET", "/metrics")] = metrics
        # Prometheus
        def query(req):
            q = req.query["query"][0]
            return {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, str(PROM_INSTANT.get(q, 1))]}]}}

        def rng(req):
            q = req.query["query"][0]
            vals = [[1000 + i * 300, str(v)] for i, v in enumerate(prom_series(q))]
            return {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {}, "values": vals}]}}
        r[("GET", "/api/v1/query")] = query
        r[("GET", "/api/v1/query_range")] = rng
        # Alertmanager
        r[("GET", "/api/v2/alerts")] = [
            {"labels": {"alertname": "BackupJobFailed", "severity": "critical", "instance": "nas.lan"}, "annotations": {"summary": "Nightly backup failed"}},
            {"labels": {"alertname": "DiskAlmostFull", "severity": "warning", "instance": "nas.lan:9100"}, "annotations": {"summary": "disk01 at 92%"}},
            {"labels": {"alertname": "HighTemperature", "severity": "warning", "instance": "pve.lan"}, "annotations": {"summary": "CPU 78 C"}}]
        # Uptime Kuma
        r[("GET", "/api/status-page/main")] = {"config": {"slug": "main"}, "publicGroupList": [
            {"name": "Core", "monitorList": [{"id": 1, "name": "Router"}, {"id": 2, "name": "DNS"}, {"id": 3, "name": "NAS"}, {"id": 4, "name": "Proxmox"}]},
            {"name": "Apps", "monitorList": [{"id": 5, "name": "Nextcloud"}, {"id": 6, "name": "Photos"}, {"id": 7, "name": "Wiki"}, {"id": 8, "name": "Git"}, {"id": 9, "name": "Backup"}]}]}
        r[("GET", "/api/status-page/heartbeat/main")] = {"heartbeatList": {
            "1": [{"status": 1, "ping": 1}], "2": [{"status": 1, "ping": 4}], "3": [{"status": 1, "ping": 2}], "4": [{"status": 1, "ping": 3}],
            "5": [{"status": 1, "ping": 61}], "6": [{"status": 1, "ping": 112}], "7": [{"status": 2, "ping": 540}],
            "8": [{"status": 1, "ping": 38}], "9": [{"status": 0, "ping": None}]}, "uptimeList": {}}
        # Proxmox
        gib = 2**30
        r[("GET", "/api2/json/nodes")] = {"data": [{"node": "pve"}]}
        r[("GET", "/api2/json/nodes/pve/status")] = {"data": {"cpu": 0.18, "uptime": 86400 * 41, "memory": {"used": 21 * gib, "total": 32 * gib},
                                                              "rootfs": {"used": 38 * gib, "total": 100 * gib}}}
        r[("GET", "/api2/json/cluster/resources")] = {"data": [
            {"vmid": 100, "name": "homeassistant", "status": "running", "node": "pve"},
            {"vmid": 101, "name": "docker-host", "status": "running", "node": "pve"},
            {"vmid": 102, "name": "pihole", "status": "running", "node": "pve"},
            {"vmid": 103, "name": "windows-test", "status": "stopped", "node": "pve"},
            {"vmid": 104, "name": "media", "status": "running", "node": "pve"},
            {"vmid": 105, "name": "backup-pbs", "status": "running", "node": "pve"}]}
        # Unraid
        r[("POST", "/graphql")] = {"data": {
            "metrics": {"cpu": {"percentTotal": 14}, "memory": {"percentTotal": 58, "used": 1, "total": 2}},
            "array": {"state": "STARTED", "capacity": {"kilobytes": {"free": "23100000000", "used": "41700000000", "total": "64800000000"}}},
            "docker": {"containers": [{"state": "RUNNING"}] * 17 + [{"state": "EXITED"}] * 2}}}
        # AdGuard
        r[("GET", "/control/stats")] = {"num_dns_queries": 48210, "num_blocked_filtering": 9830, "num_replaced_safebrowsing": 12,
                                        "num_replaced_parental": 0, "avg_processing_time": 0.004}
        r[("GET", "/control/status")] = {"protection_enabled": True, "running": True, "version": "v0.107"}
        # UniFi
        r[("POST", "/api/auth/login")] = (200, {"unique_id": "x"}, {"Set-Cookie": "TOKEN=demo; Path=/", "X-CSRF-Token": "csrf"})
        r[("GET", "/proxy/network/api/s/default/stat/health")] = {"data": [
            {"subsystem": "wan", "status": "ok", "wan_ip": "203.0.113.7", "rx_bytes-r": 2_650_000, "tx_bytes-r": 310_000, "num_adopted": 1, "num_disconnected": 0},
            {"subsystem": "lan", "status": "ok", "num_adopted": 4, "num_disconnected": 0},
            {"subsystem": "wlan", "status": "ok", "num_adopted": 3, "num_disconnected": 0}]}
        r[("GET", "/proxy/network/api/s/default/stat/sta")] = {"data": [{"mac": f"aa:{i:02x}", "is_wired": i % 3 == 0} for i in range(31)]}
        # Open-Meteo
        r[("GET", "/v1/forecast")] = forecast(dt.date.today())

    def _patch_system(self):
        """The `system` widget reads THIS machine; give it a calm made-up one instead."""
        from wigix.widgets import system as sysmod
        t0 = time.time()
        net = {"n": 0}

        def cpu_percent():
            return 22 + 14 * math.sin((time.time() - t0) / 5) + 4 * math.sin(time.time() * 3)

        def net_io_counters():
            net["n"] += int(1_400_000 + 900_000 * math.sin(time.time() / 4))
            return types.SimpleNamespace(bytes_recv=net["n"], bytes_sent=net["n"] // 5)
        sysmod.psutil = types.SimpleNamespace(
            cpu_percent=cpu_percent, net_io_counters=net_io_counters,
            virtual_memory=lambda: types.SimpleNamespace(percent=47.5),
            disk_usage=lambda p: types.SimpleNamespace(percent=63.0))
        self._sysmod = sysmod

    def _patch_mpris(self):
        from wigix.integrations import mpris as mp
        sep = mp.SEP
        line = sep.join(["Playing", "The Example Band", "Neon Skyline", "Night Drive", "file://" + str(self.tmp / "cover.png"),
                         str(104_000_000), str(231_000_000), "spotify"]) + "\n"
        mp.shutil = types.SimpleNamespace(which=lambda n: "/usr/bin/playerctl")
        mp.subprocess = types.SimpleNamespace(
            run=lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout=line if cmd[1] == "metadata" else "", stderr=""),
            CompletedProcess=object, TimeoutExpired=Exception, CalledProcessError=Exception, SubprocessError=Exception)

    def _integrations(self):
        h = self.http.url
        return {
            "ha": {"type": "home_assistant", "url": self.ha.url, "token": TOKEN},
            "pve": {"type": "proxmox", "url": h, "token_id": "monitor@pve!wigix", "token": "x", "node": "pve"},
            "nas": {"type": "unraid", "url": h + "/graphql", "api_key": "x"},
            "docker": {"type": "docker", "socket": self.sock.path},
            "prom": {"type": "prometheus", "url": h,
                     "queries": {k: k for k in PROM_INSTANT},
                     "ranges": {k: {"query": k, "hours": 6, "step": 300} for k in
                                ("cpu_hist", "mem_hist", "net_hist", "temp_hist", "load_hist", "req_hist", "disk_hist")}},
            "alerts": {"type": "alertmanager", "url": h},
            "kuma": {"type": "uptime_kuma", "url": h, "slug": "main"},
            "dns": {"type": "adguard", "url": h, "username": "admin", "password": "x"},
            "unifi": {"type": "unifi", "url": h, "username": "admin", "password": "x"},
            "weather": {"type": "open_meteo", "latitude": 48.85, "longitude": 2.35, "base_url": h},
            "cal": {"type": "ical", "file": str(self.tmp / "calendar.ics")},
            "media": {"type": "mpris"},
            "api": {"type": "http", "base_url": h, "paths": ["/v1/stats", "/v1/jobs"]},
        }

    def checks(self):
        return [{"name": "pve.lan", "http": self.http.url + "/ok"}, {"name": "nas.lan", "tcp": self.tcp_ok},
                {"name": "router.lan", "http": self.http.url + "/ok"}, {"name": "backup.lan", "tcp": "127.0.0.1:9"}]

    def config(self, pages, **extra):
        return {"fps": 2, "integrations": {k: dict(v) for k, v in self.integrations.items()}, "pages": pages, **extra}

    def stop(self):
        for s in (self.ha, self.http, self.sock):
            try:
                s.stop()
            except Exception:
                pass


# ------------------------------------------------------------------------------------------------ rendering
def settle(dash, timeout=10.0):
    """Start the workers and wait until every integration has data (or gave up) and widgets updated once."""
    dash.start_workers()
    t = time.time()
    while time.time() - t < timeout:
        if all(getattr(i, "data", None) is not None or getattr(i, "error", None) or i.status().get("connected")
               for i in dash.integrations.items.values()):
            break
        time.sleep(0.1)
    slow = any(getattr(w, "type_name", "") == "node" for pg in dash.pages for w, _ in pg.items)    # node needs two scrapes
    time.sleep(4.5 if slow else 2.0)
    for page in dash.pages:
        for w, _ in page.items:
            for attr in ("cpu_hist", "net_hist"):                 # system/node widgets: a full history right away
                h = getattr(w, attr, None)
                if h is not None and len(h) < 5:
                    h.clear()
                    h.extend(wave(60, 30 if attr == "cpu_hist" else 1_200_000, 14 if attr == "cpu_hist" else 700_000, 30, 0.3,
                                  4 if attr == "cpu_hist" else 150_000))


def render_dashboard(cfg, page=0):
    dash = Dashboard(cfg)
    try:
        settle(dash)
        dash.page_idx = page
        return dash.current().render()
    finally:
        dash._stop.set()
        for p in dash.pages:
            for w, _ in p.items:
                w._halt = True
        dash.integrations.stop()


def try_page(name, widgets_cfg):
    """Drop widgets whose type is not registered (yet) and report them."""
    ok = []
    for w in widgets_cfg:
        if w["type"] in widgets.REGISTRY:
            ok.append(w)
        else:
            SKIPPED.append(f"{name}: widget {w['type']!r} not available")
    return ok


# ------------------------------------------------------------------------------------------------ dashboards
def homelab(d: Demo):
    pages = [{"name": "Homelab", "grid": {"cols": 4, "rows": 3, "gap": 10}, "widgets": try_page("homelab", [
        {"type": "clock", "at": [0, 0, 1, 1], "format": "%H:%M", "date_format": "%a %d %b"},
        {"type": "system", "title": "workstation", "at": [0, 1, 1, 2]},
        {"type": "proxmox", "name": "pve", "at": [1, 0, 1, 3]},
        {"type": "unraid", "name": "nas", "at": [2, 0, 1, 2]},
        {"type": "checks", "title": "Services", "at": [3, 0, 1, 2], "checks": d.checks()},
        {"type": "ha_entity", "entity": "sensor.living_room_temperature", "name": "Living Room", "precision": 1,
         "color_rules": [{"above": 25, "color": "#f05454"}], "at": [2, 2, 1, 1]},
        {"type": "ha_gauge", "entity": "sensor.power_consumption", "name": "Power", "min": 0, "max": 1000, "at": [3, 2, 1, 1]},
    ])}]
    return d.config(pages)


def home(d: Demo):
    pages = [{"name": "Home", "grid": {"cols": 4, "rows": 3, "gap": 10}, "widgets": try_page("home", [
        {"type": "weather", "name": "Paris", "at": [0, 0, 1, 2]},
        {"type": "calendar_agenda", "name": "Agenda", "max_events": 5, "at": [1, 0, 2, 2]},
        {"type": "ha_alerts", "title": "Alerts", "scan": True, "at": [3, 0, 1, 1]},
        {"type": "ha_entity", "entity": "sensor.power_consumption", "name": "Power", "at": [3, 1, 1, 1]},
        {"type": "ha_list", "title": "Climate", "entities": ["sensor.living_room_temperature", "sensor.bedroom_temperature",
                                                             "sensor.outdoor_temperature", "sensor.living_room_humidity"], "at": [0, 2, 1, 1]},
        {"type": "ha_toggle", "entity": "light.living_room", "at": [1, 2, 1, 1]},
        {"type": "ha_toggle", "entity": "light.desk_lamp", "at": [2, 2, 1, 1]},
        {"type": "media_player", "name": "Now playing", "at": [3, 2, 1, 1]},
    ])}]
    return d.config(pages)


def monitoring(d: Demo):
    cr = [{"above": 80, "color": "#f05454"}, {"above": 60, "color": "#f5be3c"}]
    pages = [{"name": "Monitoring", "grid": {"cols": 4, "rows": 3, "gap": 10}, "widgets": try_page("monitoring", [
        {"type": "prom_graph", "range": "cpu_hist", "name": "CPU (6 h)", "unit": "%", "precision": 0, "color_rules": cr, "at": [0, 0, 2, 1]},
        {"type": "prom_graph", "range": "net_hist", "name": "Network (6 h)", "unit": "Mb/s", "precision": 0, "at": [0, 1, 2, 1]},
        {"type": "prom_value", "query": "mem", "name": "Memory", "unit": "%", "precision": 0, "color_rules": cr, "at": [0, 2, 1, 1]},
        {"type": "prom_value", "query": "load", "name": "Load (1 m)", "precision": 2, "at": [1, 2, 1, 1]},
        {"type": "docker_containers", "title": "Containers", "at": [2, 0, 1, 2]},
        {"type": "dns_stats", "name": "AdGuard", "at": [2, 2, 1, 1]},
        {"type": "uptime_kuma", "title": "Uptime", "at": [3, 0, 1, 2]},
        {"type": "alertmanager_alerts", "title": "Alerts", "at": [3, 2, 1, 1]},
    ])}]
    return d.config(pages)


THEME_SYNTH = {"bg": "#120b24", "card": "#1f1440", "text": "#f4e9ff", "dim": "#8f7fb8", "ok": "#3ef0b0", "warn": "#ffb454",
               "crit": "#ff4f9a", "accent": "#b36bff"}


def themed(d: Demo):
    pages = [{"name": "Synthwave", "grid": {"cols": 4, "rows": 3, "gap": 10}, "widgets": try_page("themed", [
        {"type": "clock", "format": "%H:%M", "date_format": "%A", "at": [0, 0, 1, 1]},
        {"type": "weather", "name": "Paris", "at": [1, 0, 1, 2]},
        {"type": "prom_graph", "range": "req_hist", "name": "Requests / s", "precision": 0, "at": [2, 0, 2, 1]},
        {"type": "ha_gauge", "entity": "sensor.living_room_temperature", "name": "Living Room", "min": 10, "max": 30, "unit": "°C", "precision": 1, "at": [0, 1, 1, 1]},
        {"type": "ha_graph", "entity": "sensor.outdoor_temperature", "name": "Outside, 24 h", "hours": 24, "precision": 1, "at": [2, 1, 2, 1]},
        {"type": "ha_toggle", "entity": "light.desk_lamp", "at": [0, 2, 1, 1]},
        {"type": "media_player", "name": "Now playing", "at": [1, 2, 2, 1]},
        {"type": "macro_pad", "columns": 2, "buttons": [
            {"label": "Movie", "color": "#5b2a9e", "on_tap": {"page": "Synthwave"}},
            {"label": "Focus", "color": "#9e2a6b", "on_tap": "fullscreen"}], "at": [3, 2, 1, 1]},
    ])}]
    return d.config(pages, theme=THEME_SYNTH)


DASHBOARDS = [("homelab", homelab), ("home", home), ("monitoring", monitoring), ("themed", themed)]


def editor_config(d: Demo):
    """Two pages for the editor screenshots: the Homelab page first (it is what the preview shows)."""
    cfg = homelab(d)
    cfg["pages"] += home(d)["pages"] + monitoring(d)["pages"]
    cfg["editor"] = {"port": 8765}
    return cfg


# ------------------------------------------------------------------------------------------------ editor
def editor_shots(d: Demo):
    from playwright.sync_api import sync_playwright
    from wigix.editor import load_token, serve
    from wigix.render import set_theme
    set_theme(None)                 # the dashboards above may have set a custom theme globally

    cfgfile = d.tmp / "dashboard.yaml"
    cfgfile.write_text(yaml.safe_dump(editor_config(d), sort_keys=False, default_flow_style=None, width=100))
    srv = serve(str(cfgfile), "127.0.0.1", 0, block=False)
    port = srv.server_address[1]
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path="/usr/bin/google-chrome-stable", args=["--no-sandbox"])
            for scheme in ("dark", "light"):
                ctx = b.new_context(viewport={"width": 1500, "height": 900}, color_scheme=scheme, bypass_csp=True)   # bypass: wait_for_function evals a string
                page = ctx.new_page()
                page.goto(f"http://127.0.0.1:{port}/?token={load_token(cfgfile)}")
                page.wait_for_timeout(1500)
                try:
                    page.wait_for_function("""() => { const im = document.querySelector('img'); const c = document.querySelector('canvas');
                                              return (im && im.complete && im.naturalWidth > 100) || (c && c.width > 100); }""", timeout=20000)
                except Exception:
                    SKIPPED.append(f"editor-{scheme}: could not detect the preview image, took the shot anyway")
                page.wait_for_timeout(5000)             # let the live widgets fill in and the preview refresh
                page.screenshot(path=str(IMG / f"editor-{scheme}.png"))
                print("wrote", IMG / f"editor-{scheme}.png")
                ctx.close()
            b.close()
    finally:
        srv.shutdown()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-editor", action="store_true")
    ap.add_argument("--only", help="comma separated dashboard names")
    a = ap.parse_args()
    widgets.load_plugins()
    IMG.mkdir(parents=True, exist_ok=True)
    d = Demo()
    try:
        for name, fn in DASHBOARDS:
            if a.only and name not in a.only.split(","):
                continue
            img = render_dashboard(fn(d))
            img.save(IMG / f"{name}.png")
            print("wrote", IMG / f"{name}.png")
        if not a.no_editor:
            editor_shots(d)
    finally:
        d.stop()
    if SKIPPED:
        print("\nSkipped:")
        for s in SKIPPED:
            print("  -", s)
    sys.stdout.flush()
    os._exit(0)          # daemon threads, fake servers


if __name__ == "__main__":
    main()
