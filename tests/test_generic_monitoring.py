# SPDX-License-Identifier: GPL-3.0-only
"""http / prometheus / alertmanager / docker / uptime_kuma integrations and their widgets, against fake servers."""
import pytest

from fake_http import FakeHTTP, FakeUnixHTTP
from wigix import widgets
from wigix.engine import Page
from wigix.integrations import Registry
from wigix.integrations.http import json_path

widgets.load_plugins()


def reg(**integs):
    return Registry({"integrations": integs})


def page(r, wtype, cols=1, rows=1, **cfg):
    return Page({"grid": {"cols": cols, "rows": rows}, "widgets": [{"type": wtype, "at": [0, 0, 1, 1], **cfg}]},
                {"integrations": r})


def drawn(r, wtype, **cfg):
    """Render one widget on a 4x3 grid cell and return the distinct color count (blank card ~3)."""
    return len(page(r, wtype, cols=4, rows=3, **cfg).render().getcolors(100000))


@pytest.fixture
def srv():
    s = FakeHTTP()
    yield s
    s.stop()


# ---------------------------------------------------------------- json_path

DOC = {"data": {"items": [{"name": "a", "n": 1}, {"name": "b", "n": "2.5"}], "a.b": 7}, "ok": True, "nothing": None}


@pytest.mark.parametrize("expr,want", [
    ("data.items[0].name", "a"),
    ("data.items[-1].n", "2.5"),
    ("$.data.items[1].name", "b"),
    ("data.items.0.name", "a"),                 # bare number indexes a list
    ('data["a.b"]', 7),
    ("data['a.b']", 7),
    ("ok", True),
    ("nothing", None),
    ("", DOC),
    ("data.items[5].name", None),               # out of range
    ("data.missing.deeper", None),
    ("data.items.name", None),                  # key on a list
    ("ok.x", None),                             # key on a scalar
    ("data.items[", None),                      # malformed
])
def test_json_path(expr, want):
    assert json_path(DOC, expr) == want


def test_json_path_default_and_list_root():
    assert json_path(DOC, "nope", default="x") == "x"
    assert json_path([{"v": 1}], "[0].v") == 1
    assert json_path(None, "a.b") is None


# ---------------------------------------------------------------- http integration

def test_http_polls_paths_with_auth_headers(srv):
    srv.routes[("GET", "/v1/stats")] = {"cpu": "12.5", "items": [{"name": "x", "v": 3}]}
    srv.routes[("GET", "/v1/other")] = {"k": 1}
    r = reg(api={"type": "http", "base_url": srv.url + "/", "token": "tok", "headers": {"X-Test": "1"},
                 "paths": ["/v1/stats", "v1/other"]})
    i = r.get("api", "http")
    i.refresh()
    assert i.connected and i.get("/v1/stats")["cpu"] == "12.5" and i.get("v1/other") == {"k": 1}
    assert i.get() == i.get("/v1/stats")                                  # default = first configured path
    h = srv.requests[0].headers
    assert h["authorization"] == "Bearer tok" and h["x-test"] == "1"


def test_http_single_path_and_token_file(srv, tmp_path):
    srv.routes[("GET", "/s")] = {"a": 1}
    tf = tmp_path / "tok"
    tf.write_text("filetoken\n")
    r = reg(api={"type": "http", "base_url": srv.url, "path": "/s", "token_file": str(tf)})
    i = r.get("api", "http")
    i.refresh()
    assert i.data == {"/s": {"a": 1}} and srv.requests[0].headers["authorization"] == "Bearer filetoken"


def test_http_no_token_means_no_auth_header(srv):
    srv.routes[("GET", "/s")] = {}
    i = reg(api={"type": "http", "base_url": srv.url, "path": "/s"}).get("api", "http")
    i.refresh()
    assert "authorization" not in srv.requests[0].headers


def test_http_error_keeps_previous_data(srv):
    srv.routes[("GET", "/s")] = {"a": 1}
    i = reg(api={"type": "http", "base_url": srv.url, "path": "/s"}).get("api", "http")
    i.refresh()
    srv.routes[("GET", "/s")] = (500, {"err": "boom"})
    i.refresh()
    assert i.error and "/s" in i.error and i.get("/s") == {"a": 1} and not i.connected
    srv.routes[("GET", "/s")] = (200, "not json")                         # a 200 that is not JSON is an error too
    i.refresh()
    assert i.error and i.get("/s") == {"a": 1}


def test_http_unknown_request_is_registered_for_next_poll(srv):
    srv.routes[("GET", "/s")] = {"a": 1}
    srv.routes[("GET", "/late")] = {"b": 2}
    i = reg(api={"type": "http", "base_url": srv.url, "path": "/s"}).get("api", "http")
    i.refresh()
    assert i.get("/late") is None
    i.refresh()
    assert i.get("/late") == {"b": 2}


def test_json_value_widget(srv):
    srv.routes[("GET", "/s")] = {"data": {"temp": "21.4999", "state": "ok", "list": [1, 2, 3]}}
    r = reg(api={"type": "http", "base_url": srv.url, "path": "/s"})
    r.get("api", "http").refresh()
    for cfg in ({"path": "data.temp", "unit": "°C", "precision": 1, "color_rules": [{"above": 20, "color": "#f05454"}]},
                {"path": "data.state", "state_colors": {"ok": "#4ad682"}},
                {"path": "data.list"},                                     # list -> its length
                {"path": "data.missing"},                                  # missing key -> dash
                {"request": "/s", "path": "data.temp"}):
        assert drawn(r, "json_value", name="Probe", **cfg) > 6


def test_json_value_color_rules_pick_color():
    from wigix.widgets.http import format_value, rule_color
    cfg = {"color_rules": [{"above": 80, "color": "#ff0000"}, {"below": 10, "color": "#0000ff"}]}
    assert rule_color(cfg, "90") == (255, 0, 0) and rule_color(cfg, 5) == (0, 0, 255)
    assert rule_color(cfg, 50) != (255, 0, 0) and rule_color(cfg, "text") == rule_color({}, 1)
    assert format_value("3.14159", "%", 2) == "3.14%" and format_value(None) == "—" and format_value(1200, "ms") == "1200 ms"
    assert format_value(True) == "True"                                    # bools are not numbers


def test_json_list_widget_variants(srv):
    srv.routes[("GET", "/q")] = {"jobs": [{"title": "alpha", "info": {"pct": "50"}}, {"title": "beta"}, {}],
                                 "map": {"x": 1, "y": {"value": 2}}, "empty": [], "n": 3}
    r = reg(api={"type": "http", "base_url": srv.url, "path": "/q"})
    r.get("api", "http").refresh()
    w = page(r, "json_list", path="jobs", name_path="title", value_path="info.pct", unit="%").items[0][0]
    assert w.rows() == [("alpha", "50"), ("beta", None), ("?", None)]
    assert page(r, "json_list", path="map").items[0][0].rows() == [("x", 1), ("y", 2)]
    assert page(r, "json_list", path="empty").items[0][0].rows() == [] and page(r, "json_list", path="n").items[0][0].rows() == []
    assert page(r, "json_list", path="jobs").items[0][0].rows()[0][0] == "?"      # default name_path "name" is absent
    for cfg in ({"path": "jobs", "name_path": "title", "value_path": "info.pct"}, {"path": "map"},
                {"path": "empty"}, {"path": "nope"}, {"path": "jobs", "max_rows": 1, "name_path": "title"}):
        assert drawn(r, "json_list", title="Jobs", **cfg) > 3


def test_json_list_scalar_items(srv):
    srv.routes[("GET", "/q")] = {"tags": ["a", "b"]}
    r = reg(api={"type": "http", "base_url": srv.url, "path": "/q"})
    r.get("api", "http").refresh()
    assert page(r, "json_list", path="tags").items[0][0].rows() == [("a", "a"), ("b", "b")]


# ---------------------------------------------------------------- prometheus

def prom_routes(srv):
    def query(req):
        q = req.query["query"][0]
        if q == "empty":
            return {"status": "success", "data": {"resultType": "vector", "result": []}}
        if q == "scalar":
            return {"status": "success", "data": {"resultType": "scalar", "result": [1, "4"]}}
        if q == "nan":
            return {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "NaN"]}]}}
        return {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "42.5"]}]}}

    def rng(req):
        vals = [[i, str(20 + i * 2)] for i in range(12)] + [[99, "NaN"]]
        return {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {}, "values": vals}]}}
    srv.routes[("GET", "/api/v1/query")] = query
    srv.routes[("GET", "/api/v1/query_range")] = rng


PROM = {"queries": {"cpu": "avg(x)", "none": "empty", "sc": "scalar", "nan": "nan"},
        "ranges": {"hist": {"query": "avg(x)", "hours": 6, "step": 300}}}


def test_prometheus_instant_and_range(srv):
    prom_routes(srv)
    i = reg(p={"type": "prometheus", "url": srv.url, **PROM}).get("p", "prometheus")
    i.refresh()
    assert i.connected
    assert i.data["cpu"] == 42.5 and i.data["none"] is None and i.data["sc"] == 4.0 and i.data["nan"] is None
    assert i.data["hist"][0] == 20.0 and len(i.data["hist"]) == 12            # NaN sample dropped
    rq = next(q for q in srv.requests if q.path.endswith("query_range"))
    start, end = float(rq.query["start"][0]), float(rq.query["end"][0])
    assert rq.query["step"] == ["300"] and abs((end - start) - 6 * 3600) < 5


def test_prometheus_default_step_and_auth(srv):
    prom_routes(srv)
    i = reg(p={"type": "prometheus", "url": srv.url, "token": "t", "ranges": {"h": {"query": "q", "hours": 1}}}).get("p", "prometheus")
    i.refresh()
    assert srv.requests[0].headers["authorization"] == "Bearer t" and srv.requests[0].query["step"] == ["60"]
    srv.requests.clear()
    i = reg(p={"type": "prometheus", "url": srv.url, "username": "u", "password": "pw", "queries": {"a": "q"}}).get("p", "prometheus")
    i.refresh()
    assert srv.requests[0].headers["authorization"].startswith("Basic ")      # base64("u:pw")
    import base64
    assert base64.b64decode(srv.requests[0].headers["authorization"][6:]) == b"u:pw"


def test_prometheus_error_keeps_previous_data(srv):
    prom_routes(srv)
    i = reg(p={"type": "prometheus", "url": srv.url, "queries": {"cpu": "avg(x)"}}).get("p", "prometheus")
    i.refresh()
    srv.routes[("GET", "/api/v1/query")] = (400, {"status": "error", "error": "bad query"})
    i.refresh()
    assert i.error and "cpu" in i.error and i.data["cpu"] == 42.5
    srv.routes[("GET", "/api/v1/query")] = {"status": "error", "error": "parse error"}      # 200 with status=error
    i.refresh()
    assert "parse error" in i.error


def test_prometheus_widgets_render(srv):
    prom_routes(srv)
    r = reg(p={"type": "prometheus", "url": srv.url, **PROM})
    r.get("p", "prometheus").refresh()
    assert drawn(r, "prom_value", query="cpu", name="CPU", unit="%", precision=0, color_rules=[{"above": 40, "color": "#f05454"}]) > 6
    assert drawn(r, "prom_value", query="none", name="Nothing") > 3          # no result -> dash
    assert drawn(r, "prom_value", query="unknown-name") > 3
    assert drawn(r, "prom_graph", range="hist", name="CPU 6h", unit="%") > 6
    assert drawn(r, "prom_graph", range="unknown-name") > 3                  # "no data"


def test_prom_graph_flat_series(srv):
    srv.routes[("GET", "/api/v1/query_range")] = {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {}, "values": [[i, "5"] for i in range(10)]}]}}
    r = reg(p={"type": "prometheus", "url": srv.url, "ranges": {"flat": {"query": "q"}}})
    r.get("p", "prometheus").refresh()
    assert drawn(r, "prom_graph", range="flat") > 6


# ---------------------------------------------------------------- alertmanager

ALERTS = [{"labels": {"alertname": "DiskFull", "severity": "warning", "instance": "nas:9100"}, "annotations": {"summary": "disk 95%"}},
          {"labels": {"alertname": "HostDown", "severity": "critical"}, "annotations": {}},
          {"labels": {"alertname": "Weird"}}]


def test_alertmanager_polls_and_sorts(srv):
    srv.routes[("GET", "/api/v2/alerts")] = ALERTS
    i = reg(am={"type": "alertmanager", "url": srv.url, "token": "t"}).get("am", "alertmanager")
    i.refresh()
    assert [a["name"] for a in i.data] == ["HostDown", "DiskFull", "Weird"]          # critical, warning, unknown
    assert i.data[1]["summary"] == "disk 95%" and i.data[2]["severity"] == ""
    q = srv.requests[0]
    assert q.query == {"active": ["true"], "silenced": ["false"], "inhibited": ["false"]}
    assert q.headers["authorization"] == "Bearer t"


def test_alertmanager_error_keeps_data_and_widget(srv):
    srv.routes[("GET", "/api/v2/alerts")] = ALERTS
    r = reg(am={"type": "alertmanager", "url": srv.url})
    i = r.get("am", "alertmanager")
    i.refresh()
    assert drawn(r, "alertmanager_alerts") > 6
    srv.routes[("GET", "/api/v2/alerts")] = (503, {})
    i.refresh()
    assert i.error and len(i.data) == 3
    srv.routes[("GET", "/api/v2/alerts")] = (200, [])        # (the fake treats a bare falsy body as "no route")
    i.refresh()
    assert i.data == [] and i.connected
    assert drawn(r, "alertmanager_alerts") > 3                                # "all clear"


def test_alertmanager_many_alerts_show_more(srv):
    srv.routes[("GET", "/api/v2/alerts")] = [{"labels": {"alertname": f"A{n}", "severity": "warning"}} for n in range(30)]
    r = reg(am={"type": "alertmanager", "url": srv.url})
    r.get("am", "alertmanager").refresh()
    assert drawn(r, "alertmanager_alerts", title="Alerts") > 6


# ---------------------------------------------------------------- docker

CONTAINERS = [
    {"Id": "a" * 64, "Names": ["/web"], "Image": "nginx:1", "State": "running", "Status": "Up 3 hours"},
    {"Id": "b" * 64, "Names": ["/db"], "Image": "postgres:16", "State": "running", "Status": "Up 3 hours (unhealthy)"},
    {"Id": "c" * 64, "Names": ["/job"], "Image": "alpine", "State": "exited", "Status": "Exited (1) 2 hours ago"},
    {"Id": "d" * 64, "Names": ["/media-sonarr"], "Image": "sonarr", "State": "paused", "Status": "Up 1 day (Paused)"},
    {"Id": "e" * 64, "Names": [], "Image": "busybox", "State": "restarting", "Status": "Restarting (1) 5 seconds ago"},
    {"Id": "f" * 64, "Names": ["/warm"], "Image": "app", "State": "running", "Status": "Up 5 seconds (health: starting)"},
]


@pytest.fixture
def dock(tmp_path):
    s = FakeUnixHTTP(tmp_path / "d.sock", {("GET", "/containers/json"): CONTAINERS})
    yield s
    s.stop()


def test_docker_over_unix_socket(dock):
    i = reg(d={"type": "docker", "socket": dock.path}).get("d", "docker")
    i.refresh()
    assert i.connected
    by = {c["name"]: c for c in i.data}
    assert by["web"] == {"name": "web", "state": "running", "status": "Up 3 hours", "image": "nginx:1"}
    assert "eeeeeeeeeeee" in by                                              # unnamed -> short id
    assert [c["name"] for c in i.data] == sorted(by, key=str.lower)
    assert dock.requests[0].query == {"all": ["1"]}


def test_docker_over_tcp(srv):
    srv.routes[("GET", "/containers/json")] = CONTAINERS[:1]
    i = reg(d={"type": "docker", "url": srv.url.replace("http://", "tcp://")}).get("d", "docker")
    i.refresh()
    assert i.connected and i.data[0]["name"] == "web"


def test_docker_errors_keep_data(dock, tmp_path):
    r = reg(d={"type": "docker", "socket": dock.path})
    i = r.get("d", "docker")
    i.refresh()
    dock.routes[("GET", "/containers/json")] = (500, {"message": "daemon error"})
    i.refresh()
    assert i.error and "500" in i.error and len(i.data) == 6
    gone = reg(d={"type": "docker", "socket": str(tmp_path / "missing.sock")}).get("d", "docker")
    gone.refresh()
    assert gone.data is None and gone.error                                  # no socket: offline, no exception


def test_docker_colors():
    from wigix.render import THEME
    from wigix.widgets.docker import container_color as cc
    assert cc({"state": "running", "status": "Up 1h"}) == THEME["ok"]
    assert cc({"state": "running", "status": "Up 1h (unhealthy)"}) == THEME["crit"]
    assert cc({"state": "running", "status": "Up 1s (health: starting)"}) == THEME["warn"]
    assert cc({"state": "exited", "status": ""}) == THEME["crit"] == cc({"state": "dead", "status": ""})
    assert cc({"state": "paused", "status": ""}) == THEME["warn"] == cc({"state": "restarting", "status": ""})
    assert cc({"state": "weird", "status": ""}) == THEME["dim"]


def test_docker_widget_options(dock):
    r = reg(d={"type": "docker", "socket": dock.path})
    r.get("d", "docker").refresh()
    for cfg in ({}, {"only_problems": True}, {"names": ["media-*", "web"]}, {"names": ["nothing-matches"]}, {"max_rows": 2}):
        assert drawn(r, "docker_containers", **cfg) > 6 or cfg.get("names") == ["nothing-matches"]
    dock.routes[("GET", "/containers/json")] = [CONTAINERS[0]]
    r.get("d", "docker").refresh()
    assert drawn(r, "docker_containers", only_problems=True) > 3             # "all healthy"
    dock.routes[("GET", "/containers/json")] = (200, [])
    r.get("d", "docker").refresh()
    assert drawn(r, "docker_containers") > 3                                 # "no containers"


# ---------------------------------------------------------------- uptime kuma

KUMA_PAGE = {"config": {"slug": "main"}, "publicGroupList": [
    {"name": "Core", "monitorList": [{"id": 1, "name": "Router"}, {"id": 2, "name": "NAS"}]},
    {"name": "Apps", "monitorList": [{"id": 3, "name": "Wiki"}, {"id": 4, "name": "New monitor"}, {"id": 5, "name": "Backup"}]}]}
KUMA_BEATS = {"heartbeatList": {
    "1": [{"status": 0, "ping": None}, {"status": 1, "ping": 3}],
    "2": [{"status": 1, "ping": 20}, {"status": 0, "ping": None}],
    "3": [{"status": 2, "ping": 150.4}],
    "5": [{"status": 3, "ping": None}]}, "uptimeList": {}}


@pytest.fixture
def kuma(srv):
    srv.routes[("GET", "/api/status-page/main")] = KUMA_PAGE
    srv.routes[("GET", "/api/status-page/heartbeat/main")] = KUMA_BEATS
    return srv


def test_kuma_polls_latest_heartbeat(kuma):
    i = reg(k={"type": "uptime_kuma", "url": kuma.url + "/", "slug": "main"}).get("k", "uptime_kuma")
    i.refresh()
    assert i.connected
    assert i.data == [{"name": "Router", "status": 1, "ping": 3}, {"name": "NAS", "status": 0, "ping": None},
                      {"name": "Wiki", "status": 2, "ping": 150.4}, {"name": "New monitor", "status": None, "ping": None},
                      {"name": "Backup", "status": 3, "ping": None}]
    assert "authorization" not in kuma.requests[0].headers                  # public API: no auth


def test_kuma_error_keeps_data_and_handles_missing_keys(kuma):
    i = reg(k={"type": "uptime_kuma", "url": kuma.url, "slug": "main"}).get("k", "uptime_kuma")
    i.refresh()
    kuma.routes[("GET", "/api/status-page/heartbeat/main")] = (500, {})
    i.refresh()
    assert i.error and len(i.data) == 5
    kuma.routes[("GET", "/api/status-page/heartbeat/main")] = (200, {})     # no heartbeatList at all
    kuma.routes[("GET", "/api/status-page/main")] = (200, {})               # and no groups
    i.refresh()
    assert i.data == [] and i.connected


def test_kuma_widget(kuma):
    r = reg(k={"type": "uptime_kuma", "url": kuma.url, "slug": "main"})
    i = r.get("k", "uptime_kuma")
    i.refresh()
    assert drawn(r, "uptime_kuma") > 6
    assert drawn(r, "uptime_kuma", only_problems=True) > 6
    kuma.routes[("GET", "/api/status-page/heartbeat/main")] = {"heartbeatList": {str(n): [{"status": 1, "ping": 5}] for n in range(1, 6)}}
    i.refresh()
    assert drawn(r, "uptime_kuma", only_problems=True) > 3                  # everything up -> "all up"
    kuma.routes[("GET", "/api/status-page/main")] = (200, {})
    i.refresh()
    assert drawn(r, "uptime_kuma") > 3                                      # no monitors


# ---------------------------------------------------------------- placeholders

@pytest.mark.parametrize("wtype,cfg,integ", [
    ("json_value", {"path": "a"}, {"type": "http", "base_url": "http://127.0.0.1:1", "path": "/x"}),
    ("json_list", {"path": "a"}, {"type": "http", "base_url": "http://127.0.0.1:1", "path": "/x"}),
    ("prom_value", {"query": "q"}, {"type": "prometheus", "url": "http://127.0.0.1:1", "queries": {"q": "x"}}),
    ("prom_graph", {"range": "q"}, {"type": "prometheus", "url": "http://127.0.0.1:1"}),
    ("alertmanager_alerts", {}, {"type": "alertmanager", "url": "http://127.0.0.1:1"}),
    ("docker_containers", {}, {"type": "docker", "socket": "/nonexistent/docker.sock"}),
    ("uptime_kuma", {}, {"type": "uptime_kuma", "url": "http://127.0.0.1:1", "slug": "x"}),
])
def test_widgets_placeholder_when_missing_or_offline(wtype, cfg, integ):
    for integs in ({}, {"x": integ}):
        r = reg(**integs)
        for i in r.items.values():
            i.refresh()                                                     # fails: nothing listening
        assert drawn(r, wtype, **cfg) > 3                                    # placeholder card, no exception


def test_every_widget_has_editor_fields():
    for t in ("json_value", "json_list", "prom_value", "prom_graph", "alertmanager_alerts", "docker_containers", "uptime_kuma"):
        fields = widgets.REGISTRY[t].fields
        assert fields and all({"name", "type"} <= set(f) for f in fields) and any(f["name"] == "integration" for f in fields)


def test_fit_text_shrinks_then_truncates():
    from wigix.render import font
    from wigix.widgets.http import fit_text
    assert fit_text("short", 20, 400) == ("short", 20)
    text, size = fit_text("a" * 60, 20, 150)
    assert size == 14 and text.endswith("…") and font(size).getlength(text) <= 150
