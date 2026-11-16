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
    for mod in ("system", "clock", "text", "node", "ha", "alerts", "proxmox", "unraid", "button"):
        importlib.import_module(f"{__name__}.{mod}")
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
