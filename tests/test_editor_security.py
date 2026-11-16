"""Editor access control, protected settings, limits, and the safety of what the engine writes and runs."""
import json
import os
import stat
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from wigix import cli, widgets
from wigix.editor import MAX_BODY, SECURITY_HEADERS, Editor, editor_url, load_token, make_handler
from wigix.engine import Dashboard, load_config, save_config

widgets.load_plugins()
XRW = {"X-Requested-With": "wigix"}
CFG = ("security: {allow_commands: false}\nplugin_dirs: []\neditor: {port: 8765}\n"
       "pages:\n- name: P\n  widgets:\n  - {type: text, text: hi}\n")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def req(url, method="GET", body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(r, timeout=8) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


@pytest.fixture
def ed(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(CFG)
    cfg.chmod(0o600)
    editor = Editor(str(cfg))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(editor))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", cfg, editor
    srv.shutdown()


def authed(editor, **extra):
    return {"Cookie": f"wigix_token={editor.token}", **extra}


# --- token -------------------------------------------------------------------------------------
def test_api_needs_the_token(ed):
    url, cfg, e = ed
    for path in ("/api/config", "/api/widgets", "/api/status", "/api/meta"):
        assert req(url + path)[0] == 401
        assert req(url + path, headers={"Cookie": "wigix_token=wrong"})[0] == 401
    assert req(url + "/api/config", headers=authed(e))[0] == 200
    assert req(url + "/api/config", headers={"X-Wigix-Token": e.token})[0] == 200
    assert req(url + "/api/config", "PUT", json.loads(req(url + "/api/config", headers=authed(e))[1]),
               headers=XRW)[0] == 401                                   # custom header alone is not enough


def test_front_page_without_token_is_a_help_page_not_the_app(ed):
    url, _, _ = ed
    code, body, _ = req(url + "/")
    assert code == 401 and b"wigix url" in body and b"palette" not in body


def test_token_link_sets_a_strict_httponly_cookie_and_redirects_clean(ed):
    url, _, e = ed
    code, _, h = req(url + f"/?token={e.token}")
    assert code == 302 and h["Location"] == "/" and e.token not in h["Location"]
    cookie = h["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and f"wigix_token={e.token}" in cookie
    assert req(url + "/?token=wrong")[0] == 401
    code, body, _ = req(url + "/", headers={"Cookie": f"wigix_token={e.token}"})
    assert code == 200 and b"palette" in body


def test_token_file_is_private_and_stable(tmp_path):
    cfg = tmp_path / "c.yaml"
    t1 = load_token(cfg)
    assert len(t1) >= 24 and load_token(cfg) == t1
    assert stat.S_IMODE(os.stat(tmp_path / "editor_token").st_mode) == 0o600
    assert editor_url(cfg, "0.0.0.0", 9000) == f"http://localhost:9000/?token={t1}"


def test_cli_url_prints_the_link(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("editor: {port: 9123}\npages: [{name: P, widgets: []}]\n")
    monkeypatch.setattr("sys.argv", ["wigix", "url", "-c", str(cfg)])
    cli.main()
    assert capsys.readouterr().out.strip() == f"http://127.0.0.1:9123/?token={load_token(cfg)}"


# --- protected settings ------------------------------------------------------------------------
@pytest.mark.parametrize("key,val", [("plugin_dirs", ["/tmp/evil"]), ("security", {"allow_commands": True}),
                                     ("editor", {"port": 1, "host": "0.0.0.0"})])
def test_code_execution_settings_cannot_be_changed_through_the_api(ed, key, val):
    url, cfg, e = ed
    cur = load_config(cfg)
    cur[key] = val
    code, body, _ = req(url + "/api/config", "PUT", cur, authed(e, **XRW))
    assert code == 400 and b"by hand" in body
    assert load_config(cfg)[key] != val                                  # file untouched


def test_unchanged_protected_settings_save_fine_and_survive(ed):
    url, cfg, e = ed
    cur = load_config(cfg)
    cur["pages"][0]["widgets"][0]["text"] = "changed"
    assert req(url + "/api/config", "PUT", cur, authed(e, **XRW))[0] == 200
    saved = load_config(cfg)
    assert saved["security"] == {"allow_commands": False} and saved["pages"][0]["widgets"][0]["text"] == "changed"
    del cur["plugin_dirs"]                                               # dropping one is a change too
    assert req(url + "/api/config", "PUT", cur, authed(e, **XRW))[0] == 400


# --- headers, host parsing, limits -------------------------------------------------------------
def test_security_headers_on_every_response(ed):
    url, _, e = ed
    for path in ("/", "/api/config"):
        _, _, h = req(url + path, headers=authed(e))
        for k, v in SECURITY_HEADERS.items():
            assert h[k] == v
    assert "frame-ancestors 'none'" in SECURITY_HEADERS["Content-Security-Policy"]


@pytest.mark.parametrize("host,ok", [("localhost:8765", True), ("LOCALHOST", True), ("127.0.0.1:9", True), ("[::1]:80", True),
                                     ("[::1]evil", False), ("localhost.evil.com", False), ("evil.com", False), ("", False),
                                     ("localhost:99999999", False), ("local host", False)])
def test_host_header_is_parsed_strictly(ed, host, ok):
    url, _, e = ed
    code = req(url + "/api/config", headers=authed(e, Host=host))[0]
    assert (code == 200) == ok


def test_oversized_and_bad_bodies_are_rejected(ed):
    url, _, e = ed
    big = b'{"pages": [], "x": "' + b"a" * (MAX_BODY + 10) + b'"}'
    try:
        code, body, _ = req(url + "/api/config", "PUT", raw=big, headers=authed(e, **XRW))
        assert code == 400 and b"request body" in body
    except (BrokenPipeError, ConnectionError, urllib.error.URLError):
        pass                    # the server answered 400 and closed while we were still sending: also a rejection
    assert req(url + "/api/preview?page=0", "POST", raw=b"not json", headers=authed(e, **XRW))[0] == 400


@pytest.mark.parametrize("wait", ["nan", "inf", "-5", "abc"])
def test_preview_wait_is_clamped_or_rejected_fast(ed, wait):
    url, cfg, e = ed
    code, _, _ = req(url + f"/api/preview?page=0&wait={wait}", "POST", load_config(cfg), authed(e, **XRW))
    assert code in (200, 400)               # never hangs


def test_handler_has_a_socket_timeout(ed):
    url, _, e = ed
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(e))
    assert srv.RequestHandlerClass.timeout == 10
    srv.server_close()


def test_preview_cache_is_bounded():
    from wigix.editor import PreviewCache
    c = PreviewCache()
    cls = widgets.REGISTRY["text"]
    for i in range(PreviewCache.MAX_ITEMS + 20):
        c.widget(cls, {"type": "text", "text": str(i)}, {})
    assert len(c.items) <= PreviewCache.MAX_ITEMS
    for v in list(c.items.values()):
        v["w"]._halt = True


# --- config file permissions -------------------------------------------------------------------
@pytest.mark.parametrize("mode", [0o600, 0o644, 0o640])
def test_save_keeps_the_files_permissions_and_gives_the_backup_the_same(tmp_path, mode):
    p = tmp_path / "c.yaml"
    p.write_text("a: 1\n")
    p.chmod(mode)
    save_config(p, {"a": 2})
    assert stat.S_IMODE(os.stat(p).st_mode) == mode
    assert stat.S_IMODE(os.stat(tmp_path / "c.yaml.bak").st_mode) == mode
    assert (tmp_path / "c.yaml.bak").read_text() == "a: 1\n" and load_config(p) == {"a": 2}


def test_new_config_files_are_private_regardless_of_umask(tmp_path):
    old = os.umask(0o022)
    try:
        save_config(tmp_path / "new.yaml", {"a": 1})
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(tmp_path / "new.yaml").st_mode) == 0o600


# --- engine actions ----------------------------------------------------------------------------
def dash(extra=None):
    cfg = {"pages": [{"name": "A", "widgets": [{"type": "text", "text": "x"}]}], "security": {"allow_commands": True}}
    cfg.update(extra or {})
    return Dashboard(cfg)


def test_url_actions_only_open_http_urls(monkeypatch):
    import wigix.engine as eng
    calls = []
    monkeypatch.setattr(eng.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    d = dash()
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "-o /tmp/x", "smb://host/share"):
        d.run_action({"url": bad})
    assert calls == []
    d.run_action({"url": "https://example.com/x"})
    assert calls == [["xdg-open", "https://example.com/x"]]


@pytest.mark.parametrize("act", [{"key": "-display"}, {"key": "ctrl+-x"}, {"type": "--delay"}, {"type": "-d 5"}])
def test_key_and_type_values_cannot_be_options(monkeypatch, act):
    import wigix.engine as eng
    calls = []
    monkeypatch.setattr(eng.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(eng.shutil, "which", lambda n: "/usr/bin/" + n if n == "xdotool" else None)
    dash().run_action(act)
    assert calls == []


def test_key_and_type_use_double_dash_separators(monkeypatch):
    import wigix.engine as eng
    calls = []
    monkeypatch.setattr(eng.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(eng.shutil, "which", lambda n: "/usr/bin/" + n if n == "xdotool" else None)
    d = dash()
    d.run_action({"key": "ctrl+alt+t"})
    d.run_action({"type": "hello world"})
    assert calls == [["xdotool", "key", "--", "ctrl+alt+t"], ["xdotool", "type", "--", "hello world"]]


# --- control API (used by the tray) --------------------------------------------------------------
def test_control_endpoint_is_guarded_and_needs_the_daemon(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("pages:\n- {name: A, widgets: [{type: text}]}\n- {name: B, widgets: [{type: text}]}\n")
    d = Dashboard(load_config(cfg), str(cfg))
    for dash_, expect in ((None, 409), (d, 200)):
        editor = Editor(str(cfg), dash_)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(editor))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_port}/api/control"
        body = {"action": "next_page"}
        assert req(url, "POST", body)[0] == 401                                     # no token
        assert req(url, "POST", body, {"Cookie": f"wigix_token={editor.token}"})[0] == 403   # no custom header
        code, out, _ = req(url, "POST", body, authed(editor, **XRW))
        assert code == expect
        if expect == 200:
            assert json.loads(out)["page_name"] == "B"
            assert req(url, "POST", {"action": "bogus"}, authed(editor, **XRW))[0] == 400
            st = json.loads(req(url.replace("control", "status"), headers=authed(editor))[1])
            assert st["connected"] is False and st["pages"] == ["A", "B"] and st["paused"] is False
        srv.shutdown()
