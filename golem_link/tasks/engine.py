"""Distributed scheduler: chooses executors, runs isolated workers, syncs results."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import psutil

from ..config import ConfigManager
from ..db import Database
from ..events import EventBus
from ..identity import NodeIdentity
from ..overload import OverloadEngine
from .base import TaskPlugin
from .plugins import PLUGINS, get_plugin, plugin_catalog

log = logging.getLogger(__name__)

TERMINAL = ("completed", "failed", "cancelled")
# Pressure score (0-100, 1000+ when blocked) above which a node is
# considered too loaded to keep sending work to.
REBALANCE_SCORE = 80.0
# A stored result larger than this is replaced with an explicit marker: a
# silent truncation would look like a successful but incomplete answer.
MAX_RESULT_BYTES = 4 * 1024 * 1024


class Scheduler:
    def __init__(
        self,
        identity: NodeIdentity,
        cfg: ConfigManager,
        db: Database,
        bus: EventBus,
        overload: OverloadEngine,
    ) -> None:
        self.identity = identity
        self.cfg = cfg
        self.db = db
        self.bus = bus
        self.overload = overload
        self.peers = None  # set by app
        self.storage = None  # set by app
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._runners: dict[str, asyncio.Task] = {}
        self._cooldowns: dict[str, float] = {}
        self._rebalanced: set[tuple[str, str]] = set()
        self._offloaded: set[str] = set()
        self._preempted: set[str] = set()
        self._busy_since: float | None = None
        self._schedule_lock = asyncio.Lock()
        self._last_owner_update: dict[str, float] = {}
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._started = False

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        self._stop.clear()
        self._started = True
        self._loop_task = asyncio.create_task(self._loop(), name="scheduler")
        log.info("scheduler started")

    async def stop(self) -> None:
        self._stop.set()
        if self._loop_task:
            self._loop_task.cancel()
        for task_id, proc in list(self._procs.items()):
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        for runner in list(self._runners.values()):
            runner.cancel()
        await asyncio.gather(*self._runners.values(), return_exceptions=True)
        if self._loop_task:
            await asyncio.gather(self._loop_task, return_exceptions=True)
        self._started = False

    # -- helpers -----------------------------------------------------------
    @property
    def node_id(self) -> str:
        return self.identity.node_id

    def plugin_names(self) -> list[str]:
        enabled = self.cfg.get().scheduling.enabled_plugins
        allow_user = self.cfg.get().scheduling.allow_custom_python
        return [p["name"] for p in plugin_catalog(enabled, allow_user) if p["enabled"]]

    def catalog(self) -> list[dict]:
        return plugin_catalog(
            self.cfg.get().scheduling.enabled_plugins,
            self.cfg.get().scheduling.allow_custom_python,
        )

    def _plugin_allowed(self, plugin: TaskPlugin) -> bool:
        sched = self.cfg.get().scheduling
        return plugin.name in sched.enabled_plugins and (
            sched.allow_custom_python or not plugin.runs_user_code
        )

    def relief_status(self) -> dict:
        sched = self.cfg.get().scheduling
        return {
            "preempt_on_overload": sched.preempt_on_overload,
            "preempt_after_s": sched.preempt_after_s,
            "preempt_max_per_task": sched.preempt_max_per_task,
            "busy_for_s": round(time.time() - self._busy_since, 1) if self._busy_since else 0.0,
            "local_running": len(self._procs),
            "rank_aging_per_min": sched.rank_aging_per_min,
            "rank_aging_max": sched.rank_aging_max,
            "nice_workers": sched.nice_workers,
        }

    def _task_view(self, row: dict, include_events: bool = False) -> dict:
        task = dict(row)
        for key in ("spec", "result", "resource_profile"):
            if task.get(key):
                try:
                    task[key] = json.loads(task[key])
                except json.JSONDecodeError:
                    task[key] = None
            else:
                task[key] = None
        task["result_truncated"] = bool(
            isinstance(task.get("result"), dict) and task["result"].get("truncated")
        )
        task["is_remote"] = task["executor_node"] not in (None, self.node_id)
        task["can_cancel"] = task["status"] in ("queued", "dispatching", "running")
        task["can_retry"] = task["status"] in ("failed", "cancelled")
        if include_events:
            task["events"] = self.db.task_events(task["id"])
        return task

    def list_tasks(self, limit: int = 200, status: str | None = None) -> list[dict]:
        return [self._task_view(r) for r in self.db.list_tasks(limit, status)]

    def get_task(self, task_id: str, include_events: bool = True) -> dict | None:
        row = self.db.get_task(task_id)
        if row is None:
            return None
        return self._task_view(row, include_events)

    def _publish(self, task_id: str) -> None:
        view = self.get_task(task_id, include_events=False)
        if view:
            self.bus.publish("task", view)

    def _event(self, task_id: str, event: str, detail: str | None = None) -> None:
        self.db.add_task_event(task_id, event, detail)
        if detail:
            log.info("task %s %s: %s", task_id, event, detail)

    # -- submission --------------------------------------------------------
    async def submit_local(
        self,
        *,
        name: str,
        plugin: str,
        spec: dict[str, Any],
        priority: int = 5,
        timeout_s: int | None = None,
        max_retries: int | None = None,
        target_node: str | None = None,
        authorized_by: str = "user",
    ) -> dict:
        if not self.cfg.get().scheduling.enabled:
            raise ValueError("task scheduling is disabled in settings")
        plugin_obj = get_plugin(plugin)
        if not self._plugin_allowed(plugin_obj):
            raise ValueError(f"plugin '{plugin}' is not enabled on this node")
        if target_node and target_node not in (self.node_id, *self._known_peer_ids()):
            raise ValueError("target node is not paired")
        spec = plugin_obj.validate(dict(spec))
        sched = self.cfg.get().scheduling
        task_id = f"t-{uuid.uuid4().hex[:12]}"
        task = {
            "id": task_id,
            "name": name.strip()[:200] or f"{plugin} task",
            "plugin": plugin,
            "spec": json.dumps(spec, default=str),
            "priority": max(0, min(int(priority), 100)),
            "source_node": self.node_id,
            "executor_node": None,
            "status": "queued",
            "progress": 0.0,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "timeout_s": int(timeout_s or sched.default_timeout_s),
            "retries": 0,
            "max_retries": int(max_retries if max_retries is not None else sched.max_retries),
            "cancel_requested": 0,
            "error": None,
            "result": None,
            "authorized_by": authorized_by,
            "resource_profile": None,
            "target_node": target_node,
        }
        self.db.insert_task(task)
        self._event(task_id, "queued", f"plugin={plugin} priority={task['priority']}")
        self._publish(task_id)
        if self._started:
            # Place immediately instead of waiting for the next tick: offload
            # latency is the difference between "the host got heavy" and "the
            # host never got heavy".
            asyncio.create_task(self._schedule_queued(), name=f"place-{task_id}")
        return self.get_task(task_id, include_events=False) or task

    async def accept_remote_task(self, payload: dict) -> dict:
        task_id = payload["task_id"]
        if self.db.get_task(task_id):
            raise ValueError("task id already exists")
        plugin_obj = get_plugin(payload["plugin"])
        if not self._plugin_allowed(plugin_obj):
            raise ValueError(f"plugin '{payload['plugin']}' is not enabled on this node")
        task = {
            "id": task_id,
            "name": str(payload["name"])[:200],
            "plugin": payload["plugin"],
            "spec": json.dumps(payload.get("spec") or {}, default=str),
            "priority": int(payload.get("priority") or 5),
            "source_node": payload["source_node"],
            "executor_node": self.node_id,
            "status": "queued",
            "progress": 0.0,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "timeout_s": int(payload.get("timeout_s") or 3600),
            "retries": 0,
            "max_retries": int(payload.get("max_retries") or 0),
            "cancel_requested": 0,
            "error": None,
            "result": None,
            "authorized_by": f"peer:{payload['source_node']}",
            "resource_profile": None,
            "target_node": None,
        }
        self.db.insert_task(task)
        self._event(task_id, "accepted", f"from {payload['source_node']}")
        self._publish(task_id)
        return self.get_task(task_id, include_events=False) or task

    def _known_peer_ids(self) -> set[str]:
        if self.peers is None:
            return set()
        return set(self.peers.peers.keys())

    # -- scheduling loop ---------------------------------------------------
    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._schedule_queued()
                self._check_timeouts()
            except Exception:
                log.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    def _local_running_count(self) -> int:
        rows = self.db.query(
            "SELECT COUNT(*) AS n FROM tasks WHERE status IN ('running','dispatching') "
            "AND executor_node = ?",
            (self.node_id,),
        )
        return int(rows[0]["n"]) if rows else 0

    def _candidate_remote(self, task: dict) -> list[tuple[str, float]]:
        if self.peers is None:
            return []
        sched = self.cfg.get().scheduling
        if not sched.allow_remote_execution:
            return []
        out: list[tuple[str, float]] = []
        for peer in self.peers.peers.values():
            if peer.status != "online" or not peer.remote_accepts_tasks:
                continue
            pressure = self.peers.pressure_for(peer.node_id)
            if pressure is None:
                continue
            score = float(pressure.get("score", 1000.0))
            # A peer that is itself under heavy load is not an offload target.
            if score >= REBALANCE_SCORE:
                continue
            if not self._fits(task, {"metrics": peer.metrics}, False):
                continue
            out.append((peer.node_id, score + 8.0))  # network/transfer overhead
        return out

    def _light_guard(self) -> str | None:
        """Why this host should not take new local work, or None when it can."""
        sched = self.cfg.get().scheduling
        if not sched.keep_host_light:
            return None
        metrics = self.bus.latest("metrics")
        if not metrics:
            return None
        cpu = float(metrics["cpu"]["percent"])
        ram = float(metrics["ram"]["percent"])
        busy: list[str] = []
        if cpu >= sched.keep_host_cpu_percent:
            busy.append(f"CPU {cpu:.0f}%")
        if ram >= sched.keep_host_ram_percent:
            busy.append(f"RAM {ram:.0f}%")
        return " / ".join(busy) or None

    def _rank_queued(self, now: float | None = None) -> list[dict]:
        """Queue order with aging: waiting work gains priority, FIFO breaks ties."""
        sched = self.cfg.get().scheduling
        now = time.time() if now is None else now
        rows = self.db.query(
            "SELECT * FROM tasks WHERE status = 'queued' AND cancel_requested = 0"
        )
        per_min = max(0.0, float(sched.rank_aging_per_min))
        cap = max(0.0, float(sched.rank_aging_max))
        for row in rows:
            waited_min = max(0.0, now - float(row["created_at"])) / 60.0
            row["effective_priority"] = round(
                float(row["priority"]) + min(cap, waited_min * per_min), 3
            )
        rows.sort(key=lambda r: (-r["effective_priority"], float(r["created_at"]), r["id"]))
        return rows[:50]

    async def _stop_preempted(self, proc: asyncio.subprocess.Process) -> None:
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    def _preempt_if_frozen(self, now: float) -> None:
        """Stop this node's own running work when the host stays over its limits.

        The task is requeued; the scheduler then places it on a healthy peer.
        Only tasks started from this node are moved, operator-pinned targets
        are respected, and every task is bounded by ``preempt_max_per_task``.
        """
        sched = self.cfg.get().scheduling
        if not sched.preempt_on_overload or not self._procs:
            self._busy_since = None
            return
        reason = self._light_guard()
        if reason is None:
            can_accept, blocked = self.overload.can_accept_tasks()
            if can_accept:
                self._busy_since = None
                return
            reason = blocked or "overload"
        if self._busy_since is None:
            self._busy_since = now
            return
        if now - self._busy_since < float(sched.preempt_after_s):
            return
        if self._preempt_one(now, reason):
            self._busy_since = now

    def _preempt_one(self, now: float, reason: str) -> bool:
        sched = self.cfg.get().scheduling
        rows = self.db.query(
            "SELECT * FROM tasks WHERE status = 'running' AND executor_node = ? "
            "AND source_node = ? ORDER BY priority ASC, started_at DESC",
            (self.node_id, self.node_id),
        )
        for row in rows:
            task_id = row["id"]
            proc = self._procs.get(task_id)
            if proc is None or row["cancel_requested"]:
                continue
            if row.get("target_node"):
                continue
            if int(row.get("preempt_count") or 0) >= int(sched.preempt_max_per_task):
                continue
            if float(row["progress"] or 0.0) >= 0.95:
                continue
            if row["started_at"] and now - float(row["started_at"]) < 5.0:
                continue
            if not self._candidate_remote(row):
                continue
            self._preempted.add(task_id)
            asyncio.create_task(self._stop_preempted(proc), name=f"preempt-{task_id}")
            self.db.update_task(
                task_id,
                status="queued",
                executor_node=None,
                started_at=None,
                progress=0.0,
                preempt_count=int(row.get("preempt_count") or 0) + 1,
            )
            self._event(
                task_id,
                "preempted",
                f"host busy ({reason}); stopped and requeued for a peer",
            )
            self._publish(task_id)
            log.info("preempted %s to protect the host (%s)", task_id, reason)
            return True
        return False

    def _fits(self, task: dict, node_ctx: dict, is_local: bool) -> bool:
        spec = json.loads(task["spec"]) if isinstance(task["spec"], str) else task.get("spec") or {}
        need_cores = int(spec.get("cpu_cores") or 0)
        need_mem = int(spec.get("memory_mb") or 0)
        if is_local:
            snap = self.bus.latest("metrics") or {}
        else:
            snap = node_ctx.get("metrics") or {}
        if not snap:
            return False
        if need_cores:
            free_cores = (snap["cpu"]["cores_logical"] or 1) * max(0.0, 1 - snap["cpu"]["percent"] / 100)
            if free_cores < need_cores:
                return False
        if need_mem:
            if snap["ram"]["available"] < need_mem * 1024 * 1024:
                return False
        return True

    async def _schedule_queued(self) -> None:
        async with self._schedule_lock:
            await self._schedule_queued_locked()

    async def _schedule_queued_locked(self) -> None:
        sched = self.cfg.get().scheduling
        if not sched.enabled:
            return
        now = time.time()
        rows = self._rank_queued(now)
        for row in rows:
            task_id = row["id"]
            if self._cooldowns.get(task_id, 0) > now:
                continue
            prepared_executor = row["executor_node"]
            if prepared_executor and prepared_executor != self.node_id:
                continue  # dispatching was already decided; waiting on accept
            if prepared_executor is None and row["source_node"] != self.node_id:
                continue  # remote task whose owner must dispatch it
            plugin_obj = get_plugin(row["plugin"])
            if not self._plugin_allowed(plugin_obj):
                continue

            if prepared_executor == self.node_id:
                chosen = self.node_id
            else:
                chosen = self._choose_executor(row)
            if chosen is None:
                continue
            if chosen == self.node_id:
                # A hard-blocked host (critical CPU/RAM or sustained thermal
                # limit) refuses even prepared work until it recovers.
                if not self.overload.can_accept_tasks()[0]:
                    continue
                if not self._fits(row, {"metrics": self.bus.latest("metrics")}, True):
                    continue
                self.db.update_task(
                    task_id, status="running", executor_node=self.node_id, started_at=time.time()
                )
                self._event(task_id, "running-locally")
                self._publish(task_id)
                runner = asyncio.create_task(self._run_local(task_id), name=f"run-{task_id}")
                self._runners[task_id] = runner
            else:
                await self._dispatch_remote(row, chosen)
        self._preempt_if_frozen(now)

    def _choose_executor(self, row: dict) -> str | None:
        sched = self.cfg.get().scheduling
        target = row.get("target_node")
        if target and target not in self._known_peer_ids() | {self.node_id}:
            return None
        if target and not (sched.rebalance_on_overload and self._target_overloaded(target)):
            return target
        light = self._light_guard()
        chosen = self._place(row)
        if target and chosen != target:
            self._note_rebalance(row, target)
        elif chosen and chosen != self.node_id and light:
            self._note_offload(row, light, chosen)
        return chosen

    def _note_offload(self, row: dict, reason: str, peer_id: str) -> None:
        if row["id"] in self._offloaded:
            return
        self._offloaded.add(row["id"])
        self._event(row["id"], "offloaded", f"host busy ({reason}); placed on {peer_id} to keep it light")
        self._publish(row["id"])

    def _target_overloaded(self, node_id: str) -> bool:
        if node_id == self.node_id:
            p = self.overload.pressure
            return bool(p.blocked_reason) or p.score >= REBALANCE_SCORE
        if self.peers is None:
            return False
        p = self.peers.pressure_for(node_id)
        if p is None:
            return False
        return float(p.get("score", 0.0)) >= REBALANCE_SCORE

    def _note_rebalance(self, row: dict, target: str) -> None:
        key = (row["id"], target)
        if key in self._rebalanced:
            return
        self._rebalanced.add(key)
        self._event(row["id"], "rebalanced", f"{target} is overloaded; placing elsewhere")
        self._publish(row["id"])

    def _place(self, row: dict) -> str | None:
        sched = self.cfg.get().scheduling
        metrics = self.bus.latest("metrics")
        if not metrics:
            return None
        local_can, reason = self.overload.can_accept_tasks()
        cpu_headroom = metrics["cpu"]["percent"] < 100 - sched.reserve_cpu_percent
        ram_headroom = metrics["ram"]["percent"] < 100 - sched.reserve_ram_percent
        local_capacity = self._local_running_count() < sched.max_concurrent_tasks
        local_ok = local_can and cpu_headroom and ram_headroom and local_capacity
        local_score = float(self.overload.pressure.score if self.overload.pressure.updated_at else 50.0)
        local_score += self._local_running_count() * 5.0
        remotes = self._candidate_remote(row)
        best_remote = min(remotes, key=lambda x: x[1]) if remotes else None
        # The host-light guard wins over policy: when this machine is already
        # busy and a healthy peer exists, new work starts there. If no peer can
        # take it, work still runs locally rather than starving.
        if best_remote and self._light_guard():
            return best_remote[0]
        if sched.policy == "local_first":
            return self.node_id if local_ok else (best_remote[0] if best_remote else None)
        if sched.policy == "remote_first":
            return best_remote[0] if best_remote else (self.node_id if local_ok else None)
        best_local = local_score if local_ok else None
        if best_remote and (best_local is None or best_remote[1] < best_local):
            return best_remote[0]
        return self.node_id if local_ok else (best_remote[0] if best_remote else None)

    async def _dispatch_remote(self, row: dict, peer_id: str) -> None:
        task_id = row["id"]
        spec = json.loads(row["spec"]) if row["spec"] else {}
        self.db.update_task(task_id, status="dispatching", executor_node=peer_id)
        self._event(task_id, "dispatching", f"to {peer_id}")
        self._publish(task_id)
        payload = {
            "task_id": task_id,
            "name": row["name"],
            "plugin": row["plugin"],
            "spec": spec,
            "priority": int(row["priority"]),
            "timeout_s": int(row["timeout_s"]),
            "max_retries": 0,  # retries are decided by the owner
            "source_node": self.node_id,
            "callback_node": self.node_id,
        }
        try:
            await self.peers.submit_task(peer_id, payload)
            self.db.update_task(task_id, status="running", started_at=time.time())
            self._event(task_id, "remote-running", peer_id)
        except Exception as exc:
            self.db.update_task(task_id, executor_node=None)
            self._event(task_id, "dispatch-failed", f"{peer_id}: {exc}")
            # Bounded like every other failure: a peer that never authorizes us
            # must not keep the task queued forever.
            self._finish(task_id, "failed", error=f"remote dispatch to {peer_id} failed: {exc}")
        self._publish(task_id)

    # -- local execution ---------------------------------------------------
    def _prepare_spec(self, spec: dict) -> dict:
        if self.storage is not None:
            return self.storage.prepare_task_spec(spec)
        return spec

    def _worker_command(self) -> list[str]:
        cmd = [sys.executable, "-m", "golem_link.tasks.worker"]
        if self.cfg.get().scheduling.nice_workers and os.name != "nt":
            nice = shutil.which("nice")
            if nice:
                cmd = [nice, "-n", "10", *cmd]
        return cmd

    def _worker_creationflags(self) -> int:
        if os.name != "nt":
            return 0
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if self.cfg.get().scheduling.nice_workers:
            flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
        return flags

    async def _run_local(self, task_id: str) -> None:
        row = self.db.get_task(task_id)
        if row is None:
            return
        task_dir = self.cfg.data_dir / "tasks" / task_id
        output_dir = task_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        spec = json.loads(row["spec"]) if row["spec"] else {}
        try:
            spec = self._prepare_spec(spec)
        except ValueError as exc:
            self._finish(task_id, "failed", error=f"invalid task spec: {exc}")
            return
        spec["allow_user_code"] = self.cfg.get().scheduling.allow_custom_python
        payload = {
            "task_id": task_id,
            "plugin": row["plugin"],
            "spec": spec,
            "output_dir": str(output_dir),
            "memory_mb": spec.get("memory_mb"),
        }
        env = dict(os.environ)
        package_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = package_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PYTHONUNBUFFERED"] = "1"
        creationflags = self._worker_creationflags()
        logs: list[str] = []
        result_data: dict | None = None
        error: str | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._worker_command(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(task_dir),
                env=env,
                creationflags=creationflags,
            )
            self._procs[task_id] = proc
            if proc.stdin:
                proc.stdin.write(json.dumps(payload, default=str).encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            stderr_task = asyncio.create_task(self._drain_stderr(proc, logs))
            started = time.time()
            profile = {"started_at": started, "cpu_peak_percent": 0.0, "memory_peak_bytes": 0}
            monitor = asyncio.create_task(self._sample_process(proc.pid, profile))
            last_owner_sync = 0.0
            timed_out = False
            try:
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    try:
                        event = json.loads(line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        logs.append(line.decode("utf-8", errors="replace").strip())
                        continue
                    kind = event.get("type")
                    if kind == "progress":
                        prog = float(event.get("value", 0.0))
                        self.db.update_task(task_id, progress=prog)
                        if event.get("message"):
                            logs.append(str(event["message"]))
                        self._publish(task_id)
                        if row["source_node"] != self.node_id and time.time() - last_owner_sync > 1.5:
                            last_owner_sync = time.time()
                            await self._sync_owner(task_id, row["source_node"], status="running", progress=prog)
                    elif kind == "log":
                        logs.append(str(event.get("message", "")))
                        if len(logs) > 500:
                            del logs[:100]
                    elif kind == "result":
                        result_data = event.get("data") or {}
                    elif kind == "error":
                        error = str(event.get("message") or "task failed")
                remaining = max(1.0, float(row["timeout_s"]) - (time.time() - started))
                try:
                    rc = await asyncio.wait_for(proc.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    timed_out = True
                    proc.kill()
                    await proc.wait()
                    rc = -9
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
                await stderr_task
            self._procs.pop(task_id, None)
            if task_id in self._preempted:
                # The scheduler already requeued this task for a peer; the
                # killed worker must not be reported as a failure or retried
                # from scratch on the same frozen host.
                self._preempted.discard(task_id)
                return
            if self.db.get_task(task_id)["cancel_requested"]:
                self._finish(task_id, "cancelled", result=result_data, logs=logs, profile=profile)
                await self._sync_owner(task_id, row["source_node"], status="cancelled", progress=1.0)
                return
            if timed_out:
                self._finish(task_id, "failed", error=f"timeout after {row['timeout_s']} s", logs=logs, profile=profile)
            elif rc == 0 and result_data is not None:
                self._finish(task_id, "completed", result=result_data, logs=logs, profile=profile)
            else:
                self._finish(
                    task_id,
                    "failed",
                    error=error or f"worker exited with code {rc}",
                    result=result_data,
                    logs=logs,
                    profile=profile,
                )
        except Exception:
            log.exception("local task %s crashed", task_id)
            self._procs.pop(task_id, None)
            self._finish(task_id, "failed", error="internal scheduler error", logs=logs)
        finally:
            self._runners.pop(task_id, None)
            final = self.db.get_task(task_id)
            if final and final["source_node"] != self.node_id:
                await self._sync_owner(
                    task_id,
                    final["source_node"],
                    status=final["status"],
                    progress=float(final["progress"]),
                    result=json.loads(final["result"]) if final["result"] else None,
                    error=final["error"],
                )

    async def _drain_stderr(self, proc: asyncio.subprocess.Process, logs: list[str]) -> None:
        if proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logs.append(f"[stderr] {text}")
                if len(logs) > 500:
                    del logs[:100]

    async def _sample_process(self, pid: int, profile: dict) -> None:
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        try:
            while True:
                try:
                    cpu = proc.cpu_percent(interval=None)
                    mem = proc.memory_info().rss
                    children = proc.children(recursive=True)
                    for child in children:
                        try:
                            mem += child.memory_info().rss
                            cpu += child.cpu_percent(interval=None)
                        except psutil.Error:
                            pass
                    profile["cpu_peak_percent"] = max(profile["cpu_peak_percent"], round(cpu, 1))
                    profile["memory_peak_bytes"] = max(profile["memory_peak_bytes"], mem)
                except psutil.Error:
                    return
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            return

    def _finish(
        self,
        task_id: str,
        status: str,
        *,
        result: dict | None = None,
        error: str | None = None,
        logs: list[str] | None = None,
        profile: dict | None = None,
    ) -> None:
        self._preempted.discard(task_id)
        row = self.db.get_task(task_id)
        if row is None:
            return
        if status == "failed":
            retries = int(row["retries"])
            if row["source_node"] == self.node_id and retries < int(row["max_retries"]):
                self.db.update_task(
                    task_id,
                    status="queued",
                    retries=retries + 1,
                    executor_node=None,
                    cancel_requested=0,
                    error=error,
                )
                self._cooldowns[task_id] = time.time() + self.cfg.get().scheduling.retry_backoff_s * (retries + 1)
                self._event(task_id, "retry-scheduled", f"attempt {retries + 2}/{int(row['max_retries']) + 1}: {error}")
                self._publish(task_id)
                return
        payload_result = dict(result or {})
        if logs:
            payload_result.setdefault("logs", logs[-300:])
        self.db.update_task(
            task_id,
            status=status,
            progress=1.0 if status == "completed" else float(row["progress"]),
            finished_at=time.time(),
            result=self._result_json(payload_result) if payload_result else None,
            error=error,
            resource_profile=json.dumps(profile, default=str) if profile else row["resource_profile"],
        )

    @staticmethod
    def _result_json(result: dict) -> str:
        raw = json.dumps(result, default=str)
        size = len(raw.encode("utf-8"))
        if size <= MAX_RESULT_BYTES:
            return raw
        return json.dumps(
            {
                "truncated": True,
                "original_bytes": size,
                "note": (
                    f"the result exceeded {MAX_RESULT_BYTES // (1024 * 1024)} MB and "
                    "was not stored in full"
                ),
            }
        )
        self._event(task_id, status, error or "")
        self._publish(task_id)

    def _check_timeouts(self) -> None:
        rows = self.db.query(
            "SELECT id, started_at, timeout_s, source_node, executor_node FROM tasks "
            "WHERE status = 'running' AND started_at IS NOT NULL"
        )
        now = time.time()
        for row in rows:
            if now - float(row["started_at"]) <= float(row["timeout_s"]) + 5:
                continue
            if row["executor_node"] == self.node_id:
                proc = self._procs.get(row["id"])
                if proc:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    continue  # _run_local will mark it failed
                self._finish(row["id"], "failed", error="timeout")
            elif row["source_node"] == self.node_id:
                self.db.update_task(
                    row["id"], status="failed", finished_at=now, error="timeout waiting for executor"
                )
                self._event(row["id"], "timeout", "executor did not finish in time")
                self._publish(row["id"])
                asyncio.create_task(self._request_remote_cancel(row["executor_node"] or "", row["id"]))

    # -- remote updates ----------------------------------------------------
    async def _sync_owner(
        self,
        task_id: str,
        owner: str,
        *,
        status: str,
        progress: float = 0.0,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        if owner == self.node_id or self.peers is None:
            return
        payload = {
            "task_id": task_id,
            "status": status,
            "progress": progress,
            "result": result,
            "error": error,
            "executor_node": self.node_id,
        }
        try:
            await self.peers.send_task_update(owner, payload)
        except Exception:
            log.debug("owner sync failed for %s", task_id, exc_info=True)

    def apply_remote_update(self, payload: dict) -> None:
        task_id = payload.get("task_id")
        row = self.db.get_task(task_id) if task_id else None
        if row is None:
            return
        executor = payload.get("executor_node")
        if row["executor_node"] not in (None, executor):
            return
        status = payload.get("status")
        if status not in ("running", "completed", "failed", "cancelled"):
            return
        fields: dict[str, Any] = {"executor_node": executor}
        if status == "running":
            fields["status"] = "running"
            fields["progress"] = float(payload.get("progress") or 0.0)
            if not row["started_at"]:
                fields["started_at"] = time.time()
        else:
            fields["status"] = status
            fields["finished_at"] = time.time()
            if status == "completed":
                fields["progress"] = 1.0
            if payload.get("result") is not None:
                result = payload["result"]
                fields["result"] = self._result_json(
                    result if isinstance(result, dict) else {"value": result}
                )
            if payload.get("error"):
                fields["error"] = payload["error"]
        if status == "failed" and row["source_node"] == self.node_id:
            retries = int(row["retries"])
            if retries < int(row["max_retries"]):
                fields.update(
                    status="queued",
                    retries=retries + 1,
                    executor_node=None,
                    error=payload.get("error"),
                )
                self._cooldowns[task_id] = time.time() + self.cfg.get().scheduling.retry_backoff_s * (retries + 1)
                self._event(task_id, "retry-scheduled", f"executor failed: {payload.get('error')}")
        self.db.update_task(task_id, **fields)
        if status != "running":
            self._event(task_id, f"remote-{status}", str(payload.get("error") or ""))
        self._publish(task_id)

    async def _request_remote_cancel(self, peer_id: str, task_id: str) -> None:
        if not peer_id or self.peers is None:
            return
        peer = self.peers.peers.get(peer_id)
        if peer is None:
            return
        try:
            await self.peers.client.post_json(
                peer.target(), "/peer/tasks/cancel", {"task_id": task_id}, timeout=10
            )
        except Exception as exc:
            log.info("remote cancel %s failed: %s", task_id, exc)

    async def cancel(self, task_id: str) -> dict:
        row = self.db.get_task(task_id)
        if row is None:
            raise ValueError("unknown task")
        if row["status"] in TERMINAL:
            return self._task_view(row)
        if row["status"] == "queued":
            self.db.update_task(task_id, status="cancelled", finished_at=time.time(), cancel_requested=1)
            self._event(task_id, "cancelled", "before start")
        elif row["executor_node"] == self.node_id:
            self.db.update_task(task_id, cancel_requested=1)
            proc = self._procs.get(task_id)
            if proc:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            else:
                self.db.update_task(task_id, status="cancelled", finished_at=time.time())
                self._event(task_id, "cancelled", "process not running")
        else:
            self.db.update_task(task_id, cancel_requested=1)
            await self._request_remote_cancel(row["executor_node"] or "", task_id)
            self._event(task_id, "cancel-requested", row["executor_node"] or "")
        self._publish(task_id)
        return self.get_task(task_id, include_events=False) or {}

    async def retry(self, task_id: str) -> dict:
        row = self.db.get_task(task_id)
        if row is None:
            raise ValueError("unknown task")
        if row["status"] not in ("failed", "cancelled"):
            raise ValueError("only failed or cancelled tasks can be retried")
        self.db.update_task(
            task_id,
            status="queued",
            executor_node=None,
            progress=0.0,
            error=None,
            result=None,
            started_at=None,
            finished_at=None,
            cancel_requested=0,
            retries=0,
        )
        self._event(task_id, "retry-manual")
        self._publish(task_id)
        return self.get_task(task_id, include_events=False) or {}

    def cancel_local_task(self, task_id: str, executor_node: str) -> dict:
        """Called by the peer server when the owner asks us to cancel."""
        row = self.db.get_task(task_id)
        if row is None:
            raise ValueError("unknown task")
        if row["executor_node"] != executor_node:
            raise ValueError("not the executor of this task")
        self.db.update_task(task_id, cancel_requested=1)
        proc = self._procs.get(task_id)
        if proc:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        elif row["status"] == "queued":
            self.db.update_task(task_id, status="cancelled", finished_at=time.time())
            self._event(task_id, "cancelled", "by owner")
            self._publish(task_id)
        return {"ok": True}

    # -- reconciliation ----------------------------------------------------
    async def reconcile_peer(self, peer_id: str) -> None:
        rows = self.db.query(
            "SELECT id, status FROM tasks WHERE executor_node = ? AND status IN ('dispatching','running')",
            (peer_id,),
        )
        if not rows or self.peers is None:
            return
        peer = self.peers.peers.get(peer_id)
        if peer is None:
            return
        for row in rows:
            try:
                state = await self.peers.client.get_json(peer.target(), f"/peer/tasks/{row['id']}", timeout=10)
            except Exception:
                continue
            if row["status"] == "running" and state.get("status") == "queued":
                continue
            self.apply_remote_update(
                {
                    "task_id": row["id"],
                    "status": state.get("status"),
                    "progress": state.get("progress", 0),
                    "result": json.loads(state["result"]) if state.get("result") else None,
                    "error": state.get("error"),
                    "executor_node": peer_id,
                }
            )
        log.info("reconciled %d task(s) with peer %s", len(rows), peer_id)
