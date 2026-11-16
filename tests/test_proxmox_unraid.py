# SPDX-License-Identifier: GPL-3.0-only
import pytest

from fake_http import FakeHTTP
from wigix import widgets
from wigix.engine import Page
from wigix.integrations import Registry

widgets.load_plugins()

NODE = {"cpu": 0.12, "uptime": 86400 * 3, "memory": {"used": 8 * 2**30, "total": 16 * 2**30},
        "rootfs": {"used": 10 * 2**30, "total": 100 * 2**30}}
VMS = [{"vmid": 101, "name": "haos", "status": "running", "node": "pve"},
       {"vmid": 100, "name": "scripts", "status": "stopped", "node": "pve"},
       {"vmid": 200, "name": "other-node", "status": "running", "node": "other"}]


@pytest.fixture
def pve():
    s = FakeHTTP({("GET", "/api2/json/nodes"): {"data": [{"node": "pve"}]},
                  ("GET", "/api2/json/nodes/pve/status"): {"data": NODE},
                  ("GET", "/api2/json/cluster/resources"): {"data": VMS}})
    yield s
    s.stop()


def reg(cfg):
    r = Registry({"integrations": cfg})
    return r


def test_proxmox_polls_and_filters_guests(pve):
    r = reg({"pve": {"type": "proxmox", "url": pve.url, "token_id": "u@pam!t", "token": "secret"}})
    i = r.get("pve", "proxmox")
    i.refresh()
    assert i.connected and i.data["node"] == "pve"                       # first node when none is configured
    assert [g["vmid"] for g in i.data["guests"]] == [100, 101]           # sorted, other node dropped
    assert pve.requests[0].headers["authorization"] == "PVEAPIToken=u@pam!t=secret"


def test_proxmox_error_keeps_last_data(pve):
    r = reg({"pve": {"type": "proxmox", "url": pve.url, "token_id": "u@pam!t", "token": "x"}})
    i = r.get("pve", "proxmox")
    i.refresh()
    pve.routes[("GET", "/api2/json/nodes/pve/status")] = (500, {"error": "boom"})
    i.refresh()
    assert i.error and i.data is not None and not i.connected


def test_proxmox_widget_renders(pve):
    r = reg({"pve": {"type": "proxmox", "url": pve.url, "token_id": "u@pam!t", "token": "x"}})
    r.get("pve", "proxmox").refresh()
    page = Page({"grid": {"cols": 1, "rows": 1}, "widgets": [{"type": "proxmox", "at": [0, 0, 1, 1]}]}, {"integrations": r})
    assert len(page.render().getcolors(100000)) > 6


def test_widget_placeholders_when_missing_or_offline():
    for integ in ({}, {"pve": {"type": "proxmox", "url": "http://127.0.0.1:1", "token_id": "a@b!c", "token": "x"}}):
        r = reg(integ)
        page = Page({"grid": {"cols": 1, "rows": 1}, "widgets": [{"type": "proxmox", "at": [0, 0, 1, 1]}]}, {"integrations": r})
        assert len(page.render().getcolors(100000)) > 3                  # placeholder card, no exception


def test_unraid_graphql_and_errors():
    data = {"data": {"metrics": {"cpu": {"percentTotal": 10}, "memory": {"percentTotal": 50, "used": 1, "total": 2}},
                     "array": {"state": "STARTED", "capacity": {"kilobytes": {"free": "1", "used": "3", "total": "4"}}},
                     "docker": {"containers": [{"state": "RUNNING"}, {"state": "EXITED"}]}}}
    s = FakeHTTP({("POST", "/graphql"): data})
    r = reg({"t": {"type": "unraid", "url": s.url + "/graphql", "api_key": "k"}})
    i = r.get("t", "unraid")
    i.refresh()
    assert i.connected and s.requests[0].headers["x-api-key"] == "k"
    page = Page({"grid": {"cols": 1, "rows": 1}, "widgets": [{"type": "unraid", "at": [0, 0, 1, 1]}]}, {"integrations": r})
    assert len(page.render().getcolors(100000)) > 6
    s.routes[("POST", "/graphql")] = {"errors": [{"message": "bad key"}]}
    i.refresh()
    assert i.error == "bad key"
    s.stop()
