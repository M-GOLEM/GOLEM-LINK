import psutil
from types import SimpleNamespace

from golem_link import resource_monitor
from golem_link.config import ConfigManager
from golem_link.db import Database
from golem_link.events import EventBus
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


def _metrics(cpu=20.0, ram=30.0):
    return {
        "cpu": {"percent": cpu, "cores_logical": 8},
        "ram": {"available": 8 * 1024 * 1024 * 1024, "percent": ram},
    }


def _scheduler(tmp_path, metrics, pressure=None, policy="local_first", keep_light=True):
    cfg = ConfigManager(tmp_path / "data")
    cfg.ensure_dirs()
    cfg.update(
        lambda c: (
            setattr(c.scheduling, "policy", policy),
            setattr(c.scheduling, "keep_host_light", keep_light),
        )
    )
    db = Database(cfg.db_path())
    bus = EventBus()
    bus.publish("metrics", metrics)
    overload = OverloadEngine(cfg, db, bus, "gl-self")
    scheduler = Scheduler(NodeIdentity(cfg), cfg, db, bus, overload)
    scheduler.peers = FakePeers(pressure) if pressure is not None else None
    return scheduler, db


def _row(db):
    row = {
        "id": "t-test",
        "name": "test",
        "spec": "{}",
        "target_node": None,
        "source_node": "gl-self",
        "executor_node": None,
        "status": "queued",
        "created_at": 0.0,
        "timeout_s": 60,
        "plugin": "text_stats",
        "priority": 5,
    }
    db.insert_task(row)
    return row


def test_light_guard_offloads_new_work_when_host_busy(tmp_path):
    scheduler, db = _scheduler(tmp_path, _metrics(cpu=72.0), pressure={"score": 20.0})
    assert scheduler._light_guard() == "CPU 72%"
    assert scheduler._choose_executor(_row(db)) == "gl-peer"
    db.close()


def test_light_guard_still_runs_locally_when_no_peer_exists(tmp_path):
    scheduler, db = _scheduler(tmp_path, _metrics(cpu=72.0), pressure=None)
    assert scheduler._choose_executor(_row(db)) == scheduler.node_id
    db.close()


def test_busy_peer_is_not_an_offload_target(tmp_path):
    scheduler, db = _scheduler(
        tmp_path, _metrics(cpu=72.0), pressure={"score": 90.0}
    )
    assert scheduler._choose_executor(_row(db)) == scheduler.node_id
    db.close()


def test_light_guard_can_be_disabled(tmp_path):
    scheduler, db = _scheduler(
        tmp_path, _metrics(cpu=72.0), pressure={"score": 20.0}, keep_light=False
    )
    assert scheduler._light_guard() is None
    assert scheduler._choose_executor(_row(db)) == scheduler.node_id
    db.close()


def test_light_guard_idle_host_keeps_work_local(tmp_path):
    scheduler, db = _scheduler(tmp_path, _metrics(cpu=20.0, ram=30.0), pressure={"score": 5.0})
    assert scheduler._light_guard() is None
    assert scheduler._choose_executor(_row(db)) == scheduler.node_id
    db.close()


class _FakeProc:
    def __init__(self, pid, name, cpu, rss):
        self.info = {"pid": pid, "name": name}
        self._cpu = cpu
        self._rss = rss

    def cpu_percent(self, interval=None):
        return self._cpu

    def memory_info(self):
        return SimpleNamespace(rss=self._rss)


def test_top_processes_sorted_and_excludes_idle(tmp_path, monkeypatch):
    cfg = ConfigManager(tmp_path / "data")
    cfg.ensure_dirs()
    monitor = ResourceMonitor(NodeIdentity(cfg), cfg, EventBus())
    procs = [
        _FakeProc(1, "System Idle Process", 90.0, 1024),
        _FakeProc(2, "chrome.exe", 40.0, 900 * 1024 * 1024),
        _FakeProc(3, "golem-link.exe", 5.0, 40 * 1024 * 1024),
        _FakeProc(4, "python.exe", 25.0, 300 * 1024 * 1024),
    ]
    monkeypatch.setattr(resource_monitor.psutil, "process_iter", lambda _attrs: procs)
    top = monitor._top_processes()
    assert [p["name"] for p in top["top_cpu"]] == ["chrome.exe", "python.exe", "golem-link.exe"]
    assert [p["name"] for p in top["top_mem"]] == ["chrome.exe", "python.exe", "golem-link.exe"]
    assert all("System Idle Process" not in p["name"] for p in top["top_cpu"])
