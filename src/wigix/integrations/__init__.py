# SPDX-License-Identifier: GPL-3.0-only
"""Integrations: named, shared connections to external systems (Home Assistant, ...).

Configured once under ``integrations:`` in the YAML and referenced by widgets with
``integration: <name>``. Plugins can register their own with ``@integration("type")``.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil

log = logging.getLogger("wigix")
REGISTRY: dict[str, type] = {}


def integration(name: str):
    def deco(cls):
        cls.type_name = name
        REGISTRY[name] = cls
        return cls
    return deco


class Integration:
    """Base class. start() must return quickly (run connections on a thread); stop() must be safe to repeat."""

    def __init__(self, name: str, cfg: dict):
        self.name, self.cfg = name, cfg

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def status(self) -> dict:
        return {"connected": True}


def load_builtin():
    for m in pkgutil.iter_modules(__path__):          # every module in this package registers its integrations
        importlib.import_module(f"{__name__}.{m.name}")


def normalize(cfg: dict) -> dict[str, dict]:
    """Return {name: cfg}. Accepts the legacy top-level ``home_assistant:`` block as integration ``ha``."""
    out = {k: dict(v) for k, v in (cfg.get("integrations") or {}).items()}
    if "home_assistant" in cfg and not any(v.get("type") == "home_assistant" for v in out.values()):
        out.setdefault("ha", {"type": "home_assistant", **cfg["home_assistant"]})
    return out


class Registry:
    """Live integration instances for one dashboard config."""

    def __init__(self, cfg: dict, reuse: "Registry | None" = None):
        load_builtin()
        self.items: dict[str, Integration] = {}
        self.cfgs = normalize(cfg)
        for name, icfg in self.cfgs.items():
            if reuse and reuse.cfgs.get(name) == icfg and name in reuse.items:
                self.items[name] = reuse.items[name]      # unchanged: keep the live connection
                continue
            cls = REGISTRY.get(icfg.get("type"))
            if cls is None:
                raise ValueError(f"integration {name!r}: unknown type {icfg.get('type')!r}; known: {sorted(REGISTRY)}")
            self.items[name] = cls(name, icfg)

    def start(self):
        for i in self.items.values():
            try:
                i.start()
            except Exception:
                log.exception("integration %s failed to start", i.name)

    def stop(self, keep: "Registry | None" = None):
        for i in self.items.values():
            if keep and any(i is j for j in keep.items.values()):
                continue
            try:
                i.stop()
            except Exception:
                log.exception("integration %s failed to stop", i.name)

    def get(self, name: str | None, type_name: str):
        """Look up by name, or the only integration of that type when name is omitted."""
        if name:
            i = self.items.get(name)
            if i is None:
                raise KeyError(f"no integration named {name!r}")
            return i
        matches = [i for i in self.items.values() if i.type_name == type_name]
        if len(matches) != 1:
            raise KeyError(f"expected exactly one {type_name} integration, found {len(matches)}; set `integration:`")
        return matches[0]
