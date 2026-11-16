# Writing widgets and integrations

Everything here is a plain Python module dropped into a folder. Built-ins live in
`src/wigix/widgets/` and `src/wigix/integrations/` and are discovered automatically (no registry to edit).
Your own go in a folder listed under `plugin_dirs:` in the config, or in a package that declares a
`wigix.widgets` entry point.

## An integration = a shared connection

Configured once, used by many widgets:

```yaml
integrations:
  pve: {type: proxmox, url: https://pve:8006, token_id: u@pam!t, token_file: ~/.config/wigix/pve_token}
```

Most integrations just poll an API. Subclass `PollingIntegration` (`integrations/polling.py`):

```python
from . import integration
from .polling import PollingIntegration

@integration("my_service")
class MyService(PollingIntegration):
    interval = 10.0                       # seconds; users can override with `interval:` in the config
    def poll(self):                       # runs on a worker thread; raise on failure
        r = requests.get(self.cfg["url"] + "/stats", timeout=6); r.raise_for_status()
        return r.json()                   # becomes `self.data`
```

Rules: do all I/O in `poll()` (never in widgets); read secrets with `read_secret(cfg.get("token"), cfg.get("token_file"), "ENV_VAR")`
so users can keep them in a 0600 file; keep the previous `data` on failure (the base class does); use `verify:` for TLS
(default true; Proxmox/Unraid default false because they ship self-signed certs). Integrations that stream
(Home Assistant's websocket) subclass `Integration` directly (see `integrations/home_assistant.py`).

## A widget = a card on the grid

```python
from . import IntegrationWidget, widget
from ..render import THEME

@widget("my_service")
class MyServiceWidget(IntegrationWidget):
    """One-line description shown in the editor palette."""
    integration_type = "my_service"
    interval = 3600.0                    # widgets of integrations don't poll; the integration does
    fields = [{"name": "name", "type": "string", "default": ""}]   # editor form: string|int|float|bool|color|yaml|lines|entity

    def draw(self, cv, box):
        d = self.guard(cv, box)          # draws "loading…/offline" and returns None until data exists
        if d is None:
            return
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name"))        # card + title; returns the inner box
        cv.text((x0, y0), f"{d['value']}", size=40, bold=True)
        cv.bar((x0, y1 - 12, x1, y1), d["percent"] / 100)
```

`draw` must be fast and never block or do I/O. Canvas helpers: `card`, `text(..., max_width=)` (shrinks to fit), `bar`,
`spark`, plus the raw PIL `cv.d` and `cv.image`. Use `THEME[...]` colors (not hard-coded) so themes work.
Interactive widgets implement `on_tap(x, y) -> bool`; return True when handled. `draw_full(cv, box)` customises the
`on_tap: fullscreen` view (defaults to `draw`). Widgets that poll something themselves (no integration) subclass
`Widget` and implement `update()` (worker thread) plus `draw()`.

## Tests

Use `tests/fake_http.py` (`FakeHTTP` for TCP, `FakeUnixHTTP` for sockets) instead of real services; see
`tests/test_proxmox_unraid.py` for the pattern: call `integration.refresh()` once instead of starting the thread,
render the widget through `Page(...).render()`, and assert it draws something and survives missing/offline integrations.
Run `pytest` before opening a PR.

## Docs

Add a short section to a docs page (`docs/integrations-*.md`, or a new one linked from the README): config example, what each widget shows, auth notes.
