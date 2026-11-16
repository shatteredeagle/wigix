# The web editor

A small local web app to arrange widgets on a live preview, set their options and edit the rest of the
config. It works on the same YAML file the daemon reads, and the daemon hot-reloads it on Save.

```yaml
editor: {port: 8765}          # in the config: the daemon serves the editor at http://127.0.0.1:8765
```

## Opening it: the access link

The editor is protected by a secret token. Run `wigix url` (add `-c path/to/config.yaml` if yours is not the default) and
open the link it prints, e.g. `http://127.0.0.1:8765/?token=...`. The first visit stores the token in an HttpOnly,
SameSite=Strict cookie and redirects to a clean URL, so you only need the link once per browser. The token lives in
`editor_token` next to your config (mode 0600); delete the file and restart to rotate it. A bare `http://127.0.0.1:8765/`
without it shows a short help page.

Other protections: only `localhost`/`127.0.0.1`/`[::1]` Host headers are accepted (DNS rebinding), writes need an
`X-Requested-With` header, requests are limited to 1 MB and time out after 10 s, and responses carry a strict
Content-Security-Policy and `frame-ancestors 'none'`. The settings that let the daemon run code (`plugin_dirs`,
`security`, `editor`) cannot be changed through the editor: edit the file by hand. Config files you save keep their
permission bits (new ones are 0600), and so does the `.bak` backup.

`wigix editor` (or the `editor:` block above) starts it; run inside the daemon it also shows live integration
status. There are no dependencies beyond the standard library, Pillow and PyYAML, and the page needs no network.

## Layout

- **Left**: add a widget. **Middle**: page tabs, the preview with a grid overlay (drag to move, drag the corner
  to resize) and a collapsible **Raw YAML** editor. **Right**: three tabs.
- **Inspector**: page name, move page left/right, delete page; the selected widget's options (each field shows
  its `help` text; `select` fields are dropdowns), the `on_tap` action, an optional per-widget **theme
  override**, Duplicate and Remove.
- **Integrations**: one card per entry of `integrations:` with a form for its type. A colored dot shows live
  status: green connected, yellow connecting, red error (hover for the message), grey not running or not
  applied yet. It refreshes every 3 seconds. A standalone editor reports "not running".
- **Settings**: the eight theme colors (with per-color and global reset), `fps`, the
  `security.allow_commands` switch (with a warning: it lets tap actions run shell commands and press keys), and
  the read-only editor/host settings. Brightness and the page grid are in the header.

## Keyboard

| Key | Action |
| --- | --- |
| Arrow keys | move the selected widget (Shift: resize) |
| Delete / Backspace | remove it |
| Ctrl+D | duplicate |
| Ctrl+Z / Ctrl+Shift+Z (or Ctrl+Y) | undo / redo, the last 100 steps |
| Ctrl+S | save and apply |

Keys are ignored while a text field has focus, so typing and the browser's own text undo keep working
(Ctrl+S always saves).

## Raw YAML

Open **Raw YAML** to see the whole config as the file will contain it. It follows the form as you edit. Type
in it and the editor parses and checks it on the server as you go (`line N: message` on a syntax error; click
the message to jump to the line); **Apply to form** replaces the form state, and can be undone. Comments are
not preserved, because the editor always writes the file from its parsed form.

## Secrets

Integration forms never take a secret directly. Fields such as `token_file` or `password_file` hold the
**path of a file** (chmod 600) that contains the secret. Entering something that is not a path there is
refused, and an existing inline `token:` / `password:` shows a warning with a button that switches to the
`_file` variant (create the file yourself; the editor never writes or logs secrets). Saving a config with an
inline secret works but returns a warning.

## Checks on save

Save refuses (HTTP 400 with the reason) an invalid config: unknown widget or integration types, a widget
outside its grid, an unknown or malformed theme color, `fps` outside 0-17, a widget whose `integration:` names
an integration that does not exist (the message lists the defined ones), an integration missing a required
setting, or a secret field holding a non-path. The previous file is kept as `<config>.bak`.

## HTTP API

All endpoints are on the editor's host and port. Every request must carry a `Host` of localhost/127.0.0.1/[::1]
(or one listed under `editor.allowed_hosts`), and every write (`PUT`, `POST`) the header `X-Requested-With: wigix`.

| Endpoint | |
| --- | --- |
| `GET /api/config` | the config file as JSON |
| `PUT /api/config` | validate and save; returns `{ok, warnings}` or `{ok: false, error}` |
| `GET /api/widgets` | widget types with their fields |
| `GET /api/integration-types` | integration types with their fields (`integrations/schemas.py`) |
| `GET /api/status` | `{running, page, page_name, integrations: {name: {type, connected, error, updated}}}` |
| `GET /api/meta` | theme defaults and read-only editor settings |
| `GET /api/entities` | Home Assistant entity picker data |
| `POST /api/validate` | `{"yaml": text}` returns `{ok, error, line, warnings, config}`; `{"config": {...}}` returns `{ok, error, warnings, yaml}` |
| `POST /api/preview?page=N` | the page rendered as PNG, or `{error}` with status 400 |

If the preview cannot be rendered the UI shows the error over the frame rather than a stale image.

## Adding an integration type

Add its options to `src/wigix/integrations/schemas.py` (same field format as widgets, plus `secret: True` for
`*_file` path fields and `required: True`). A test fails for any registered integration that has no entry.
