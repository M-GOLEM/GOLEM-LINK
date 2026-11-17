"""Overload / thermal detection and alert generation."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .config import ConfigManager
from .db import Database
from .events import EventBus

log = logging.getLogger(__name__)

WARN_COOLDOWN_S = 120.0
CRIT_COOLDOWN_S = 45.0


@dataclass
class NodePressure:
    node_id: str
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    max_temp_c: float | None = None
    thermal_protected: bool = False
    blocked_reason: str | None = None
    active_tasks: int = 0
    score: float = 0.0
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "cpu_percent": self.cpu_percent,
            "ram_percent": self.ram_percent,
            "max_temp_c": self.max_temp_c,
            "thermal_protected": self.thermal_protected,
            "blocked_reason": self.blocked_reason,
            "active_tasks": self.active_tasks,
            "score": round(self.score, 3),
            "updated_at": self.updated_at,
        }


class OverloadEngine:
    def __init__(self, cfg: ConfigManager, db: Database, bus: EventBus, node_id: str) -> None:
        self.cfg = cfg
        self.db = db
        self.bus = bus
        self.node_id = node_id
        self._last_emit: dict[str, float] = {}
        self._critical_temp_streak: dict[str, int] = {}
        self.pressure = NodePressure(node_id=node_id)

    # -- alerts ------------------------------------------------------------
    def _emit(
        self,
        severity: str,
        category: str,
        message: str,
        value: float | None,
        threshold: float | None,
        dedupe_key: str,
    ) -> None:
        cooldown = CRIT_COOLDOWN_S if severity == "critical" else WARN_COOLDOWN_S
        now = time.time()
        if now - self._last_emit.get(dedupe_key, 0.0) < cooldown:
            return
        # The cooldown is in-memory, so an agent restart re-evaluating a still
        # open condition would otherwise insert a duplicate alert.
        if self.db.has_open_alert(dedupe_key):
            self._last_emit[dedupe_key] = now
            return
        self._last_emit[dedupe_key] = now
        alert_id = self.db.add_alert(
            self.node_id, severity, category, message, value, threshold, dedupe_key
        )
        payload = {
            "id": alert_id,
            "ts": now,
            "node_id": self.node_id,
            "severity": severity,
            "category": category,
            "message": message,
            "value": value,
            "threshold": threshold,
            "acknowledged": 0,
        }
        self.bus.publish("alert", payload)
        self.db.audit("system", "alert", category, message, None)
        log.warning("alert [%s] %s: %s", severity, category, message)

    # -- evaluation --------------------------------------------------------
    def evaluate(self, snapshot: dict, active_tasks: int = 0) -> NodePressure:
        t = self.cfg.get().thresholds
        p = NodePressure(node_id=self.node_id)
        p.cpu_percent = float(snapshot["cpu"]["percent"])
        p.ram_percent = float(snapshot["ram"]["percent"])
        p.active_tasks = active_tasks

        if p.cpu_percent >= t.cpu_crit_percent:
            self._emit("critical", "cpu", f"CPU at {p.cpu_percent:.0f}% (critical ≥ {t.cpu_crit_percent:.0f}%)", p.cpu_percent, t.cpu_crit_percent, "cpu-crit")
        elif p.cpu_percent >= t.cpu_warn_percent:
            self._emit("warning", "cpu", f"CPU at {p.cpu_percent:.0f}% (warning ≥ {t.cpu_warn_percent:.0f}%)", p.cpu_percent, t.cpu_warn_percent, "cpu-warn")

        if p.ram_percent >= t.ram_crit_percent:
            self._emit("critical", "ram", f"RAM at {p.ram_percent:.0f}% (critical ≥ {t.ram_crit_percent:.0f}%)", p.ram_percent, t.ram_crit_percent, "ram-crit")
        elif p.ram_percent >= t.ram_warn_percent:
            self._emit("warning", "ram", f"RAM at {p.ram_percent:.0f}% (warning ≥ {t.ram_warn_percent:.0f}%)", p.ram_percent, t.ram_warn_percent, "ram-warn")

        temps = snapshot.get("temps") or []
        if not temps:
            # Windows desktop boards expose no psutil sensors; the GPU reading
            # from nvidia-smi is still real, so thermal protection uses it
            # instead of silently disabling itself.
            gpus = [g for g in snapshot.get("gpu") or [] if g.get("temperature") is not None]
            if gpus:
                hottest_gpu = max(gpus, key=lambda g: float(g["temperature"]))
                temps = [
                    {
                        "label": f"{hottest_gpu.get('name') or 'GPU'} (GPU)",
                        "current": float(hottest_gpu["temperature"]),
                    }
                ]
        if temps:
            hottest = max(temps, key=lambda x: x["current"])
            label = hottest["label"]
            p.max_temp_c = float(hottest["current"])
            if t.temp_crit_c is not None and p.max_temp_c >= t.temp_crit_c:
                streak = self._critical_temp_streak.get(label, 0) + 1
                self._critical_temp_streak[label] = streak
                self._emit("critical", "temperature", f"{label} at {p.max_temp_c:.0f}°C (limit {t.temp_crit_c:.0f}°C)", p.max_temp_c, t.temp_crit_c, f"temp-crit-{label}")
                if streak >= t.thermal_sustained_samples:
                    p.thermal_protected = True
                    p.blocked_reason = "thermal protection active"
            else:
                self._critical_temp_streak[label] = 0 if (t.temp_crit_c is not None and p.max_temp_c < t.temp_crit_c) else self._critical_temp_streak.get(label, 0)
                if t.temp_warn_c is not None and p.max_temp_c >= t.temp_warn_c:
                    self._emit("warning", "temperature", f"{hottest['label']} at {p.max_temp_c:.0f}°C (warning {t.temp_warn_c:.0f}°C)", p.max_temp_c, t.temp_warn_c, f"temp-warn-{hottest['label']}")

        for disk in snapshot.get("disk", []):
            pct = 100.0 - float(disk.get("percent", 0.0))  # free percent
            if pct <= t.disk_free_crit_percent:
                self._emit("critical", "disk", f"{disk['mountpoint']} has {pct:.0f}% free", pct, t.disk_free_crit_percent, f"disk-crit-{disk['mountpoint']}")
            elif pct <= t.disk_free_warn_percent:
                self._emit("warning", "disk", f"{disk['mountpoint']} has {pct:.0f}% free", pct, t.disk_free_warn_percent, f"disk-warn-{disk['mountpoint']}")

        if p.thermal_protected:
            p.blocked_reason = "thermal protection active"
        elif p.cpu_percent >= t.cpu_crit_percent:
            p.blocked_reason = "cpu critical"
        elif p.ram_percent >= t.ram_crit_percent:
            p.blocked_reason = "memory critical"

        # Lower score = better candidate for new work. Weighted mix with a
        # hard penalty when the node cannot accept tasks at all.
        temp_component = 0.0
        if p.max_temp_c is not None:
            temp_component = min(max((p.max_temp_c - 50.0) / 50.0, 0.0), 1.0) * 20.0
        p.score = (p.cpu_percent * 0.55) + (p.ram_percent * 0.35) + temp_component
        if p.blocked_reason:
            p.score += 1000.0
        p.updated_at = time.time()
        self.pressure = p
        self.bus.publish("pressure", p.as_dict())
        return p

    def evaluate_network(self, node_id: str, latency_ms: float | None, loss_pct: float | None = None) -> None:
        t = self.cfg.get().thresholds
        if latency_ms is not None and latency_ms >= t.net_latency_warn_ms:
            self._emit("warning", "network", f"High latency to {node_id}: {latency_ms:.0f} ms", latency_ms, t.net_latency_warn_ms, f"net-lat-{node_id}")
        if loss_pct is not None and loss_pct >= t.net_loss_warn_percent:
            self._emit("warning", "network", f"Packet loss to {node_id}: {loss_pct:.1f}%", loss_pct, t.net_loss_warn_percent, f"net-loss-{node_id}")

    def can_accept_tasks(self) -> tuple[bool, str | None]:
        p = self.pressure
        if p.blocked_reason:
            return False, p.blocked_reason
        return True, None
