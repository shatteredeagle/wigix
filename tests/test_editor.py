# SPDX-License-Identifier: GPL-3.0-only
"""Editor: API behaviour (stdlib http against a real server) and one browser test driven by Playwright."""
import json
import pathlib
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from wigix import widgets
from wigix.editor import DEFAULT_THEME, Editor, make_handler
from wigix.engine import Dashboard, load_config
from wigix.integrations import REGISTRY, load_builtin
from wigix.integrations.schemas import SCHEMAS, inline_secret_keys
from wigix.render import _BASE_THEME, set_theme

widgets.load_plugins()
load_builtin()
XRW = {"X-Requested-With": "wigix"}
TOKEN = [""]      # the token of the editor most recently started by serve()
BASE = "pages:\n- name: P\n  widgets:\n  - {type: text, text: hi}\n"


def serve(tmp_path, text=BASE, dashboard=False):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(text)
    ed = Editor(str(cfg), Dashboard(load_config(cfg), str(cfg)) if dashboard else None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ed))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    TOKEN[0] = ed.token
    return f"http://127.0.0.1:{srv.server_port}", cfg, srv


@pytest.fixture
def editor(tmp_path):
    url, cfg, srv = serve(tmp_path)
    yield url, cfg
    srv.shutdown()


def call(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Cookie": f"wigix_token={TOKEN[0]}", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(url, path, body, method="POST"):
    code, raw = call(url + path, method, body, XRW)
    return code, json.loads(raw)


# --- integration schemas -----------------------------------------------------------------------
def test_every_registered_integration_has_a_schema():
    missing = sorted(set(REGISTRY) - set(SCHEMAS))
    assert not missing, f"add editor fields for {missing} to integrations/schemas.py"
    assert not sorted(set(SCHEMAS) - set(REGISTRY)), "schema for an integration that does not exist"


def test_schema_fields_are_well_formed():
    kinds = {"string", "int", "float", "bool", "color", "yaml", "lines", "select"}
    for t, fields in SCHEMAS.items():
        names = [f["name"] for f in fields]
        assert len(names) == len(set(names)), t
        for f in fields:
            assert f["type"] in kinds and {"name", "type", "default", "help"} <= set(f), (t, f)
            assert f["type"] != "select" or f["options"], (t, f)
            if f.get("secret"):
                assert f["name"].endswith("_file"), (t, f)
    assert inline_secret_keys("proxmox") == {"token": "token_file"}


def test_integration_types_endpoint(editor):
    url, _ = editor
    types = {t["type"]: t for t in json.loads(call(url + "/api/integration-types")[1])}
    assert set(types) == set(REGISTRY)
    assert types["home_assistant"]["fields"][0]["name"] == "url" and types["pihole"]["doc"]


def test_theme_defaults_match_render():
    set_theme({})
    from wigix.render import parse_color
    assert {k: parse_color(v) for k, v in DEFAULT_THEME.items()} == dict(_BASE_THEME)


# --- status ------------------------------------------------------------------------------------
def test_status_standalone_and_in_daemon(tmp_path):
    (tmp_path / "a").mkdir()
    url, _, srv = serve(tmp_path / "a")
    st = json.loads(call(url + "/api/status")[1])
    assert st["running"] is False and "not running" in st["message"]
    srv.shutdown()
    d = tmp_path / "b"
    d.mkdir()
    url, _, srv = serve(d, BASE + "integrations:\n  api: {type: http, base_url: 'http://127.0.0.1:9'}\n", dashboard=True)
    st = json.loads(call(url + "/api/status")[1])
    srv.shutdown()
    assert st["running"] and st["page_name"] == "P"
    assert st["integrations"]["api"]["type"] == "http" and st["integrations"]["api"]["connected"] is False


# --- guards ------------------------------------------------------------------------------------
def test_new_endpoints_keep_the_guards(editor):
    url, _ = editor
    for path in ("/api/status", "/api/integration-types", "/api/meta"):
        assert call(url + path, headers={"Host": "evil.example.com"})[0] == 403
    assert call(url + "/api/validate", "POST", {"yaml": BASE})[0] == 403            # no X-Requested-With
    assert call(url + "/api/validate", "POST", {"yaml": BASE}, {"Host": "evil.example.com", **XRW})[0] == 403


# --- validation --------------------------------------------------------------------------------
def test_validate_yaml_reports_line_and_does_not_echo_content(editor):
    url, _ = editor
    code, r = post(url, "/api/validate", {"yaml": BASE})
    assert code == 200 and r["ok"] and r["config"]["pages"][0]["name"] == "P"
    code, r = post(url, "/api/validate", {"yaml": "pages:\n- name: x\n  widgets: [ {type: text, token: hunter2\n"})
    assert not r["ok"] and r["line"] and "hunter2" not in json.dumps(r)
    code, r = post(url, "/api/validate", {"yaml": "pages: [{name: x, widgets: [{type: nope}]}]"})
    assert not r["ok"] and "nope" in r["error"]
    assert post(url, "/api/validate", {"yaml": "- just\n- a list\n"})[1]["ok"] is False


def test_validate_config_returns_round_trippable_yaml(editor):
    import yaml
    url, _ = editor
    cfg = {"pages": [{"name": "P", "grid": {"cols": 2, "rows": 1}, "widgets": [{"type": "text", "text": "x", "at": [0, 0, 1, 1]}]}],
           "theme": {"accent": "#112233"}}
    r = post(url, "/api/validate", {"config": cfg})[1]
    assert r["ok"] and yaml.safe_load(r["yaml"]) == cfg
    again = post(url, "/api/validate", {"yaml": r["yaml"]})[1]
    assert again["ok"] and again["config"] == cfg


def test_widget_must_reference_an_existing_integration(editor):
    url, cfg = editor
    page = {"name": "P", "widgets": [{"type": "text", "text": "x", "integration": "nas"}]}
    code, r = post(url, "/api/config", {"pages": [page]}, "PUT")
    assert code == 400 and "'nas'" in r["error"] and "Integrations panel" in r["error"]
    ok = {"pages": [page], "integrations": {"nas": {"type": "unraid", "url": "https://t/graphql", "api_key_file": "~/k"}}}
    assert post(url, "/api/config", ok, "PUT")[0] == 200
    legacy = {"pages": [dict(page, widgets=[{"type": "text", "integration": "ha"}])], "home_assistant": {"url": "http://h"}}
    assert post(url, "/api/config", legacy, "PUT")[0] == 200       # the legacy block counts as integration "ha"


def test_integration_and_setting_validation(editor):
    url, _ = editor
    pages = [{"name": "P", "widgets": []}]
    bad = [{"integrations": {"x": {"type": "nope"}}}, {"integrations": {"x": "str"}}, {"theme": {"accent": "blue-ish"}},
           {"theme": {"nokey": "#fff"}}, {"fps": 99}, {"brightness": 150}, {"security": "yes"}]
    for extra in bad:
        code, r = post(url, "/api/config", {"pages": pages, **extra}, "PUT")
        assert code == 400 and r["error"], extra
    assert post(url, "/api/config", {"pages": pages, "fps": 10, "theme": {"accent": "#abcdef"}}, "PUT")[0] == 200
    code, r = post(url, "/api/config", {"pages": pages, "security": {"allow_commands": True}}, "PUT")
    assert code == 400 and "by hand" in r["error"]            # code-execution settings are not editable here


def test_secrets_are_files_never_inline_or_echoed(editor):
    url, cfg = editor
    pages = [{"name": "P", "widgets": []}]
    secret = "s3cr3t-token-value"
    code, r = post(url, "/api/config", {"pages": pages, "integrations": {
        "pve": {"type": "proxmox", "url": "https://p", "token_id": "u@pam!t", "token_file": secret}}}, "PUT")
    assert code == 400 and secret not in json.dumps(r) and "path of a file" in r["error"]
    assert not (cfg.parent / "c.yaml.bak").exists() or secret not in cfg.read_text()
    code, r = post(url, "/api/config", {"pages": pages, "integrations": {
        "pve": {"type": "proxmox", "url": "https://p", "token_id": "u@pam!t", "token": secret}}}, "PUT")
    assert code == 200 and any("token_file" in w for w in r["warnings"]) and secret not in json.dumps(r)


def test_incomplete_integration_is_named_in_the_error(editor):
    url, _ = editor
    cfg = {"pages": [{"name": "P", "widgets": []}], "integrations": {"hole": {"type": "pihole"}}}
    code, r = post(url, "/api/config", cfg, "PUT")
    assert code == 400 and "'url'" in r["error"] and "hole" in r["error"]
    code, r = post(url, "/api/preview?page=0", cfg)
    assert code == 400 and "'url'" in r["error"]


def test_preview_error_is_reported(editor):
    url, _ = editor
    code, r = post(url, "/api/preview?page=0", {"pages": [{"name": "P", "widgets": [{"type": "no_such_widget"}]}]})
    assert code == 400 and "no_such_widget" in r["error"]


def test_preview_uses_edited_theme(editor):
    url, _ = editor
    from io import BytesIO
    from PIL import Image
    cfg = {"pages": [{"name": "P", "widgets": []}], "theme": {"bg": "#102030"}}
    code, png = call(url + "/api/preview?page=0", "POST", cfg, XRW)
    assert code == 200 and Image.open(BytesIO(png)).convert("RGB").getpixel((3, 3)) == (0x10, 0x20, 0x30)


def test_save_keeps_backup_and_writes_yaml(editor):
    url, cfg = editor
    new = {"pages": [{"name": "Q", "widgets": [{"type": "text", "text": "new"}]}]}
    assert post(url, "/api/config", new, "PUT")[0] == 200
    assert "name: Q" in cfg.read_text() and "name: P" in (cfg.parent / "c.yaml.bak").read_text()


# --- browser -----------------------------------------------------------------------------------
CHROME = "/usr/bin/google-chrome-stable"


def test_editor_in_a_browser(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    if not pathlib.Path(CHROME).exists():
        pytest.skip("google-chrome-stable not installed")
    url, cfg, srv = serve(tmp_path, "pages:\n- name: P\n  grid: {cols: 4, rows: 3, gap: 10}\n  widgets: []\n")
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            except Exception as e:      # a Chrome that cannot start here is an environment problem, not a failure
                pytest.skip(f"cannot launch Chrome: {e}")
            # bypass_csp only because Playwright's wait_for_function evals strings; the CSP itself is exercised
            # by test_editor_loads_under_its_content_security_policy below
            pg = browser.new_context(bypass_csp=True, viewport={"width": 1500, "height": 900}).new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"{url}/?token={TOKEN[0]}")
            pg.wait_for_selector("#palette button")

            pg.click("#palette button[data-type=text]")                      # add a widget
            pg.wait_for_selector(".wb")
            assert pg.locator(".wb").count() == 1
            box = pg.locator(".wb").bounding_box()
            pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)    # drag it one cell right
            pg.mouse.down()
            pg.mouse.move(box["x"] + box["width"] * 1.7, box["y"] + box["height"] / 2, steps=8)
            pg.mouse.up()
            moved = pg.locator(".wb").bounding_box()
            assert moved["x"] > box["x"] + box["width"] * 0.5

            pg.keyboard.press("Control+d")                                              # duplicate, then undo twice
            assert pg.locator(".wb").count() == 2
            pg.keyboard.press("Control+z")
            assert pg.locator(".wb").count() == 1
            pg.keyboard.press("Control+z")                                              # undoes the drag
            assert abs(pg.locator(".wb").bounding_box()["x"] - box["x"]) < 2
            pg.keyboard.press("Control+Shift+z")
            assert pg.locator(".wb").bounding_box()["x"] > box["x"] + box["width"] * 0.5

            pg.click("#yamlbox summary")                                                # YAML mirrors the form...
            pg.wait_for_function("document.querySelector('#ytext').value.includes('type: text')")
            text = pg.input_value("#ytext")
            assert "rows: 3" in text
            pg.fill("#ytext", "pages:\n- name: Edited\n  grid: {cols: 4, rows: 3, gap: 10}\n  widgets:\n  - {type: text, text: from yaml, at: [0, 0, 1, 1]}\n")
            pg.wait_for_function("!document.querySelector('#yapply').disabled")
            pg.click("#yapply")                                                         # ...and back
            assert pg.locator(".tab.on").inner_text() == "Edited" and pg.locator(".wb").count() == 1
            pg.fill("#ytext", "pages: [oops")
            pg.wait_for_function("document.querySelector('#ystat').className==='err'")
            assert pg.input_value("#ytext") == "pages: [oops" and pg.locator("#yapply").is_disabled()
            pg.click("#yreset")
            pg.wait_for_function("document.querySelector('#ytext').value.includes('Edited')")

            pg.keyboard.press("Control+s")                                              # save
            pg.wait_for_function("document.querySelector('#msg').textContent.startsWith('saved')")
            browser.close()
        saved = cfg.read_text()
        assert "name: Edited" in saved and "from yaml" in saved
        assert not errors, errors
    finally:
        srv.shutdown()


def test_editor_loads_under_its_content_security_policy(tmp_path):
    """The strict CSP must not break the page: no violations, widgets palette and preview image load."""
    sync_api = pytest.importorskip("playwright.sync_api")
    if not pathlib.Path(CHROME).exists():
        pytest.skip("google-chrome-stable not installed")
    url, cfg, srv = serve(tmp_path, "pages:\n- name: P\n  grid: {cols: 4, rows: 3, gap: 10}\n  widgets:\n  - {type: text, text: hi}\n")
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            except Exception as e:
                pytest.skip(f"cannot launch Chrome: {e}")
            pg = browser.new_page()
            problems = []
            pg.on("pageerror", lambda e: problems.append(str(e)))
            pg.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
            pg.goto(f"{url}/?token={TOKEN[0]}")
            pg.wait_for_selector("#palette button")
            pg.wait_for_selector(".wb")
            for _ in range(40):                                         # the first preview waits ~1.5 s for live data
                if pg.evaluate("document.querySelector('#img').naturalWidth") > 0:
                    break
                pg.wait_for_timeout(250)
            assert pg.evaluate("document.querySelector('#img').naturalWidth") > 0     # the blob: image loaded under the CSP
            assert problems == []
            browser.close()
    finally:
        srv.shutdown()
