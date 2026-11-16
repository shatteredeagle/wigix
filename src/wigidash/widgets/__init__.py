"""Widget plugin API.

A widget is a class registered with ``@widget("name")``. The engine calls ``update()`` on a worker
thread every ``interval`` seconds (do network/slow work there, store results on ``self``) and
``draw()`` on the render thread (draw only, never block).
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import pathlib
import pkgutil

log = logging.getLogger("wigidash")
REGISTRY: dict[str, type] = {}


def widget(name: str):
    def deco(cls):
        cls.type_name = name
        REGISTRY[name] = cls
        return cls
    return deco


class Widget:
    interval = 2.0  # seconds between update() calls
    fields: list = []  # config schema for the editor: [{name, type: string|int|float|bool|color|yaml, default, help}]

    def __init__(self, cfg: dict, ctx: dict):
        self.cfg = cfg
        self.ctx = ctx          # shared: global config, secrets helpers
        self.error: str | None = None

    def update(self) -> None:
        pass

    def draw(self, cv, box) -> None:
        raise NotImplementedError

    def draw_full(self, cv, box) -> None:
        """Fullscreen view (``on_tap: fullscreen``). Defaults to the normal drawing at the bigger size."""
        self.draw(cv, box)

    def on_tap(self, x: int, y: int) -> bool:
        """Called when the widget is tapped (screen coords). Return True if handled; otherwise the
        widget's YAML ``on_tap`` action (see Dashboard.run_action) runs."""
        return False

    @property
    def title(self):
        return self.cfg.get("title")


def load_plugins(extra_dirs=()):
    """Import built-ins, third-party entry points (group ``wigidash.widgets``), and *.py in extra_dirs."""
    for m in pkgutil.iter_modules(__path__):          # every module in this package registers its widgets
        importlib.import_module(f"{__name__}.{m.name}")
    for ep in importlib.metadata.entry_points(group="wigidash.widgets"):
        try:
            ep.load()
        except Exception:
            log.exception("failed to load widget plugin %s", ep.name)
    for d in extra_dirs:
        for path in sorted(pathlib.Path(d).expanduser().glob("*.py")):
            spec = importlib.util.spec_from_file_location(f"wigidash_user_{path.stem}", path)
            m = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(m)
            except Exception:
                log.exception("failed to load %s", path)


class IntegrationWidget(Widget):
    """Base for widgets that read an integration. Set ``integration_type``; use ``self.integ`` and ``self.guard``.

    The integration is chosen by the widget's ``integration:`` option, or is the only one of that type.
    """
    integration_type = ""

    @property
    def integ(self):
        return self.ctx["integrations"].get(self.cfg.get("integration") or None, self.integration_type)

    def placeholder(self, cv, box, text, color=None):
        from ..render import THEME
        x0, y0, x1, y1 = cv.card(box, self.title or self.cfg.get("name"))
        cv.text(((x0 + x1) // 2, (y0 + y1) // 2), text, size=20, color=color or THEME["dim"], anchor="mm", max_width=x1 - x0)

    def guard(self, cv, box):
        """Return the integration's ``data`` once it has some, else draw a status card and return None."""
        from ..render import THEME
        try:
            integ = self.integ
        except KeyError:
            self.placeholder(cv, box, "no integration", THEME["crit"])
            return None
        if integ.data is None:
            self.placeholder(cv, box, "offline" if integ.error else "loading…", THEME["crit"] if integ.error else None)
            return None
        return integ.data
