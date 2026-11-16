# Monitoring integrations

Five integrations for the usual homelab monitoring stack, each with its own widgets. Like every
integration they are configured once under `integrations:` and polled on a background thread, so
widgets never wait on the network: a service that is down shows "offline" and the last good data stays on screen.
Widgets use an integration automatically when it is the only one of its type; otherwise set `integration: <name>`.

| integration | widgets | polls |
|---|---|---|
| `http` | `json_value`, `json_list` | any JSON URLs you list |
| `prometheus` | `prom_value`, `prom_graph` | named instant and range queries |
| `alertmanager` | `alertmanager_alerts` | firing alerts |
| `docker` | `docker_containers` | the container list |
| `uptime_kuma` | `uptime_kuma` | a public status page |

All secrets can be given inline (`token:`), from a file (`token_file:`, keep it `chmod 600`) or from an environment variable.

## Generic JSON over HTTP

```yaml
integrations:
  api:
    type: http
    base_url: https://api.example.com
    token_file: ~/.config/wigix/api_token   # optional bearer token (or token: / env HTTP_TOKEN)
    headers: {Accept: application/json}        # optional extra headers, e.g. {X-Api-Key: ...}
    paths: [/v1/stats, /v1/queue]              # polled every `interval` seconds (default 30)
    verify: true                               # false for self-signed https

widgets:
  - {type: json_value, at: [0, 0, 1, 1], name: Queue, request: /v1/stats, path: "data.queue.length"}
  - {type: json_value, at: [1, 0, 1, 1], name: Temp, request: /v1/stats, path: "sensors[0].temp",
     unit: "°C", precision: 1, color_rules: [{above: 70, color: "#f05454"}]}
  - {type: json_list, at: [2, 0, 2, 2], title: Jobs, request: /v1/queue, path: jobs,
     name_path: title, value_path: progress.percent, unit: "%"}
```

- `request` is one of the URL paths the integration polls (blank = the first). A request that is not in
  `paths` is added the first time a widget asks for it and fills in one poll later.
- `path` is a small path language: `data.items[0].name`, `[-1]` for the last item, `a["key.with.dots"]`.
  A missing step shows a dash rather than an error.
- `json_value` shows a number (numeric strings work too), text, or the length of a list/object.
  `color_rules` color numbers, `state_colors: {ok: "#4ad682"}` color text.
- `json_list` shows rows from a list (`name_path` / `value_path` are read inside each item; a plain
  list of values works too) or from an object (rows are key / value). `max_rows` caps it; the rest becomes "+N more".

## Prometheus and Alertmanager

```yaml
integrations:
  prom:
    type: prometheus
    url: https://prometheus.example.com
    token_file: ~/.config/wigix/prom_token  # bearer token, or username + password(_file) for basic auth
    queries:                                   # name -> PromQL (instant query, /api/v1/query)
      cpu: 'avg(1 - rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100'
    ranges:                                    # name -> range query (/api/v1/query_range)
      cpu_hist: {query: 'avg(1 - rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100', hours: 6, step: 300}
  alerts:
    type: alertmanager
    url: https://alertmanager.example.com      # same auth options as prometheus

widgets:
  - {type: prom_value, at: [0, 0, 1, 1], query: cpu, name: CPU, unit: "%", precision: 0,
     color_rules: [{above: 85, color: "#f05454"}]}
  - {type: prom_graph, at: [1, 0, 2, 1], range: cpu_hist, name: "CPU 6h", unit: "%"}
  - {type: alertmanager_alerts, at: [3, 0, 1, 2]}
```

- `query:` / `range:` on a widget are the *names* from the integration config, so the PromQL lives in one place.
- Make each query return one series (`sum`, `avg`, `max`, ...); if several come back the first is used.
  An empty result shows a dash. `step` defaults to about 60 points over `hours`.
- `prom_graph` plots relative to the minimum (so a small change is visible), with the current value and min/max.
- `alertmanager_alerts` lists firing alerts (silenced and inhibited ones are excluded), critical first, colored by
  the `severity` label (critical red, warning amber, anything else blue), `all clear` when there are none, and `+N more`
  when they do not fit.

## Docker

```yaml
integrations:
  docker:
    type: docker
    socket: /var/run/docker.sock      # default; the user running wigix must be able to read it
    # url: tcp://docker-host.lan:2375 # or a TCP / https endpoint
widgets:
  - {type: docker_containers, at: [0, 0, 2, 2], title: Containers, only_problems: false,
     names: ["media-*", "db"], max_rows: 12}
```

Shows `running/total up`, then a row per container with a state dot: green running, red exited / dead /
unhealthy, amber paused / restarting / still starting. Problems are listed first. `names` filters
(wildcards allowed), `only_problems` hides healthy containers ("all healthy" when none are left).

Only read-only `GET /containers/json` is used. Do not expose the Docker socket over TCP without TLS and
authentication: anyone who can reach it controls the host. Prefer the local socket or a socket proxy that allows only `GET`.

## Uptime Kuma

```yaml
integrations:
  kuma:
    type: uptime_kuma
    url: https://kuma.example.com
    slug: default          # https://kuma.example.com/status/<slug>
widgets:
  - {type: uptime_kuma, at: [0, 0, 1, 2], title: Uptime}
```

Uses the public status page API, so no login or API key: create a status page in Kuma and add the monitors you
want to see (only those are visible). The widget shows a dot per monitor (green up, red down, amber pending,
blue maintenance, grey no data yet), the latest ping, and `all up` / `N down` in the header. Down monitors come first;
`only_problems: true` hides the rest.

## Testing

`tests/test_generic_monitoring.py` runs everything against `tests/fake_http.py` (TCP and unix-socket fakes), so it needs
no real services. The Docker tests use `FakeUnixHTTP`.
