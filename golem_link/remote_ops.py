"""Operational view of a node and the two maintenance actions behind it.

Everything in :func:`collect` is read-only and cheap: it answers "what is this
machine running, is the service healthy, how full is it, what is in the
queue". Paired controllers reach it through ``/peer/ops`` only when the target
node grants ``allow_control``.

The actions (restart, rollback) are delegated to the update manager, which
owns the platform-specific privilege story. Nothing here takes a command from
a request.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import remote_maintenance as remote
from .version import VERSION

LOG_TAIL_BYTES = 256 * 1024


def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int | None, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def _unit_state(unit: str) -> dict:
    active_rc, active = _run(["systemctl", "is-active", unit])
    enabled_rc, enabled = _run(["systemctl", "is-enabled", unit])
    return {
        "unit": unit,
        "active": active.splitlines()[0] if active else None,
        "enabled": enabled.splitlines()[0] if enabled else None,
        "ok": active_rc == 0,
    }


def _windows_service() -> dict:
    rc, out = _run(["sc", "query", "golem-link"])
    state = ""
    for line in out.splitlines():
        if "STATE" in line:
            state = line.split(":", 1)[-1].strip()
            break
    return {
        "mode": "windows-service",
        "name": "golem-link",
        "state": state or None,
        "ok": rc == 0 and "RUNNING" in state.upper(),
    }


def _dir_size(path: Path, limit_entries: int = 20000) -> int:
    total = 0
    seen = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
                seen += 1
                if seen >= limit_entries:
                    return total
    except OSError:
        pass
    return total


def tail_log(ctx, lines: int = 200) -> list[str]:
    path = Path(ctx.cfg.data_dir) / "logs" / "golem-link.log"
    if not path.is_file():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - LOG_TAIL_BYTES))
            data = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return data[-max(1, min(int(lines), 1000)):]


def _task_counts(ctx) -> dict:
    rows = ctx.db.query("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")
    counts = {str(row["status"]): int(row["n"]) for row in rows}
    return {
        "queued": counts.get("queued", 0) + counts.get("dispatching", 0),
        "running": counts.get("running", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
        "cancelled": counts.get("cancelled", 0),
    }


def _disk_view(path: Path) -> dict | None:
    try:
        usage = shutil.disk_usage(str(path))
    except OSError:
        return None
    return {
        "path": str(path),
        "total": usage.total,
        "free": usage.free,
        "used_percent": round((usage.used / usage.total) * 100, 1) if usage.total else None,
    }


def collect(ctx) -> dict:
    data_dir = Path(ctx.cfg.data_dir)
    metrics = ctx.bus.latest("metrics") or {}
    update = ctx.updates.status()
    service: dict
    units: list[dict] = []
    if ctx.updates.platform == "windows":
        service = _windows_service()
    elif ctx.updates.platform == "linux":
        units = [_unit_state("golem-link"), _unit_state("golem-link-update.path")]
        service = {
            "mode": "systemd",
            "name": "golem-link",
            "state": units[0]["active"],
            "ok": units[0]["ok"],
            "helper_ready": remote.helper_ready(),
            "helper_can_rollback": remote.helper_supports("rollback"),
        }
    else:
        service = {"mode": "process", "name": "golem-link", "state": "running", "ok": True}
    disk = metrics.get("disk") or {}
    if isinstance(disk, dict) and "percent" in disk:
        disk_view = {
            "path": str(data_dir),
            "total": disk.get("total"),
            "free": disk.get("free"),
            "used_percent": disk.get("percent"),
        }
    else:
        disk_view = _disk_view(data_dir) or {}
    return {
        "node_id": ctx.identity.node_id,
        "node_name": ctx.cfg.get().node_name,
        "version": VERSION,
        "platform": ctx.updates.platform,
        "os": platform.platform(),
        "hostname": socket.gethostname(),
        "python": platform.python_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "uptime_s": metrics.get("uptime_s"),
        "cpu": metrics.get("cpu") or {},
        "ram": metrics.get("ram") or {},
        "disk": disk_view,
        "service": service,
        "units": units,
        "data_dir": str(data_dir),
        "db_bytes": Path(ctx.cfg.db_path()).stat().st_size
        if Path(ctx.cfg.db_path()).is_file()
        else 0,
        "logs_bytes": _dir_size(data_dir / "logs"),
        "update": {
            key: update.get(key)
            for key in (
                "state",
                "current_version",
                "latest_version",
                "auto_install",
                "remote_install",
                "rollback_supported",
                "error",
            )
        },
        "tasks": _task_counts(ctx),
        "peers": {
            "paired": len(ctx.db.list_peers()),
            "online": sum(1 for p in ctx.peers.peers.values() if p.status == "online"),
        },
        "collected_at": time.time(),
    }


def restart(ctx) -> dict:
    return ctx.updates.restart_agent()


def rollback(ctx, confirm: bool) -> dict:
    if not confirm:
        raise ValueError("confirmation is required to roll back an update")
    return ctx.updates.rollback()
