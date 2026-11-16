"""Generic JSON-over-HTTP widgets (``json_value``, ``json_list``) for the ``http`` integration.

Common config: ``integration`` (name; optional when there is exactly one ``http`` integration),
``request`` (which polled URL path to read; defaults to the integration's first), ``path`` (where to look
in the response, e.g. ``data.items[0].name``; see integrations/http.py).
Value coloring: ``color_rules: [{above: 80, color: "#f05454"}, {below: 20, color: "#569cff"}]`` for numbers,
``state_colors: {ok: "#4ad682"}`` for text.

The helpers at the top are shared by the other monitoring widgets (prometheus, docker, ...).
"""
from __future__ import annotations

from . import IntegrationWidget, widget
from ..integrations.http import json_path
from ..render import THEME, fmt_number, font, parse_color

NAME = {"name": "name", "type": "string", "default": "", "help": "card title"}
INTEG = {"name": "integration", "type": "string", "default": "", "help": "integration name (optional if only one)"}
UNIT = {"name": "unit", "type": "string", "default": ""}
PRECISION = {"name": "precision", "type": "int", "default": "", "help": "decimal places"}
RULES = {"name": "color_rules", "type": "yaml", "default": [], "help": 'e.g. [{"above": 80, "color": "#f05454"}]'}


def as_float(v):
    """Numbers and numeric strings -> float; everything else (None, bools, text) -> None."""
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def format_value(v, unit="", precision=None) -> str:
    """Tidy display text: numbers via fmt_number, other values as-is, None as an em dash."""
    if v is None:
        return "—"
    f = as_float(v)
    s = fmt_number(f, precision) if f is not None else str(v)
    return s + (unit if unit in ("%", "°C", "°F") else f" {unit}" if unit else "")


def rule_color(cfg: dict, v):
    """Color for a value from the widget's ``color_rules`` (numbers) or ``state_colors`` (text)."""
    f = as_float(v)
    if f is not None:
        for rule in cfg.get("color_rules") or []:
            if ("above" in rule and f > rule["above"]) or ("below" in rule and f < rule["below"]):
                return parse_color(rule.get("color"), THEME["text"])
        return THEME["text"]
    c = (cfg.get("state_colors") or {}).get(str(v))
    return parse_color(c, THEME["text"]) if c else THEME["text"]


def fit_text(text, size, max_width, min_size=14):
    """Shrink to ``min_size``, then cut with an ellipsis, so long text stays readable instead of tiny."""
    text = str(text)
    while size > min_size and font(size).getlength(text) > max_width:
        size -= 2
    if font(size).getlength(text) > max_width:
        while len(text) > 1 and font(size).getlength(text + "…") > max_width:
            text = text[:-1]
        text += "…"
    return text, size


def header_note(cv, box, title, options, color):
    """Right-aligned note on the title line: the first of ``options`` (longest first) that fits beside the title."""
    x_right = box[2] - 14
    room = x_right - (box[0] + 14 + (font(18).getlength(title) + 12 if title else 0))
    for text in options:
        if font(18).getlength(text) <= room or text == options[-1]:
            cv.text((x_right, box[1] + 8), text, size=18, color=color, anchor="ra")
            return


def draw_big_value(cv, box, title, text, color):
    """A card with one large centered value (shrinks to fit)."""
    x0, y0, x1, y1 = cv.card(box, title)
    cv.text(((x0 + x1) // 2, (y0 + y1) // 2), text, size=54, bold=True, color=color, anchor="mm", max_width=x1 - x0)


def dot_rows(cv, inner, rows, row_h=26, size=18):
    """Rows of ``(dot_color, name, right_text, name_color)``: a status dot, a name and a dim right-aligned note.

    One column, or two when the card is wide and the rows do not fit; the last slot becomes "+N more"
    when even that is not enough.
    """
    x0, y0, x1, y1 = inner
    per = max(1, (y1 - y0) // row_h)
    cols = 2 if len(rows) > per and x1 - x0 >= 400 else 1
    cap = cols * per
    shown = rows if len(rows) <= cap else rows[:cap - 1]
    colw = (x1 - x0) // cols
    for i, (dot, name, note, color) in enumerate(shown):
        col, r = divmod(i, per)
        x, y = x0 + col * colw, y0 + r * row_h
        cv.d.ellipse((x, y + (row_h - 12) // 2, x + 12, y + (row_h - 12) // 2 + 12), fill=dot)
        room = colw - 22 - (10 if cols > 1 else 0)
        right = font(size - 2).getlength(note) + 12 if note else 0
        if note and font(size).getlength(name) + right > room:
            note, right = "", 0                              # the name matters more than the note on narrow cards
        text, tsize = fit_text(name, size, room - right)
        cv.text((x + 22, y + 2), text, size=tsize, color=color or THEME["text"])
        if note:
            cv.text((x + colw - (10 if cols > 1 else 0), y + 4), note, size=size - 2, color=THEME["dim"], anchor="ra")
    if len(rows) > cap:
        col, r = divmod(cap - 1, per)
        cv.text((x0 + col * colw + 22, y0 + r * row_h + 2), f"+{len(rows) - cap + 1} more", size=size, color=THEME["dim"])


@widget("json_value")
class JsonValue(IntegrationWidget):
    """One value from a JSON API response as a big number or text. Needs an ``http`` integration."""
    integration_type = "http"
    interval = 3600.0
    fields = [NAME, INTEG,
              {"name": "request", "type": "string", "default": "", "help": "polled URL path, e.g. /v1/stats"},
              {"name": "path", "type": "string", "default": "", "help": "where in the JSON, e.g. data.items[0].name"},
              UNIT, PRECISION, RULES,
              {"name": "state_colors", "type": "yaml", "default": {}, "help": 'text value -> color, e.g. {"ok": "#4ad682"}'}]

    def draw(self, cv, box):
        if self.guard(cv, box) is None:
            return
        v = json_path(self.integ.get(self.cfg.get("request")), self.cfg.get("path"))
        if isinstance(v, (dict, list)):                    # not a single value: show how many entries instead
            v = len(v)
        title = self.cfg.get("name") or self.cfg.get("path") or self.cfg.get("request")
        draw_big_value(cv, box, title, format_value(v, self.cfg.get("unit", ""), self.cfg.get("precision")),
                       THEME["dim"] if v is None else rule_color(self.cfg, v))


@widget("json_list")
class JsonList(IntegrationWidget):
    """Rows from a JSON list (or object): a name on the left, a value on the right. Needs an ``http`` integration."""
    integration_type = "http"
    interval = 3600.0
    fields = [{"name": "title", "type": "string", "default": ""}, INTEG,
              {"name": "request", "type": "string", "default": "", "help": "polled URL path, e.g. /v1/queue"},
              {"name": "path", "type": "string", "default": "", "help": "path to the list (or object) in the JSON"},
              {"name": "name_path", "type": "string", "default": "name", "help": "path to the label inside each item"},
              {"name": "value_path", "type": "string", "default": "value", "help": "path to the value inside each item"},
              {"name": "max_rows", "type": "int", "default": 8}, UNIT, PRECISION, RULES]

    def rows(self):
        """[(label, value)] from the configured list; an object yields (key, value) pairs."""
        items = json_path(self.integ.get(self.cfg.get("request")), self.cfg.get("path"))
        if isinstance(items, dict):
            items = list(items.items())
        elif isinstance(items, list):
            items = [(None, it) for it in items]
        else:
            return []
        np_, vp = self.cfg.get("name_path") or "name", self.cfg.get("value_path") or "value"
        out = []
        for key, it in items:
            if isinstance(it, (dict, list)):
                label, value = json_path(it, np_), json_path(it, vp)
            else:
                label, value = None, it                       # a plain value: the label can only be its key
            if label is None:
                label = key if key is not None else value if not isinstance(it, (dict, list)) else "?"
            out.append((str(label), value))
        return out

    def draw(self, cv, box):
        if self.guard(cv, box) is None:
            return
        x0, y0, x1, y1 = cv.card(box, self.title)
        rows = self.rows()
        if not rows:
            cv.text(((x0 + x1) // 2, (y0 + y1) // 2), "empty", size=20, color=THEME["dim"], anchor="mm")
            return
        h = 28
        limit = max(1, min(int(self.cfg.get("max_rows") or 8), (y1 - y0 + 4) // h))
        shown = rows if len(rows) <= limit else rows[:limit - 1]
        unit, prec = self.cfg.get("unit", ""), self.cfg.get("precision")
        for i, (label, v) in enumerate(shown):
            y = y0 + i * h
            text = format_value(v, unit, prec)
            label, lsize = fit_text(label, 20, x1 - x0 - font(20).getlength(text) - 12)
            cv.text((x0, y), label, size=lsize, color=THEME["dim"])
            cv.text((x1, y), text, size=20, color=rule_color(self.cfg, v), anchor="ra")
        if len(rows) > len(shown):
            cv.text((x0, y0 + len(shown) * h), f"+{len(rows) - len(shown)} more", size=18, color=THEME["dim"])
