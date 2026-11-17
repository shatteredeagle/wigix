# Home Assistant

One live connection, shared by every Home Assistant widget. State arrives over HA's WebSocket API,
so widgets update instantly with no polling; the connection reconnects on its own (backoff up to 30 s)
and widgets show "connecting…" / "HA offline" instead of crashing.

## Set up

1. In Home Assistant: your profile -> Security -> **Long-lived access tokens** -> Create. Copy it.
2. Save it to a file only you can read:
   ```sh
   mkdir -p ~/.config/wigix && ( umask 077; read -rsp 'token: ' t; printf %s "$t" > ~/.config/wigix/ha_token )
   ```
3. Add the integration to your config (the web editor picks it up on save):
   ```yaml
   integrations:
     ha:
       type: home_assistant
       url: http://homeassistant.local:8123
       token_file: ~/.config/wigix/ha_token
       # verify: ~/.config/wigix/ca.pem   # self-signed https: a CA file, or false
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


## `air`: radon and room climate graphs

A btop-style panel for an air-quality sensor (built for an Aranet Radon Plus, works with any numeric sensors): one big
radon graph (a week by default) with dashed lines for the weekly average and the WHO (2.7 pCi/L) and EPA (4 pCi/L)
action levels, and temperature, humidity and pressure graphs underneath. The radon value is coloured by those levels.

```yaml
- type: air
  name: Basement air
  integration: ha
  radon: sensor.radon_pci_l
  radon_avg: sensor.radon_7_day_average_pci_l   # drawn as a dashed line and shown in the title
  temperature: sensor.aranetrn_3cfd2_temperature
  humidity: sensor.aranetrn_3cfd2_humidity
  pressure: sensor.aranetrn_3cfd2_pressure
  hours: 168          # radon graph length; env_hours: 24 for the climate graphs
  at: [1, 1, 1, 1]
```

Every sensor is optional. Levels follow the sensor's unit (pCi/L or Bq/m³); override them with `warn:` and `crit:`.
History comes from Home Assistant's recorder and refreshes every 5 minutes, so the integration needs a long-lived access
token (`token_file:`).


## `sensor_graphs`: a few sensors as stacked graphs

```yaml
- type: sensor_graphs
  name: Climate
  integration: ha
  sensors:
    - {entity: sensor.frog_temperature, label: Warm side}
    - {entity: sensor.frog_cool_temperature, label: Cool side}
    - {entity: sensor.frog_cool_humidity, label: Humidity}
  battery: sensor.frog_sensor_battery     # optional, shown in the title
  hours: 24
  at: [2, 1, 1, 2]
```

Each sensor gets a labelled graph with its current value, zoomed to its own range. Pair it with a `camera` widget for a
terrarium or pet page: a camera on the top row, `sensor_graphs` in the two rows below it. With a Frigate NVR the cheapest
live source is its go2rtc restream (`stream_url: rtsp://frigate.lan:8554/<camera>`), which keeps the load off the camera.


## `multi_graph`: several sensors as coloured lines

```yaml
- type: multi_graph
  name: Enclosures · 24 h
  integration: ha
  graphs:
    - title: Temperature °F
      series:
        - {entity: sensor.spider_2_temperature, label: GrapeAss, color: "#c084fc"}
        - {entity: sensor.frog_cool_temperature, label: Frog cool, color: "#4ad682", dashed: true}
    - title: Humidity %
      series:
        - {entity: sensor.spider_2_humidity, label: GrapeAss, color: "#c084fc"}
  at: [1, 1, 1, 1]
```

One graph per group, one coloured line per sensor, with a legend of the current values. Give the matching `camera`
widget the same colour as `border: "#c084fc"` and each picture is outlined in its line's colour.
