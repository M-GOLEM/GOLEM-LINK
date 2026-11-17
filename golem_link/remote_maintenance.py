"""Opt-in remote maintenance for Linux nodes.

The Linux service runs with ``NoNewPrivileges`` and never elevates itself.
When the operator installs GOLEM LINK with ``--allow-remote-updates`` the
installer adds a root-owned one-shot unit plus a fixed helper; paired devices
can then ask this node to install a *verified* staged release. The helper
takes no arguments, re-checks the staged artifact's SHA-256 and size, copies
it to a root-owned path and runs the installer.

Enablement is root-owned on purpose: the unprivileged agent can only *request*
the helper, never change what it does. Delete
``/etc/golem-link/remote-updates.enabled`` and disable the
``golem-link-update.path`` unit to revoke it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

MARKER = Path("/etc/golem-link/remote-updates.enabled")
HELPER = Path("/opt/golem-link/bin/golem-link-apply-update")
REQUEST_NAME = "remote-update.request"
RESULT_NAME = "remote-update.result"


def enabled() -> bool:
    return os.name == "posix" and MARKER.is_file()


def helper_ready() -> bool:
    return enabled() and HELPER.is_file() and os.access(HELPER, os.X_OK)


def request_path(work_dir: Path) -> Path:
    return Path(work_dir) / REQUEST_NAME


def result_path(work_dir: Path) -> Path:
    return Path(work_dir) / RESULT_NAME


def read_result(work_dir: Path) -> dict | None:
    try:
        data = json.loads(result_path(work_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_request(work_dir: Path, payload: dict) -> Path:
    """Hand a request to the root helper atomically.

    The agent owns ``updates/``; the helper owns everything it does with the
    file. Replacing (never appending) means a stale request can never be
    replayed after a crash.
    """
    target = request_path(work_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".request-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    return target


def clear_request(work_dir: Path) -> None:
    try:
        request_path(work_dir).unlink()
    except FileNotFoundError:
        pass


def helper_supports(action: str) -> bool:
    """True when the installed helper knows a request action.

    Helpers are root-owned and replaced by the installer, so reading the file
    is the cheapest reliable capability probe (unlike a version file, it can
    never drift from the script that actually runs).
    """
    if not HELPER.is_file():
        return False
    try:
        with HELPER.open("rb") as handle:
            head = handle.read(64 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return False
    return f'"{action}"' in head or f"'{action}'" in head
