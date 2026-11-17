"""Isolated task worker process.

Protocol (stdin -> stdout, JSON lines)
--------------------------------------
stdin:  one JSON object: {"task_id","plugin","spec","output_dir","memory_mb"}
stdout: {"type":"progress"|"log"|"result"|"error"|"done", ...}
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path

from .base import PluginContext, emit_line
from .plugins import get_plugin


def _apply_limits(memory_mb: int | None) -> None:
    if os.name == "nt":
        # Windows has no portable address-space rlimit here. memory_mb is a
        # reservation used by the scheduler, not a hard per-process cap.
        return
    try:
        os.nice(10)
    except OSError:
        pass
    if memory_mb:
        try:
            import resource

            limit = int(memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (ImportError, ValueError, OSError):
            pass


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        emit_line({"type": "error", "message": "worker payload is not valid JSON"})
        return 2
    task_id = payload.get("task_id") or "unknown"
    plugin_name = payload.get("plugin") or ""
    spec = payload.get("spec") or {}
    output_dir = Path(payload.get("output_dir") or Path.cwd() / "task-output")
    output_dir.mkdir(parents=True, exist_ok=True)
    _apply_limits(payload.get("memory_mb"))

    ctx = PluginContext(task_id=task_id, spec=spec, output_dir=output_dir, emit=emit_line)

    def _handle_term(_signum, _frame):
        ctx.cancel()

    for sig_name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, sig_name):
            try:
                signal.signal(getattr(signal, sig_name), _handle_term)
            except (ValueError, OSError):
                pass

    started = time.time()
    try:
        plugin = get_plugin(plugin_name)
        if plugin.runs_user_code and not spec.get("allow_user_code"):
            raise RuntimeError(f"plugin '{plugin_name}' requires explicit user-code authorization")
        ctx.log(f"plugin {plugin_name} starting on {sys.platform}")
        result = plugin.run(ctx)
        emit_line({"type": "result", "data": result or {}})
        emit_line(
            {
                "type": "done",
                "elapsed_s": round(time.time() - started, 2),
            }
        )
        return 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        try:
            (output_dir / "worker-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        emit_line({"type": "error", "message": str(exc) or exc.__class__.__name__})
        emit_line({"type": "done", "elapsed_s": round(time.time() - started, 2)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
