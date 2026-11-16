"""``docker_containers``: container states from a ``docker`` integration."""
from __future__ import annotations

import fnmatch

from . import IntegrationWidget, widget
from .http import INTEG, dot_rows, header_note
from ..render import THEME

# Container states that are not a problem by themselves; everything else is shown amber or red.
STATE_COLOR = {"running": "ok", "exited": "crit", "dead": "crit", "paused": "warn", "restarting": "warn", "created": "warn"}


def container_color(c: dict):
    """running green, exited/dead/unhealthy red, paused/restarting/starting amber, anything else dim."""
    status = c.get("status", "").lower()
    if "(unhealthy)" in status:
        return THEME["crit"]
    if c.get("state") == "running" and "starting)" in status:
        return THEME["warn"]
    return THEME[STATE_COLOR.get(c.get("state"), "dim")]


def note(c: dict) -> str:
    """Short reason shown beside a container that is not plainly healthy."""
    status = c.get("status", "").lower()
    if "(unhealthy)" in status:
        return "unhealthy"
    if c.get("state") == "running":
        return "starting" if "starting)" in status else ""
    return c.get("state", "")


@widget("docker_containers")
class DockerContainers(IntegrationWidget):
    """Docker container list with state dots and running/problem counts. Needs a ``docker`` integration."""
    integration_type = "docker"
    interval = 3600.0
    fields = [{"name": "title", "type": "string", "default": "Containers"}, INTEG,
              {"name": "only_problems", "type": "bool", "default": False, "help": "hide healthy running containers"},
              {"name": "names", "type": "lines", "default": [], "help": "only these containers (wildcards ok, e.g. media-*)"},
              {"name": "max_rows", "type": "int", "default": 12}]

    def draw(self, cv, box):
        d = self.guard(cv, box)
        if d is None:
            return
        pats = self.cfg.get("names") or []
        cs = [c for c in d if not pats or any(fnmatch.fnmatch(c["name"], p) for p in pats)]
        bad = [c for c in cs if container_color(c) != THEME["ok"]]
        inner = cv.card(box, self.title or "Containers")
        x0, y0, x1, y1 = inner
        running = sum(1 for c in cs if c["state"] == "running")
        header_note(cv, box, self.title or "Containers",
                    [f"{running}/{len(cs)} up · {len(bad)} bad", f"{len(bad)} bad"] if bad else [f"{running}/{len(cs)} up"],
                    THEME["warn"] if bad else THEME["ok"] if cs else THEME["dim"])
        shown = bad if self.cfg.get("only_problems") else cs
        if not shown:
            cv.text(((x0 + x1) // 2, (y0 + y1) // 2), "all healthy" if cs else "no containers", size=20,
                    color=THEME["ok"] if cs else THEME["dim"], anchor="mm")
            return
        # Problems first so they survive the max_rows / "+N more" cut.
        shown = sorted(shown, key=lambda c: container_color(c) == THEME["ok"])[:int(self.cfg.get("max_rows") or 12)]
        dot_rows(cv, inner, [(container_color(c), c["name"], note(c), None if container_color(c) == THEME["ok"] else container_color(c))
                             for c in shown])
