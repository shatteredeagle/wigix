# Home Assistant

One live connection, shared by every Home Assistant widget. State arrives over HA's WebSocket API,
so widgets update instantly with no polling; the connection reconnects on its own (backoff up to 30 s)
and widgets show "connecting…" / "HA offline" instead of crashing.

## Set up

1. In Home Assistant: your profile -> Security -> **Long-lived access tokens** -> Create. Copy it.
2. Save it to a file only you can read:
   ```sh
   mkdir -p ~/.config/wigidash && ( umask 077; read -rsp 'token: ' t; printf %s "$t" > ~/.config/wigidash/ha_token )
   ```
3. Add the integration to your config (the web editor picks it up on save):
   ```yaml
   integrations:
     ha:
       type: home_assistant
       url: http://homeassistant.local:8123
       token_file: ~/.config/wigidash/ha_token
       # verify: false        # self-signed https
   ```
   Widgets use it automatically when it is the only HA integration; otherwise set `integration: ha`.

A bad token is reported (`HA offline`) and not retried, so it cannot trip HA's login ban.

## Widgets

| type | what it does | key options |
|---|---|---|
| `ha_entity` | big value + "changed 3m ago" | `entity`, `name`, `unit`, `precision`, `color_rules` |
| `ha_list` | rows of name / value | `entities`, `title` |
| `ha_toggle` | tap toggles a light/switch/fan/etc; shows brightness | `entity` |
| `ha_button` | tap calls any service (scenes, scripts, ...) | `service`, `entity`, `data`, `label` |
| `ha_graph` | history sparkline, min/max | `entity`, `hours` |
| `ha_gauge` | arc gauge | `entity`, `min`, `max`, `color_rules` |
| `ha_alerts` | active problems: explicit entities and/or auto-scan of problem-class binary sensors | `entities`, `alert_states`, `scan` |

Colors by value: `color_rules: [{above: 80, color: "#f05454"}, {below: 20, color: "#569cff"}]`
(numbers) or `state_colors: {on: "#4ad682"}` (non-numeric states).

Any widget can also do something on tap through the YAML:
`on_tap: {ha_service: light.toggle, entity: light.desk}`, `on_tap: next_page`, `on_tap: {page: Name}`.

## Testing without a Home Assistant

`tests/fake_ha.py` is a small fake HA (websocket auth, states, live events, history) used by the test
suite (`pip install -e '.[dev]' && pytest`). It is also handy for developing widgets offline.
