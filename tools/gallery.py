#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-only
"""Render every registered widget with sample data and write docs/gallery.md + docs/img/gallery/<type>.png.

    .venv/bin/python tools/gallery.py

Re-run it after adding a widget: new types show up automatically. Widgets that need an integration use the fakes from
tools/make_screenshots.py; types without a sample entry below are drawn with their field defaults (so they show their
placeholder), and a widget that fails to draw is listed at the end and gets no image.
"""
from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import make_screenshots as ms  # noqa: E402  (also sets up sys.path for wigix and the test fakes)
import yaml  # noqa: E402
from PIL import Image  # noqa: E402

from wigix import widgets  # noqa: E402
from wigix.render import THEME, set_theme  # noqa: E402

ROOT = ms.ROOT
OUT = ROOT / "docs" / "img" / "gallery"

# type -> sample widget options (besides type/at)
SAMPLES = {
    "alertmanager_alerts": {},
    "button": {"label": "Open wiki", "color": "#2a4a7f", "on_tap": {"url": "https://wiki.example.com"}},
    "calendar_agenda": {"max_events": 4},
    "checks": None,                                   # filled from the demo in main()
    "clock": {},
    "dns_stats": {},
    "docker_containers": {},
    "ha_alerts": {},
    "ha_button": {"label": "Movie night", "service": "scene.turn_on", "entity": "scene.movie_night"},
    "ha_entity": {"entity": "sensor.living_room_temperature", "precision": 1},
    "ha_gauge": {"entity": "sensor.power_consumption", "min": 0, "max": 1000},
    "ha_graph": {"entity": "sensor.living_room_temperature", "hours": 24, "precision": 1},
    "ha_list": {"title": "Climate", "entities": ["sensor.living_room_temperature", "sensor.bedroom_temperature", "sensor.outdoor_temperature"]},
    "ha_toggle": {"entity": "light.living_room"},
    "json_list": {"title": "Jobs", "request": "/v1/jobs", "path": "jobs", "name_path": "name", "value_path": "value", "unit": "%"},
    "json_value": {"name": "Active jobs", "request": "/v1/stats", "path": "queue.active"},
    "macro_pad": {"columns": 2, "buttons": [{"label": "Mute", "color": "#5b2a9e", "on_tap": "next_page"},
                                            {"label": "Focus", "color": "#9e2a6b", "on_tap": "fullscreen"}]},
    "media_player": {},
    "node": {"name": "nas", "url": "http://nas.lan:9100/metrics"},
    "printer": {},
    "prom_graph": {"range": "cpu_hist", "name": "CPU (6 h)", "unit": "%", "precision": 0},
    "prom_value": {"query": "mem", "name": "Memory", "unit": "%", "precision": 0},
    "proxmox": {"name": "pve"},
    "system": {"title": "workstation"},
    "text": {"text": "Hello wigix", "size": 28},
    "unifi": {},
    "unraid": {"name": "nas"},
    "uptime_kuma": {},
    "weather": {"name": "Paris"},
}

RENDER_OVERRIDE: dict = {}

# integration snippets shown under "Needs"
INTEG_DOC = {
    "alertmanager": {"type": "alertmanager", "url": "http://alertmanager.lan:9093"},
    "docker": {"type": "docker", "socket": "/var/run/docker.sock"},
    "adguard": {"type": "adguard", "url": "http://adguard.lan", "username": "admin", "password_file": "~/.config/wigix/adguard_pw"},
    "home_assistant": {"type": "home_assistant", "url": "http://homeassistant.local:8123", "token_file": "~/.config/wigix/ha_token"},
    "http": {"type": "http", "base_url": "http://api.lan:8080", "token_file": "~/.config/wigix/api_token"},
    "ical": {"type": "ical", "url_file": "~/.config/wigix/cal_url"},
    "mpris": {"type": "mpris"},
    "open_meteo": {"type": "open_meteo", "latitude": 48.85, "longitude": 2.35},
    "prometheus": {"type": "prometheus", "url": "http://prometheus.lan:9090", "queries": {"mem": "avg(node_memory_used_percent)"},
                   "ranges": {"cpu_hist": {"query": "avg(cpu_percent)", "hours": 6}}},
    "proxmox": {"type": "proxmox", "url": "https://pve.lan:8006", "token_id": "monitor@pve!wigix", "token_file": "~/.config/wigix/pve_token"},
    "unifi": {"type": "unifi", "url": "https://unifi.lan", "username": "wigix", "password_file": "~/.config/wigix/unifi_pw"},
    "unraid": {"type": "unraid", "url": "https://nas.lan/graphql", "api_key_file": "~/.config/wigix/unraid_key"},
    "uptime_kuma": {"type": "uptime_kuma", "url": "https://kuma.example.com", "slug": "main"},
}
# widgets whose type name is not the integration type
INTEG_ALT = {"dns_stats": "adguard"}
SAMPLE_INTEG = {"checks": None}


def default_cfg(cls):
    """Options from the field defaults that are not empty (used for widgets without a SAMPLES entry)."""
    out = {}
    for f in getattr(cls, "fields", []):
        if f.get("default") not in ("", None, [], {}) and f["name"] not in ("integration",):
            out[f["name"]] = f["default"]
    return out


def render_cell(demo, wtype, wcfg, cols, rows, at, dash_cache):
    cfg = demo.config([{"name": "g", "grid": {"cols": cols, "rows": rows, "gap": 10},
                        "widgets": [dict(wcfg, type=wtype, at=at)]}])
    return ms.render_dashboard(cfg)


def crop(img, grid_cols, grid_rows, at, gap=10):
    """Crop the widget's box out of a full-screen render (keeps the page background around it)."""
    from wigix.engine import Page
    p = Page({"grid": {"cols": grid_cols, "rows": grid_rows, "gap": gap}, "widgets": []}, {})
    return img.crop(p.box(at))


def render_widget(demo, wtype, wcfg):
    one = crop(render_cell(demo, wtype, wcfg, 4, 3, [0, 0, 1, 1], None), 4, 3, [0, 0, 1, 1])
    big = crop(render_cell(demo, wtype, wcfg, 4, 3, [0, 0, 2, 2], None), 4, 3, [0, 0, 2, 2])
    gap, pad = 16, 14
    bg = THEME["bg"]
    w = pad * 2 + one.width + gap + big.width
    h = pad * 2 + max(one.height, big.height)
    canvas = Image.new("RGB", (w, h), bg)
    canvas.paste(one, (pad, pad))
    canvas.paste(big, (pad + one.width + gap, pad))
    return canvas


def yaml_snippet(wtype, wcfg):
    item = {"type": wtype, "at": [0, 0, 1, 1], **wcfg}
    return yaml.safe_dump([item], sort_keys=False, default_flow_style=None, width=100).rstrip()


def fmt_default(v):
    if v in ("", None):
        return "-"
    return "`" + (yaml.safe_dump(v, default_flow_style=True, width=1000).strip().removesuffix("\n...").strip()
                  if not isinstance(v, str) else v) + "`"


def esc(s):
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def main():
    widgets.load_plugins()
    OUT.mkdir(parents=True, exist_ok=True)
    demo = ms.Demo()
    RENDER_OVERRIDE["node"] = {"url": demo.http.url + "/metrics"}      # what is drawn; the docs keep the example URL
    SAMPLES["checks"] = {"title": "Services", "checks": demo.checks()}
    skipped, done = [], []
    try:
        for wtype in sorted(widgets.REGISTRY):
            cls = widgets.REGISTRY[wtype]
            wcfg = SAMPLES.get(wtype)
            if wcfg is None:
                wcfg = default_cfg(cls)
            try:
                set_theme(None)
                render_widget(demo, wtype, {**wcfg, **RENDER_OVERRIDE.get(wtype, {})}).save(OUT / f"{wtype}.png")
                done.append(wtype)
            except Exception as e:                                  # noqa: BLE001
                skipped.append(f"{wtype}: {type(e).__name__}: {e}")
        lines = ["# Widget gallery", "",
                 "Every built-in widget, drawn from fake data by `tools/gallery.py` (regenerate it with "
                 "`.venv/bin/python tools/gallery.py`). Each picture shows the widget in a 1x1 cell (left) and a 2x2 cell "
                 "(right) of a 4x3 grid, so you can see how it adapts. Widgets that need a service show a "
                 "placeholder card when it is missing.", "",
                 "Jump to: " + ", ".join(f"[{t}](#{t.replace('_', '-')})" for t in done), ""]
        for wtype in done:
            cls = widgets.REGISTRY[wtype]
            doc = (cls.__doc__ or "").strip().split("\n\n")[0].replace("\n", " ")
            doc = doc.replace("``", "`").replace("tower.lan", "nas.lan")
            wcfg = SAMPLES.get(wtype) or default_cfg(cls)
            lines += [f"## {wtype}", "", doc, "", f"![{wtype}](img/gallery/{wtype}.png)", ""]
            itype = getattr(cls, "integration_type", "") or INTEG_ALT.get(wtype, "")
            if itype:
                snip = INTEG_DOC.get(itype)
                lines += [f"Needs {'an' if itype[0] in 'aeiou' else 'a'} `{itype}` integration:", "", "```yaml", "integrations:",
                          *("  " + ln for ln in yaml.safe_dump({"main": snip} if snip else {"main": {"type": itype}},
                                                               sort_keys=False, default_flow_style=None, width=100).rstrip().split("\n")),
                          "```", ""]
            lines += ["```yaml", yaml_snippet(wtype, wcfg), "```", ""]
            fields = getattr(cls, "fields", [])
            if fields:
                lines += ["| option | type | default | description |", "|---|---|---|---|"]
                for f in fields:
                    lines.append(f"| `{f['name']}` | {f.get('type', 'string')} | {esc(fmt_default(f.get('default')))} | {esc(f.get('help'))} |")
                lines.append("")
        (ROOT / "docs" / "gallery.md").write_text("\n".join(lines).rstrip() + "\n")
        print("wrote docs/gallery.md with", len(done), "widgets")
    finally:
        demo.stop()
    for s in skipped:
        print("skipped", s)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
