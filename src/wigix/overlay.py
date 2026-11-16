"""Things layered over the pages: the startup splash, the idle screensaver (with dimming) and popups
(shown for N seconds, usually fired by a Home Assistant trigger such as a doorbell).

Config (all optional)::

    splash: {seconds: 1.5}          # or `splash: false`
    idle:
      after: 300                    # seconds without a touch
      dim_to: 10                    # brightness % while idle (omit to keep brightness)
      off_after: 1800               # brightness 0 after this long idle (omit to never)
      screensaver: {type: screensaver, text: wigix}     # or `false` to only dim
    triggers:
      - name: doorbell
        when: {entity: binary_sensor.doorbell, state: "on"}   # also: state_in, above, below, integration
        show: {type: camera, ha_camera: camera.front_door}    # a widget shown fullscreen as a popup
        seconds: 20
        action: {ha_service: light.turn_on, entity: light.porch}   # optional, any action
        cooldown: 10
"""
from __future__ import annotations

import logging
import threading
import time
from importlib import metadata

from PIL import Image, ImageDraw

from . import widgets
from .device import H, W
from .render import THEME, Canvas, theme_override

log = logging.getLogger("wigix")


def _version():
    try:
        return metadata.version("wigix")
    except metadata.PackageNotFoundError:
        return "dev"


class Overlays:
    def __init__(self, dash):
        self.dash = dash
        self.last_touch = time.time()
        self.idle = False
        self.brightness_override: int | None = None
        self.manual_off = False                       # display paused from the tray / control API
        self.manual_brightness: int | None = None     # brightness set from the tray / control API (until reload)
        self._splash_until = 0.0
        self._firmware = ""
        self._screensaver = None            # (cfg, widget) cache
        self._popup = None                  # (widget, until)
        self._trigger_state: dict[str, bool] = {}
        self._trigger_fired: dict[str, float] = {}

    # --- events from the device / touch ----------------------------------------------------
    def device_connected(self, info: dict | None = None):
        cfg = self.dash.cfg.get("splash", {})
        if cfg is False:
            return
        secs = float((cfg or {}).get("seconds", 1.5)) if isinstance(cfg, dict) or cfg in (None, True) else 1.5
        self._firmware = (info or {}).get("firmware", "")
        self._splash_until = time.time() + secs

    def on_touch(self, down: bool) -> bool:
        """Record activity. Returns True when this touch only woke the screen (the caller must swallow it)."""
        self.last_touch = time.time()
        woke = self.idle
        if woke:
            self.idle = False
            self.brightness_override = None
            self.dash._wake.set()
        return woke

    # --- popups ----------------------------------------------------------------------------
    def show_popup(self, wcfg: dict, seconds: float = 15.0):
        cls = widgets.REGISTRY.get(wcfg.get("type"))
        if cls is None:
            log.warning("popup: unknown widget type %r", wcfg.get("type"))
            return
        self.close_popup()
        w = cls(wcfg, self.dash.ctx)
        w._halt = False
        threading.Thread(target=self.dash._worker, args=(w,), daemon=True).start()
        self._popup = (w, time.time() + float(seconds))
        self.wake()
        self.dash._wake.set()

    def close_popup(self):
        if self._popup:
            self._popup[0]._halt = True
        self._popup = None
        self.dash._wake.set()

    def popup_active(self) -> bool:
        return self._popup is not None

    def wake(self):
        """Leave idle without a touch (a trigger fired)."""
        self.last_touch = time.time()
        if self.idle:
            self.idle, self.brightness_override = False, None

    # --- per-loop update -------------------------------------------------------------------
    def update(self, now: float | None = None):
        now = now or time.time()
        if self._popup and now > self._popup[1]:
            self.close_popup()
        self._update_idle(now)
        self._update_triggers(now)

    def _update_idle(self, now):
        if self.manual_off:                       # a paused display stays off, whatever idle/touch say
            self.idle, self.brightness_override = False, 0
            return
        cfg = self.dash.cfg.get("idle")
        if not cfg:
            self.idle, self.brightness_override = False, None
            return
        after = float(cfg.get("after", 300))
        quiet = now - self.last_touch
        if quiet > after:
            self.idle = True
            off = cfg.get("off_after")
            if off and quiet > float(off):
                self.brightness_override = 0
            elif "dim_to" in cfg:
                self.brightness_override = int(cfg["dim_to"])
        else:
            self.idle, self.brightness_override = False, None

    def _update_triggers(self, now):
        for i, t in enumerate(self.dash.cfg.get("triggers") or []):
            key = t.get("name") or str(i)
            try:
                matched = self._matches(t.get("when") or {})
            except KeyError:
                continue
            if matched is None:                              # integration not connected: no verdict
                continue
            was = self._trigger_state.get(key, False)
            self._trigger_state[key] = matched
            if matched and not was and now - self._trigger_fired.get(key, 0) >= float(t.get("cooldown", 10)):
                self._trigger_fired[key] = now
                log.info("trigger %s fired", key)
                if t.get("wake", True):
                    self.wake()
                if t.get("show"):
                    self.show_popup(t["show"], float(t.get("seconds", 15)))
                if t.get("action"):
                    try:
                        self.dash.run_action(t["action"])
                    except Exception:
                        log.exception("trigger action failed")

    def _matches(self, when: dict):
        ha = self.dash.integrations.get(when.get("integration"), "home_assistant")
        if not ha.connected:
            return None
        s = ha.state(when["entity"])
        if s is None:
            return False
        st = s["state"]
        if "state" in when:
            return st == str(when["state"])
        if "state_in" in when:
            return st in [str(x) for x in when["state_in"]]
        try:
            v = float(st)
        except ValueError:
            return False
        if "above" in when:
            return v > float(when["above"])
        if "below" in when:
            return v < float(when["below"])
        return False

    # --- frames ----------------------------------------------------------------------------
    def fps(self) -> float | None:
        if self._popup and self._popup[0].fps:
            return float(self._popup[0].fps)
        if self.idle and self._screensaver_cfg() is not None:
            return 8.0
        return None

    def _screensaver_cfg(self):
        cfg = (self.dash.cfg.get("idle") or {}).get("screensaver", {"type": "screensaver"})
        return None if cfg is False else cfg

    def frame(self) -> Image.Image | None:
        """The overlay image to show instead of the page, or None."""
        if self.manual_off:
            return Image.new("RGB", (W, H), (0, 0, 0))
        if self._popup:
            return self._render_widget(self._popup[0], close_button=True)
        if time.time() < self._splash_until:
            return self._splash()
        if self.idle:
            cfg = self._screensaver_cfg()
            if cfg is not None:
                if self._screensaver is None or self._screensaver[0] != cfg:
                    cls = widgets.REGISTRY.get(cfg.get("type", "screensaver"))
                    self._screensaver = (dict(cfg), cls(cfg, self.dash.ctx)) if cls else None
                if self._screensaver:
                    return self._render_widget(self._screensaver[1], close_button=False)
        return None

    @staticmethod
    def _render_widget(w, close_button):
        img = Image.new("RGB", (W, H), THEME["bg"])
        cv = Canvas(ImageDraw.Draw(img), img)
        box = (10, 10, W - 10, H - 10)
        w.box = box
        try:
            with theme_override(w.cfg.get("theme")):
                w.draw_full(cv, box)
        except Exception:
            log.exception("overlay draw failed: %s", w.type_name)
        if close_button:
            cx, cy, r = W - 40, 40, 22
            cv.d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(0, 0, 0), outline=THEME["dim"], width=2)
            cv.d.line((cx - 8, cy - 8, cx + 8, cy + 8), fill=THEME["text"], width=3)
            cv.d.line((cx - 8, cy + 8, cx + 8, cy - 8), fill=THEME["text"], width=3)
        return img

    def _splash(self):
        img = Image.new("RGB", (W, H), (0, 0, 0))
        cv = Canvas(ImageDraw.Draw(img), img)
        cv.text((W // 2, H // 2 - 20), "wigix", size=120, bold=True, color=THEME["accent"], anchor="mm")
        sub = f"v{_version()}" + (f"   ·   {self._firmware}" if self._firmware else "")
        cv.text((W // 2, H // 2 + 70), sub, size=22, color=THEME["dim"], anchor="mm")
        return img
