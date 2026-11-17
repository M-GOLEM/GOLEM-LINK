"""Task plugin contract shared by the scheduler and the isolated worker."""

from __future__ import annotations

import abc
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class PluginContext:
    task_id: str
    spec: dict[str, Any]
    output_dir: Path
    emit: Callable[[dict], None]
    _cancelled: threading.Event = field(default_factory=threading.Event)
    logs: list[str] = field(default_factory=list)
    _last_progress: float = 0.0

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()

    def log(self, message: str, level: str = "info") -> None:
        line = f"[{level}] {message}"
        self.logs.append(line)
        if len(self.logs) > 500:
            del self.logs[:100]
        self.emit({"type": "log", "level": level, "message": message})

    def progress(self, fraction: float, message: str | None = None, force: bool = False) -> None:
        now = time.time()
        fraction = min(max(float(fraction), 0.0), 1.0)
        if not force and now - self._last_progress < 0.25 and fraction < 1.0:
            return
        self._last_progress = now
        payload: dict[str, Any] = {"type": "progress", "value": fraction}
        if message:
            payload["message"] = message
            self.logs.append(message)
        self.emit(payload)


class TaskPlugin(abc.ABC):
    name: str = ""
    description: str = ""
    requires: list[str] = []
    default_enabled: bool = True
    # When true the plugin runs user-supplied code and must be enabled
    # explicitly in configuration.
    runs_user_code: bool = False

    def validate(self, spec: dict[str, Any]) -> dict[str, Any]:
        return spec

    @abc.abstractmethod
    def run(self, ctx: PluginContext) -> dict[str, Any]:
        ...


def emit_line(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
    sys.stdout.flush()
