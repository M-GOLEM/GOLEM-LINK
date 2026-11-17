"""Automatic host relief: return the agent's own cached memory to the OS.

Only the GOLEM LINK process is touched. Operating-system processes and the
operator's applications are never modified; the goal is to keep the desktop
responsive, not to police the machine.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import time

import psutil

from .config import ConfigManager
from .events import EventBus

log = logging.getLogger(__name__)


def _release_os_memory() -> str | None:
    """Ask the platform allocator to hand free pages back (best effort)."""
    if hasattr(ctypes, "WinDLL"):
        try:
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
            psapi.EmptyWorkingSet.restype = wintypes.BOOL
            if psapi.EmptyWorkingSet(kernel32.GetCurrentProcess()):
                return "EmptyWorkingSet"
            return None
        except Exception:
            log.debug("EmptyWorkingSet unavailable", exc_info=True)
            return None
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
        return "malloc_trim"
    except Exception:
        log.debug("malloc_trim unavailable", exc_info=True)
        return None


class HostGuard:
    def __init__(self, cfg: ConfigManager, bus: EventBus) -> None:
        self.cfg = cfg
        self.bus = bus
        self._process = psutil.Process()
        self.last_run: float | None = None
        self.last_reason: str | None = None
        self.last_reclaimed_bytes = 0
        self.total_reclaimed_bytes = 0
        self.last_ram_percent: float | None = None
        self.last_method: str | None = None
        self.runs = 0

    # -- introspection -----------------------------------------------------
    def _rss(self) -> int:
        try:
            return int(self._process.memory_info().rss)
        except psutil.Error:
            return 0

    def status(self) -> dict:
        sched = self.cfg.get().scheduling
        return {
            "enabled": sched.relieve_memory,
            "threshold_percent": sched.relieve_ram_percent,
            "cooldown_s": sched.relieve_cooldown_s,
            "ram_percent": self.last_ram_percent,
            "runs": self.runs,
            "last_run": self.last_run,
            "last_reason": self.last_reason,
            "last_reclaimed_bytes": self.last_reclaimed_bytes,
            "total_reclaimed_bytes": self.total_reclaimed_bytes,
            "last_method": self.last_method,
        }

    # -- relief ------------------------------------------------------------
    def pressure_reason(self, snapshot: dict | None) -> str | None:
        if snapshot is None:
            return None
        try:
            ram = float(snapshot["ram"]["percent"])
        except (KeyError, TypeError, ValueError):
            return None
        self.last_ram_percent = ram
        if ram < self.cfg.get().scheduling.relieve_ram_percent:
            return None
        return f"RAM {ram:.0f}%"

    def maybe_relieve(self, snapshot: dict | None) -> dict | None:
        sched = self.cfg.get().scheduling
        if not sched.relieve_memory:
            return None
        reason = self.pressure_reason(snapshot)
        if reason is None:
            return None
        now = time.time()
        if self.last_run is not None and now - self.last_run < sched.relieve_cooldown_s:
            return None
        return self.relieve(reason)

    def relieve(self, reason: str) -> dict:
        before = self._rss()
        collected = gc.collect()
        method = _release_os_memory()
        after = self._rss()
        reclaimed = max(0, before - after)
        self.runs += 1
        self.last_run = time.time()
        self.last_reason = reason
        self.last_reclaimed_bytes = reclaimed
        self.total_reclaimed_bytes += reclaimed
        self.last_method = method
        log.info(
            "host relief (%s): reclaimed %.1f MB via %s",
            reason,
            reclaimed / (1024 * 1024),
            method or "gc only",
        )
        payload = {
            "ts": self.last_run,
            "reason": reason,
            "reclaimed_bytes": reclaimed,
            "collected_objects": collected,
            "method": method,
            "rss_bytes": after,
        }
        self.bus.publish("host_guard", payload)
        return payload
