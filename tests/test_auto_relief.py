"""Automatic host relief: queue aging, running-task preemption, memory trim."""

from __future__ import annotations

import os
import time

import pytest

from golem_link import host_guard as host_guard_module
from golem_link.config import ConfigManager
from golem_link.db import Database
from golem_link.events import EventBus
from golem_link.host_guard import HostGuard
from golem_link.identity import NodeIdentity
from golem_link.overload import OverloadEngine
from golem_link.resource_monitor import ResourceMonitor
from golem_link.tasks.engine import Scheduler


class FakePeer:
    status = "online"
    remote_accepts_tasks = True
    node_id = "gl-peer"
    metrics = {
        "cpu": {"percent": 10.0, "cores_logical": 8},
        "ram": {"available": 8 * 1024 * 1024 * 1024, "percent": 20.0},
    }


class FakePeers:
    def __init__(self, pressure):
        self.peers = {"gl-peer": FakePeer()}
        self._pressure = pressure

    def pressure_for(self, node_id):
        return self._pressure


class FakeProc:
    def __init__(self):
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    async def wait(self):
        return 0


def _metrics(cpu=20.0, ram=30.0):
    return {
        "cpu": {"percent": cpu, "cores_logical": 8},
        "ram": {"available": 8 * 1024 * 1024 * 1024, "percent": ram},
    }


def _scheduler(tmp_path, metrics, pressure=None, **sched_fields):
    cfg = ConfigManager(tmp_path / "data")
    cfg.ensure_dirs()
    cfg.update(
        lambda c: [setattr(c.scheduling, key, value) for key, value in sched_fields.items()]
    )
    db = Database(cfg.db_path())
    bus = EventBus()
    bus.publish("metrics", metrics)
    overload = OverloadEngine(cfg, db, bus, "gl-self")
    scheduler = Scheduler(NodeIdentity(cfg), cfg, db, bus, overload)
    scheduler.peers = FakePeers(pressure) if pressure is not None else None
    return scheduler, db


def _task(db, task_id="t-test", *, created_at=None, priority=5, status="queued", **fields):
    row = {
        "id": task_id,
        "name": task_id,
        "spec": "{}",
        "target_node": None,
        "source_node": "gl-self",
        "executor_node": None,
        "status": status,
        "created_at": time.time() if created_at is None else created_at,
        "timeout_s": 60,
        "plugin": "text_stats",
        "priority": priority,
    }
    row.update(fields)
    db.insert_task(row)
    return row


# -- ranking ----------------------------------------------------------------
def test_aging_lifts_an_old_low_priority_task(tmp_path):
    scheduler, db = _scheduler(tmp_path, _metrics())
    now = time.time()
    _task(db, "t-old", created_at=now - 600, priority=1)
    _task(db, "t-new", created_at=now, priority=10)
    assert [row["id"] for row in scheduler._rank_queued(now)][:2] == ["t-old", "t-new"]
    db.close()


def test_aging_is_capped_and_keeps_fifo_ties(tmp_path):
    scheduler, db = _scheduler(
        tmp_path, _metrics(), rank_aging_per_min=2.0, rank_aging_max=25.0
    )
    now = time.time()
    _task(db, "t-ancient", created_at=now - 3600, priority=1)
    _task(db, "t-strong", created_at=now, priority=30)
    _task(db, "t-early", created_at=now - 120, priority=10)
    _task(db, "t-late", created_at=now - 60, priority=10)
    ranked = [row["id"] for row in scheduler._rank_queued(now)]
    # 1 + capped 25 = 26 < 30, and equal priorities fall back to arrival order.
    assert ranked[:3] == ["t-strong", "t-ancient", "t-early"]
    assert ranked.index("t-early") < ranked.index("t-late")
    db.close()


def test_aging_can_be_disabled(tmp_path):
    scheduler, db = _scheduler(tmp_path, _metrics(), rank_aging_per_min=0.0)
    now = time.time()
    _task(db, "t-old", created_at=now - 600, priority=1)
    _task(db, "t-new", created_at=now, priority=10)
    assert [row["id"] for row in scheduler._rank_queued(now)][:2] == ["t-new", "t-old"]
    db.close()


# -- preemption -------------------------------------------------------------
async def test_busy_host_moves_its_own_running_task_to_a_peer(tmp_path):
    scheduler, db = _scheduler(
        tmp_path,
        _metrics(cpu=85.0),
        pressure={"score": 20.0},
        preempt_after_s=0.0,
        preempt_max_per_task=2,
    )
    now = time.time()
    _task(
        db,
        "t-run",
        status="running",
        source_node=scheduler.node_id,
        executor_node=scheduler.node_id,
        started_at=now - 30.0,
        progress=0.2,
    )
    proc = FakeProc()
    scheduler._procs["t-run"] = proc
    scheduler._preempt_if_frozen(now)
    scheduler._preempt_if_frozen(now)
    await __import__("asyncio").sleep(0)
    row = db.get_task("t-run")
    assert proc.terminated
    assert row["status"] == "queued"
    assert row["executor_node"] is None
    assert row["started_at"] is None
    assert row["preempt_count"] == 1
    events = [event["event"] for event in db.task_events("t-run")]
    assert "preempted" in events
    db.close()


async def test_no_preemption_without_a_healthy_peer(tmp_path):
    scheduler, db = _scheduler(
        tmp_path, _metrics(cpu=85.0), pressure=None, preempt_after_s=0.0
    )
    now = time.time()
    _task(
        db,
        "t-run",
        status="running",
        source_node=scheduler.node_id,
        executor_node=scheduler.node_id,
        started_at=now - 30.0,
    )
    proc = FakeProc()
    scheduler._procs["t-run"] = proc
    scheduler._preempt_if_frozen(now)
    scheduler._preempt_if_frozen(now)
    assert not proc.terminated
    assert db.get_task("t-run")["status"] == "running"
    db.close()


async def test_preemption_respects_the_move_cap_and_pinned_targets(tmp_path):
    scheduler, db = _scheduler(
        tmp_path,
        _metrics(cpu=85.0),
        pressure={"score": 20.0},
        preempt_after_s=0.0,
        preempt_max_per_task=2,
    )
    now = time.time()
    _task(
        db,
        "t-capped",
        status="running",
        source_node=scheduler.node_id,
        executor_node=scheduler.node_id,
        started_at=now - 30.0,
        preempt_count=2,
    )
    _task(
        db,
        "t-pinned",
        status="running",
        source_node=scheduler.node_id,
        executor_node=scheduler.node_id,
        started_at=now - 30.0,
        target_node="gl-peer",
    )
    procs = {"t-capped": FakeProc(), "t-pinned": FakeProc()}
    scheduler._procs.update(procs)
    scheduler._preempt_if_frozen(now)
    scheduler._preempt_if_frozen(now)
    assert not procs["t-capped"].terminated
    assert not procs["t-pinned"].terminated
    db.close()


def test_idle_recovery_resets_the_busy_timer(tmp_path):
    scheduler, db = _scheduler(tmp_path, _metrics(cpu=20.0), pressure={"score": 20.0})
    scheduler._busy_since = time.time() - 100
    scheduler._preempt_if_frozen(time.time())
    assert scheduler._busy_since is None
    db.close()


def test_relief_status_reports_the_controls(tmp_path):
    scheduler, db = _scheduler(tmp_path, _metrics())
    status = scheduler.relief_status()
    assert status["preempt_on_overload"] is True
    assert status["rank_aging_max"] > 0
    assert status["local_running"] == 0
    db.close()


# -- memory relief ----------------------------------------------------------
def _guard(tmp_path, **sched_fields):
    cfg = ConfigManager(tmp_path / "guard")
    cfg.ensure_dirs()
    cfg.update(
        lambda c: [setattr(c.scheduling, key, value) for key, value in sched_fields.items()]
    )
    return HostGuard(cfg, EventBus()), cfg


def test_host_guard_releases_memory_under_pressure(tmp_path, monkeypatch):
    guard, _cfg = _guard(
        tmp_path, relieve_ram_percent=80.0, relieve_cooldown_s=600.0
    )
    rss = [200 * 1024 * 1024, 120 * 1024 * 1024]
    monkeypatch.setattr(guard, "_rss", lambda: rss.pop(0))
    monkeypatch.setattr(host_guard_module, "_release_os_memory", lambda: "test-trim")
    payload = guard.maybe_relieve(_metrics(ram=85.0))
    assert payload is not None
    assert payload["reclaimed_bytes"] == 80 * 1024 * 1024
    assert payload["method"] == "test-trim"
    assert guard.status()["runs"] == 1
    # Cooldown bounds how often the agent collects garbage under pressure.
    assert guard.maybe_relieve(_metrics(ram=85.0)) is None


def test_host_guard_ignores_a_comfortable_host_and_can_be_disabled(tmp_path, monkeypatch):
    guard, _cfg = _guard(tmp_path, relieve_ram_percent=80.0)
    monkeypatch.setattr(host_guard_module, "_release_os_memory", lambda: "test-trim")
    monkeypatch.setattr(guard, "_rss", lambda: 100)
    assert guard.maybe_relieve(_metrics(ram=50.0)) is None

    disabled, _ = _guard(tmp_path / "off", relieve_memory=False)
    assert disabled.maybe_relieve(_metrics(ram=95.0)) is None


# -- worker priority and adaptive sampling ----------------------------------
def test_workers_run_below_normal_priority(tmp_path):
    scheduler, db = _scheduler(tmp_path, _metrics(), nice_workers=True)
    command = scheduler._worker_command()
    assert command[-2:] == ["-m", "golem_link.tasks.worker"]
    if os.name != "nt":
        import shutil

        nice_path = shutil.which("nice")
        if nice_path:
            assert command[0] == nice_path
            assert command[1:3] == ["-n", "10"]
    if os.name == "nt":
        flags = scheduler._worker_creationflags()
        assert flags & 0x08000000  # CREATE_NO_WINDOW
        assert flags & 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
    else:
        assert scheduler._worker_creationflags() == 0
    db.close()


def test_worker_priority_can_be_disabled(tmp_path):
    scheduler, db = _scheduler(tmp_path, _metrics(), nice_workers=False)
    assert scheduler._worker_command()[:2] == [__import__("sys").executable, "-m"]
    if os.name == "nt":
        assert not scheduler._worker_creationflags() & 0x00004000
    db.close()


def test_monitor_slows_down_under_high_cpu_and_recovers(tmp_path):
    cfg = ConfigManager(tmp_path / "monitor")
    cfg.ensure_dirs()
    cfg.update(lambda c: setattr(c.thresholds, "sample_interval_s", 2.0))
    monitor = ResourceMonitor(NodeIdentity(cfg), cfg, EventBus())
    assert monitor._next_interval(_metrics(cpu=20.0)) == 2.0
    assert monitor._next_interval(_metrics(cpu=90.0)) == 4.0
    assert monitor._next_interval(_metrics(cpu=75.0)) == 4.0  # hysteresis
    assert monitor._next_interval(_metrics(cpu=60.0)) == 2.0


def test_monitor_interval_is_capped_and_can_be_fixed(tmp_path):
    cfg = ConfigManager(tmp_path / "monitor-cap")
    cfg.ensure_dirs()
    cfg.update(
        lambda c: (
            setattr(c.thresholds, "sample_interval_s", 4.0),
            setattr(c.thresholds, "max_sample_interval_s", 5.0),
        )
    )
    monitor = ResourceMonitor(NodeIdentity(cfg), cfg, EventBus())
    assert monitor._next_interval(_metrics(cpu=95.0)) == 5.0

    cfg.update(lambda c: setattr(c.thresholds, "sample_interval_s", 2.0))
    cfg.update(lambda c: setattr(c.thresholds, "adaptive_sampling", False))
    assert monitor._next_interval(_metrics(cpu=95.0)) == 2.0
