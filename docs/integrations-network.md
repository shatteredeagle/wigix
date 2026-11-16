# Network, weather, calendar and media

Six integrations and five widgets for the things you glance at all day. Integrations are configured once under
`integrations:`; widgets use them automatically when there is only one of the right type, otherwise set
`integration: <name>` on the widget. Secrets can be given inline, through a `*_file` option (a file only you can
read) or an environment variable; prefer the file:

```sh
mkdir -p ~/.config/wigidash && ( umask 077; read -rsp 'secret: ' s; printf %s "$s" > ~/.config/wigidash/pihole_pw )
```

Every widget shows "loading..." until the first poll succeeds and "offline" (or a specific message) if it
cannot connect. When a later poll fails, the last good values stay on screen.

```yaml
integrations:
  pihole:  {type: pihole,  url: http://pihole.lan, password_file: ~/.config/wigidash/pihole_pw}
  unifi:   {type: unifi,   url: https://unifi.lan, username: wigidash, password_file: ~/.config/wigidash/unifi_pw}
  weather: {type: open_meteo, latitude: 48.8566, longitude: 2.3522, units: metric}
  cal:     {type: ical, url_file: ~/.config/wigidash/cal_url, timezone: Europe/Paris}
  media:   {type: mpris}

pages:
  - name: Home
    widgets:
      - {type: dns_stats, name: Pi-hole, at: [0, 0, 1, 1]}
      - {type: unifi, at: [1, 0, 1, 1]}
      - {type: weather, name: Paris, at: [2, 0, 2, 2]}
      - {type: calendar_agenda, max_events: 4, at: [0, 1, 2, 2]}
      - {type: media_player, at: [2, 2, 2, 1]}
```

## DNS blockers: `pihole`, `adguard` and `dns_stats`

| integration | options |
|---|---|
| `pihole` | `url`, `password` / `password_file` / `$PIHOLE_PASSWORD`, `version` (6 default, or 5), `api_token(_file)` for v5, `verify` |
| `adguard` | `url`, `username`, `password` / `password_file` / `$ADGUARD_PASSWORD`, `verify` |

Both polls every 15 s and expose the same data: `queries`, `blocked`, `percent_blocked`, `enabled` (plus
`domains_blocked`/`clients` where available), so one widget serves either.

`dns_stats` shows queries today, the blocked count, a percent-blocked bar and a blocking / paused indicator.
It finds its integration by `integration:` or, if there is exactly one Pi-hole/AdGuard configured, by itself.

- **Pi-hole v6** logs in with `POST /api/auth` and sends the session id in the `sid` header; an expired session
  is renewed automatically. Use the web interface password (or an app password); leave it out if you have none.
- **Pi-hole v5** uses `/admin/api.php?summaryRaw` with the token from Settings -> API (`version: 5`).
- **AdGuard Home** uses HTTP basic auth against `/control/stats` and `/control/status`. Its numbers cover the
  retention window you set in AdGuard (24 h by default).
- **Toggling**: `tap_toggle: true` makes a tap switch Pi-hole **v6** blocking on and off (it stays off until you tap
  again). It is off by default so a stray touch cannot take down your DNS filtering, and is not available for v5 or AdGuard.

## UniFi: `unifi`

```yaml
unifi: {type: unifi, url: https://unifi.lan, username: wigidash, password_file: ~/.config/wigidash/unifi_pw, site: default}
```

For UniFi OS consoles (UDM, UDR, Cloud Key Gen2+, ...). Create a **local** user with read-only access to Network;
Ubiquiti cloud/SSO accounts with 2FA cannot log in this way. The integration signs in with `POST /api/auth/login`,
keeps the cookie and CSRF token, and signs in again when the console answers 401. `verify` defaults to false
because consoles ship a self-signed certificate; set it to true if you installed a real one.

The `unifi` widget shows the WAN status (green dot for ok, amber for warning, red otherwise), download and upload
throughput, client counts (Wi-Fi/wired on bigger cards) and adopted devices up/down.

## Weather: `open_meteo` and `weather`

```yaml
weather: {type: open_meteo, latitude: 48.8566, longitude: 2.3522, units: imperial, interval: 900}
```

[Open-Meteo](https://open-meteo.com) needs no account or key (free for non-commercial use). `units` is `metric`
(Celsius, km/h) or `imperial` (Fahrenheit, mph). It is polled every 10 minutes by default.

The `weather` widget draws the current temperature, a condition icon and text (from the WMO weather code), today's
high/low, wind and humidity. Give it a 2x2 (or taller) cell and a 3-day forecast row appears (`forecast: false`
hides it). Codes without a known icon still show their number.

## Calendar: `ical` and `calendar_agenda`

```yaml
cal: {type: ical, url_file: ~/.config/wigidash/cal_url, days: 7, timezone: Europe/Paris}
# or a local file:  {type: ical, file: ~/calendar.ics}
```

Works with any iCalendar feed: Google Calendar's "Secret address in iCal format", Nextcloud/Fastmail share links,
or a file. The address of a private feed **is** the password, so keep it in `url_file` (the integration also
keeps it out of error messages and logs). The feed is fetched every 5 minutes; recurring events are expanded for the
next `days` days (default 7) and events that already ended today are dropped. `timezone` is used for events
without a zone and for display (default: this machine's zone).

The `calendar_agenda` widget lists the next `max_events` (default 4, as many as fit) with a relative day
(`Today`, `Tomorrow`, `Wed`, then `Dec 3`) and time (`time_format`, default `%H:%M`), `all day` for all-day
events, and "nothing planned" when the week is empty. A full-width cell shows one line per event.

## Media: `mpris` and `media_player` (Linux)

```yaml
media: {type: mpris, player: spotify}        # player is optional: default is whatever is active
```

Needs the `playerctl` package (`pacman -S playerctl`, `apt install playerctl`, ...); without it the widget says
"playerctl missing". It works with any MPRIS player (Spotify, Firefox/Chromium, mpv, VLC, ...). Status is polled
once a second; the progress bar is interpolated between polls.

The `media_player` widget shows title, artist, a progress bar and, when the cell is wide enough, the album cover
(`file://` or `http(s)://` art URLs, fetched once per track in the background). Tap the left third for previous,
the middle for play/pause, the right third for next.

## Testing

`tests/test_network_personal.py` runs all of these against `tests/fake_http.py` and a patched `playerctl`, with
no real services involved.
