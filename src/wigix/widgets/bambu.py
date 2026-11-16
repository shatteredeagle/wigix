import os

from . import IntegrationWidget, widget
from ..render import THEME

# gcode_state -> (label, theme color key)
STATES = {"RUNNING": ("printing", "ok"), "PREPARE": ("preparing", "ok"), "PAUSE": ("paused", "warn"),
          "FAILED": ("failed", "crit"), "IDLE": ("idle", "dim"), "FINISH": ("finished", "accent")}
HEATING_MARGIN = 3.0            # degrees below target that still count as "heating up"


def fmt_remaining(minutes) -> str:
    """Minutes -> "1h 12m" / "37m" / "2d 3h"; "" when unknown."""
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return ""
    if m < 0:
        return ""
    if m >= 1440:
        return f"{m // 1440}d {m % 1440 // 60}h"
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _temp(cur, target) -> tuple[str, str]:
    """("215/220", color key): text for one heater, amber while it is still heating toward its target."""
    cur, target = _num(cur), _num(target)
    if cur is None:
        return "--", "dim"
    if not target:
        return f"{cur:.0f}", "text"
    return f"{cur:.0f}/{target:.0f}", "warn" if cur < target - HEATING_MARGIN else "text"


def _error_text(code) -> str:
    """print_error is a 32-bit HMS-style code; show it as 0300_4001 so it can be looked up."""
    try:
        n = int(code)
    except (TypeError, ValueError):
        return ""
    return f"error {n >> 16 & 0xFFFF:04X}_{n & 0xFFFF:04X}" if n else ""


def _job_name(d) -> str:
    name = d.get("subtask_name") or os.path.basename(str(d.get("gcode_file") or ""))
    return os.path.splitext(name)[0] if name.endswith((".3mf", ".gcode")) else name


@widget("printer")
class Printer(IntegrationWidget):
    """Bambu Lab 3D printer: state, job, progress, time left, layers and temperatures. Needs a ``bambu`` integration."""
    integration_type = "bambu"
    interval = 3600.0
    fields = [{"name": "name", "type": "string", "default": "Printer", "help": "card title"},
              {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one)"}]

    def draw(self, cv, box):
        self._paint(cv, box)

    def _paint(self, cv, box):
        try:
            integ = self.integ
        except KeyError:
            self.placeholder(cv, box, "no integration", THEME["crit"])
            return
        d = integ.data
        if d is None or not integ.connected:                      # no state yet, or the printer went away
            self.placeholder(cv, box, "offline" if integ.error or d else "loading…", THEME["crit"] if integ.error else None)
            return
        x0, y0, x1, y1 = cv.card(box, self.cfg.get("name", "Printer"))
        h = box[3] - box[1]
        big = h >= 260                                            # tall cards list each heater on its own row
        k = 1.0 if h < 260 else 1.35 if h < 400 else 1.7          # fonts and spacing scale with the card height
        corner = 56 if h >= 500 else 0                            # fullscreen: leave room for the close button
        px = lambda n: int(n * k)
        state = str(d.get("gcode_state") or "").upper()
        label, ckey = STATES.get(state, (state.lower() or "unknown", "dim"))
        cv.text((x1 - corner, box[1] + 8), label, size=18, color=THEME[ckey], anchor="ra")

        err = _error_text(d.get("print_error"))
        y = y0
        job = _job_name(d)
        if job:
            cv.text((x0, y), job, size=px(17), color=THEME["dim"], max_width=x1 - x0)
        y += px(22)
        if state != "IDLE":
            pct = _num(d.get("mc_percent")) or 0
            cv.text((x0, y), f"{pct:.0f}%", size=px(38), bold=True, color=THEME[ckey] if ckey != "dim" else THEME["text"])
            left = fmt_remaining(d.get("mc_remaining_time")) if state in ("RUNNING", "PAUSE", "PREPARE") else ""
            if left:
                cv.text((x1, y + px(22)), left, size=px(20), anchor="ra")
            y += px(48)
            cv.bar((x0, y, x1, y + px(10)), pct / 100, color=THEME[ckey] if ckey != "dim" else None)
            y += px(18)
            layer, total = d.get("layer_num"), d.get("total_layer_num")
            if err and not big:                                    # compact card: the error replaces the layer line
                cv.text((x0, y), err, size=px(17), color=THEME["crit"], max_width=x1 - x0)
            elif total:
                cv.text((x0, y), f"layer {layer or 0}/{total}", size=px(17), color=THEME["dim"])
            y += px(26)
        else:
            if err and not big:
                cv.text((x0, y), err, size=px(17), color=THEME["crit"], max_width=x1 - x0)
            y += px(10)

        # Heaters: nozzle and bed (and chamber when the printer reports one and there is room).
        rows = [("nozzle", d.get("nozzle_temper"), d.get("nozzle_target_temper")),
                ("bed", d.get("bed_temper"), d.get("bed_target_temper"))]
        if big and _num(d.get("chamber_temper")) is not None:
            rows.append(("chamber", d.get("chamber_temper"), None))
        if big:                                                    # one row per heater
            for name, cur, tgt in rows:
                txt, c = _temp(cur, tgt)
                cv.text((x0, y), name, size=px(17), color=THEME["dim"])
                cv.text((x1, y), f"{txt}°", size=px(18), color=THEME[c], anchor="ra")
                y += px(24)
        else:                                                      # compact: both on one line, anchored to the bottom
            y = max(y, y1 - px(20))
            half = (x1 - x0) // 2
            for i, (name, cur, tgt) in enumerate(rows):
                txt, c = _temp(cur, tgt)
                cv.text((x0 + i * half, y), f"{name[0].upper()} {txt}°", size=px(16), color=THEME[c], max_width=half - 4)
            y += px(24)
        if err and big:
            cv.text((x0, y1 - px(20)), err, size=px(17), color=THEME["crit"], max_width=x1 - x0)
