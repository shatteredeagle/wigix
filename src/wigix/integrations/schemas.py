"""Editor metadata for the built-in integrations: the options each ``type:`` accepts.

The field format is the one widgets use (``{name, type, default, help}``; types string, int, float, bool,
color, yaml, lines, select with ``options``) plus two optional keys:

- ``secret: True``  the value is a path to a file holding the secret (``token_file``, ...), never the secret itself.
  The editor warns about an inline ``token:`` / ``password:`` and offers the ``_file`` variant instead.
- ``required: True``  the integration cannot work without it (the editor warns when it is empty).

A test fails when a registered integration type has no entry here, so add one with every new integration.
Plugins that are not listed still work: the editor falls back to a plain key/value (YAML) form for them.
"""
from __future__ import annotations


def _f(name, type="string", default="", help="", **extra):
    return {"name": name, "type": type, "default": default, "help": help, **extra}


def _url(help="Base URL, e.g. http://host:port", default=""):
    return _f("url", default=default, help=help, required=True)


def _verify(default=True):
    return _f("verify", "string", "true" if default else "false",
              "Verify the TLS certificate of https URLs: true, false (only for a trusted LAN), or the path of a CA bundle "
              "for a self-signed certificate (e.g. ~/.config/wigix/ca.pem)")


def _file(name, help, **extra):
    return _f(name, help=f"Path to a file (chmod 600) containing {help}", secret=True, **extra)


def _interval(seconds):
    return _f("interval", "float", seconds, "Seconds between polls")


SCHEMAS: dict[str, list[dict]] = {
    "home_assistant": [
        _url("Home Assistant address", "http://homeassistant.local:8123"),
        _file("token_file", "a long-lived access token", required=True),
        _verify(),
    ],
    "proxmox": [
        _url("e.g. https://pve.lan:8006"),
        _f("token_id", help="API token id, e.g. user@pam!name (a PVEAuditor token is enough)", required=True),
        _file("token_file", "the API token secret", required=True),
        _f("node", help="Node name; empty means the first node"),
        _verify(),
        _interval(5.0),
    ],
    "unraid": [
        _url("GraphQL endpoint, e.g. https://tower.lan/graphql"),
        _file("api_key_file", "an Unraid API key", required=True),
        _verify(),
        _interval(5.0),
    ],
    "http": [
        _f("base_url", help="Prefix for the request paths, e.g. https://api.example.com"),
        _f("paths", "lines", [], "One request path per line, polled every interval, e.g. /v1/stats"),
        _file("token_file", "an optional bearer token"),
        _f("token_env", default="HTTP_TOKEN", help="Environment variable that holds the token when no file is set"),
        _f("headers", "yaml", {}, "Extra request headers as a JSON object"),
        _interval(30.0),
        _verify(),
    ],
    "prometheus": [
        _url("e.g. https://prometheus.example.com"),
        _file("token_file", "an optional bearer token"),
        _f("username", help="HTTP basic auth user (alternative to a token)"),
        _file("password_file", "the basic-auth password"),
        _f("queries", "yaml", {}, "Instant queries as a JSON object {name: promql}"),
        _f("ranges", "yaml", {}, 'Range queries, e.g. {"cpu_hist": {"query": "...", "hours": 6, "step": 300}}'),
        _interval(30.0),
        _verify(),
    ],
    "alertmanager": [
        _url("e.g. https://alertmanager.example.com"),
        _file("token_file", "an optional bearer token"),
        _f("username", help="HTTP basic auth user (alternative to a token)"),
        _file("password_file", "the basic-auth password"),
        _interval(30.0),
        _verify(),
    ],
    "docker": [
        _f("socket", default="/var/run/docker.sock", help="Unix socket of the Docker engine"),
        _f("url", help="Use a TCP endpoint instead of the socket, e.g. tcp://docker-host.lan:2375"),
        _interval(10.0),
        _verify(),
    ],
    "uptime_kuma": [
        _url("e.g. https://kuma.example.com"),
        _f("slug", default="default", help="Status page slug: the part after /status/ in its URL", required=True),
        _interval(30.0),
        _verify(),
    ],
    "pihole": [
        _url("Pi-hole web interface URL"),
        _f("version", "select", 6, "Pi-hole major version (5 uses an API token instead of a password)", options=[6, 5]),
        _file("password_file", "the v6 web/app password (omit when none is set)"),
        _file("api_token_file", "the v5 API token"),
        _interval(15.0),
        _verify(),
    ],
    "adguard": [
        _url("e.g. http://adguard.lan:3000"),
        _f("username", help="Admin user name"),
        _file("password_file", "the admin password"),
        _interval(15.0),
        _verify(),
    ],
    "unifi": [
        _url("The console address (not the controller port 8443)"),
        _f("username", help="A local read-only account is enough", required=True),
        _file("password_file", "the account password", required=True),
        _f("site", default="default", help="Site name"),
        _interval(10.0),
        _verify(),
    ],
    "open_meteo": [
        _f("latitude", "float", 0.0, "Latitude in degrees", required=True),
        _f("longitude", "float", 0.0, "Longitude in degrees", required=True),
        _f("units", "select", "metric", "Temperature and wind units", options=["metric", "imperial"]),
        _interval(600.0),
        _f("base_url", default="https://api.open-meteo.com", help="Only to point at a proxy or a test server"),
    ],
    "ical": [
        _file("url_file", "the secret .ics feed address"),
        _f("file", help="A local .ics file instead of a URL"),
        _f("days", "int", 7, "How many days ahead to look"),
        _f("timezone", help="IANA zone such as Europe/Paris; empty means this machine's zone"),
        _interval(300.0),
    ],
    "mpris": [
        _f("player", help="Only this player (see `playerctl -l`); empty means the active one"),
    ],
    "bambu": [
        _f("host", help="The printer's address", required=True),
        _f("serial", help="Printer serial number", required=True),
        _file("access_code_file", "the 8-character LAN access code", required=True),
        _f("port", "int", 8883, "MQTT port"),
    ],
}


def inline_secret_keys(type_name: str) -> dict[str, str]:
    """{inline key: its _file field} for a type, e.g. {"token": "token_file"}."""
    return {f["name"][:-5]: f["name"] for f in SCHEMAS.get(type_name, []) if f.get("secret") and f["name"].endswith("_file")}


def fields_for(type_name: str) -> list[dict]:
    return SCHEMAS.get(type_name, [])
