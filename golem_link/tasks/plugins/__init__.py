"""Task plugin registry."""

from __future__ import annotations

import importlib.util
from typing import Type

from ..base import TaskPlugin

PLUGINS: dict[str, TaskPlugin] = {}


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def register(cls: Type[TaskPlugin]) -> Type[TaskPlugin]:
    instance = cls()
    if not instance.name:
        raise ValueError("plugin must define a name")
    PLUGINS[instance.name] = instance
    return cls


def get_plugin(name: str) -> TaskPlugin:
    try:
        return PLUGINS[name]
    except KeyError as exc:
        raise ValueError(f"unknown task plugin '{name}'") from exc


def plugin_catalog(enabled: list[str], allow_user_code: bool) -> list[dict]:
    items = []
    for plugin in PLUGINS.values():
        active = plugin.name in enabled and (allow_user_code or not plugin.runs_user_code)
        available = all(_module_available(req) for req in plugin.requires)
        items.append(
            {
                "name": plugin.name,
                "description": plugin.description,
                "requires": plugin.requires,
                "enabled": active and available,
                "runs_user_code": plugin.runs_user_code,
                "available": available,
            }
        )
    return items


# Importing the built-in module fills the registry.
from . import builtin  # noqa: E402,F401
