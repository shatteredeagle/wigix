# SPDX-License-Identifier: GPL-3.0-only
"""Normalised view of the user-facing display settings (screensaver, page rotation, splash, theme presets).

The config file accepts several spellings for the same idea (``page_seconds`` and ``rotate:``, ``idle: false``
and ``idle: {enabled: false}``); everything that reads these settings goes through here, so the daemon, the
editor and the tray agree.

    rotate: {enabled: true, seconds: 20, pause_after_touch: 30}     # or the old `page_seconds: 20`
    idle: {enabled: true, after: 300, dim_to: 15, off_after: 1800, screensaver: {...} | false}
    splash: {seconds: 1.5} | false
    page_dots: true
    pages: [{name: Home, seconds: 8, rotate: true}]                 # per-page dwell time / opt out of rotation
"""
from __future__ import annotations

PRESETS: dict[str, dict[str, str]] = {
    "midnight": {"bg": "#0a0c10", "card": "#1c2028", "text": "#e8ecf2", "dim": "#8088a0", "ok": "#4ad682",
                 "warn": "#f5be3c", "crit": "#f05454", "accent": "#569cff"},
    "amoled": {"bg": "#000000", "card": "#11141a", "text": "#f2f4f8", "dim": "#7a8294", "ok": "#3ddc84",
               "warn": "#ffc233", "crit": "#ff5a5a", "accent": "#4da3ff"},
    "nord": {"bg": "#2e3440", "card": "#3b4252", "text": "#eceff4", "dim": "#8f9bb3", "ok": "#a3be8c",
             "warn": "#ebcb8b", "crit": "#bf616a", "accent": "#88c0d0"},
    "dracula": {"bg": "#1e1f29", "card": "#2b2d3b", "text": "#f8f8f2", "dim": "#8a8fb0", "ok": "#50fa7b",
                "warn": "#f1fa8c", "crit": "#ff5555", "accent": "#bd93f9"},
    "solarized": {"bg": "#002b36", "card": "#073642", "text": "#eee8d5", "dim": "#839496", "ok": "#859900",
                  "warn": "#b58900", "crit": "#dc322f", "accent": "#268bd2"},
    "forest": {"bg": "#0e1512", "card": "#18241f", "text": "#e6efe9", "dim": "#7f9488", "ok": "#5fd38d",
               "warn": "#e6b84a", "crit": "#e8604c", "accent": "#4fc3a1"},
    "sunset": {"bg": "#16101a", "card": "#251a2c", "text": "#f6ebf0", "dim": "#9a8499", "ok": "#6fd49a",
               "warn": "#f7b955", "crit": "#f0586b", "accent": "#ff8a5c"},
    "daylight": {"bg": "#eef1f6", "card": "#ffffff", "text": "#1b2029", "dim": "#68718a", "ok": "#1f9d57",
                 "warn": "#b7791f", "crit": "#d23b3b", "accent": "#2f6fe4"},
}
DEFAULT_PRESET = "midnight"


def resolve_theme(theme) -> dict:
    """A ``theme:`` block -> plain {key: color} dict. ``preset:`` (or a bare preset name) supplies the base and
    explicit colors win over it."""
    if isinstance(theme, str):
        theme = {"preset": theme}
    if not isinstance(theme, dict):
        return {}
    out = dict(PRESETS.get(str(theme.get("preset", "")).lower(), {}))
    out.update({k: v for k, v in theme.items() if k != "preset"})
    return out


def _mapping(v) -> dict:
    return v if isinstance(v, dict) else {}


def rotate_settings(cfg: dict, override: bool | None = None) -> dict:
    """{enabled, seconds, pause_after_touch}. ``override`` is the tray's runtime on/off switch."""
    r = cfg.get("rotate")
    legacy = float(cfg.get("page_seconds") or 0)
    if r is False:
        enabled, body = False, {}
    elif r is True:
        enabled, body = True, {}
    else:
        body = _mapping(r)
        enabled = bool(body.get("enabled", bool(body) or legacy > 0))
    secs = float(body.get("seconds") or legacy or 15)
    out = {"enabled": enabled, "seconds": max(1.0, secs),
           "pause_after_touch": max(0.0, float(body.get("pause_after_touch", 30)))}
    if override is not None:
        out["enabled"] = override
    return out


def page_dwell(page_cfg: dict, default: float) -> float:
    try:
        return max(1.0, float(page_cfg.get("seconds") or default))
    except (TypeError, ValueError):
        return default


def in_rotation(page_cfg: dict) -> bool:
    return page_cfg.get("rotate", True) is not False


def idle_settings(cfg: dict, override: bool | None = None) -> dict | None:
    """The idle block (dim + screensaver) as a dict, or None when it is switched off.

    ``override`` is the tray's runtime switch: False turns idle handling off, True turns it on even if the
    config disables it (with default timings)."""
    if override is False:
        return None
    v = cfg.get("idle")
    if v is True or (override and not v):
        return {}
    if not v or not isinstance(v, dict) or v.get("enabled") is False:
        return {} if override else None
    return v


def screensaver_config(cfg: dict, override: bool | None = None) -> dict | None:
    """The screensaver widget config, or None for dimming only (``screensaver: false``) or no idle handling."""
    idle = idle_settings(cfg, override)
    if idle is None:
        return None
    sc = idle.get("screensaver", {"type": "screensaver"})
    if sc is False or (isinstance(sc, dict) and sc.get("enabled") is False):
        return None
    return sc if isinstance(sc, dict) else {"type": "screensaver"}


def splash_seconds(cfg: dict) -> float | None:
    s = cfg.get("splash", {})
    if s is False or (isinstance(s, dict) and s.get("enabled") is False):
        return None
    return float(s.get("seconds", 1.5)) if isinstance(s, dict) else 1.5
