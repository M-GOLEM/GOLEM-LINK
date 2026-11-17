"""End-to-end test: two nodes on loopback pair, connect, schedule and transfer.

Both nodes run their real peer HTTPS server, real services (monitor, scheduler,
peer manager) and real worker subprocesses; only mDNS/UDP discovery is off and
both listeners are bound to 127.0.0.1 with distinct ports.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import socket
import time
from pathlib import Path

import pytest
import uvicorn

from golem_link.app import GolemService, build_context, create_peer_app
from golem_link.networking.client import PeerConnectionError


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def wait_until(predicate, timeout: float, interval: float = 0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


class Node:
    def __init__(self, ctx, port: int, server, task: asyncio.Task, service: GolemService):
        self.ctx = ctx
        self.port = port
        self.server = server
        self.task = task
        self.service = service

    @property
    def node_id(self) -> str:
        return self.ctx.identity.node_id

    async def stop_server(self) -> None:
        self.server.should_exit = True
        await asyncio.wait_for(self.task, timeout=15)

    async def start_server(self) -> None:
        cfg = self.ctx.cfg
        config = uvicorn.Config(
            create_peer_app(self.ctx),
            host="127.0.0.1",
            port=self.port,
            ssl_certfile=str(cfg.cert_path()),
            ssl_keyfile=str(cfg.key_path()),
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
        self.server = uvicorn.Server(config)
        self.server.capture_signals = lambda: contextlib.nullcontext()  # shared loop
        self.task = asyncio.create_task(self.server.serve())
        assert await wait_until(lambda: self.server.started, 15), "peer server did not start"


async def build_node(tmp_path: Path, name: str, share_dir: Path | None = None) -> Node:
    ctx = build_context(tmp_path / name)
    port = free_port()

    def tune(cfg):
        cfg.node_name = name
        cfg.peer.port = port
        cfg.discovery.mdns_enabled = False
        cfg.discovery.beacon_enabled = False
        cfg.thresholds.sample_interval_s = 0.5

    ctx.cfg.update(tune)
    if share_dir is not None:
        ctx.storage.add_share(
            name=f"{name}-share",
            path=str(share_dir),
            read_only=False,
            allow_delete=True,
        )
    config = uvicorn.Config(
        create_peer_app(ctx),
        host="127.0.0.1",
        port=port,
        ssl_certfile=str(ctx.cfg.cert_path()),
        ssl_keyfile=str(ctx.cfg.key_path()),
        log_level="warning",
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    server.capture_signals = lambda: contextlib.nullcontext()
    task = asyncio.create_task(server.serve())
    assert await wait_until(lambda: server.started, 15), "peer server did not start"
    service = GolemService(ctx)
    await service.start()
    return Node(ctx, port, server, task, service)


@pytest.mark.asyncio
async def test_two_node_cluster_end_to_end(tmp_path: Path):
    share_a = tmp_path / "shared-a"
    share_b = tmp_path / "shared-b"
    share_a.mkdir()
    share_b.mkdir()
    (share_b / "docs").mkdir()
    (share_b / "docs" / "readme.txt").write_text("hello from node B\n", encoding="utf-8")

    node_a = await build_node(tmp_path, "node-a", share_a)
    node_b = await build_node(tmp_path, "node-b", share_b)
    try:
        a_id, b_id = node_a.node_id, node_b.node_id
        assert a_id != b_id

        # --- pairing: A initiates with a manual LAN address fallback ----------
        started = await node_a.ctx.peers.begin_pairing(None, f"127.0.0.1:{node_b.port}")
        assert started["code"].isdigit() and len(started["code"]) == 6
        request = await wait_until(
            lambda: bool(node_b.ctx.db.list_pair_requests()), 5
        )
        assert request
        pending = node_b.ctx.db.list_pair_requests()[0]
        assert pending["node_id"] == a_id
        assert pending["address"] == "127.0.0.1"
        assert pending["peer_port"] == node_a.port
        assert pending["code"] == started["code"]  # human-compared code agrees

        approval = node_b.ctx.peers.approve_pair(pending["id"], True)
        assert approval["status"] == "approved"
        polled = await node_a.ctx.peers.poll_pairing(started["request_id"])
        assert polled["status"] == "approved"

        # A fresh request against the already-paired peer reports it clearly
        # instead of failing on the missing ephemeral key.
        with pytest.raises(PeerConnectionError, match="already paired"):
            await node_a.ctx.peers.begin_pairing(None, f"127.0.0.1:{node_b.port}")

        # --- both directions connect with pinned TLS and exchange metrics -----
        async def both_online() -> bool:
            a_peer = node_a.ctx.peers.peers.get(b_id)
            b_peer = node_b.ctx.peers.peers.get(a_id)
            return bool(a_peer and b_peer and a_peer.status == "online" and b_peer.status == "online")

        deadline = time.time() + 25
        while time.time() < deadline:
            if await both_online():
                break
            await asyncio.sleep(0.2)
        assert await both_online(), "nodes did not both come online"

        a_view = node_a.ctx.peers.peers[b_id]
        b_view = node_b.ctx.peers.peers[a_id]
        assert a_view.port == node_b.port
        assert b_view.port == node_a.port
        assert a_view.fingerprint == node_b.ctx.identity.fingerprint
        assert b_view.fingerprint == node_a.ctx.identity.fingerprint
        assert await wait_until(lambda: a_view.metrics is not None and b_view.metrics is not None, 15)
        assert a_view.latency_ms is not None and a_view.latency_ms >= 0

        # --- pairing records the initiator's own metadata, not the peer's -----
        own = node_a.ctx.identity.info()
        stored = node_b.ctx.db.get_peer(a_id)
        assert stored is not None
        assert stored["os"] == own["os"]
        assert stored["hostname"] == own["hostname"]
        assert stored["cpu_model"] == own["cpu_model"]
        assert stored["allow_tasks"] == 1  # task sharing on by default once paired
        assert node_a.ctx.db.get_peer(b_id)["allow_tasks"] == 1

        # --- a stale row (from an older build) is repaired on reconnect -------
        node_b.ctx.db.execute(
            "UPDATE peers SET os = 'Plan9', hostname = 'WRONG-HOST', cpu_model = 'fake' "
            "WHERE node_id = ?",
            (a_id,),
        )
        await node_b.ctx.peers._connect_once(b_view)
        healed = node_b.ctx.db.get_peer(a_id)
        assert healed["os"] == own["os"]
        assert healed["hostname"] == own["hostname"]
        assert healed["cpu_model"] == own["cpu_model"]

        # --- task submission is refused once the operator withdraws consent ---
        node_b.ctx.peers.set_peer_permission(a_id, allow_tasks=False)
        assert await wait_until(
            lambda: not node_a.ctx.peers.peers[b_id].remote_accepts_tasks, 10
        ), "permission change was not pushed to the owner"
        with pytest.raises(ValueError):
            await node_a.ctx.peers.submit_task(
                b_id,
                {
                    "task_id": "t-denied",
                    "name": "denied",
                    "plugin": "disk_benchmark",
                    "spec": {"size_mb": 1},
                    "priority": 5,
                    "timeout_s": 60,
                    "max_retries": 0,
                    "source_node": a_id,
                    "callback_node": a_id,
                },
            )

        # --- remote task: forced to the remote node, real subprocess ---------
        node_b.ctx.peers.set_peer_permission(a_id, allow_tasks=True, allow_storage=True)
        assert await wait_until(
            lambda: node_a.ctx.peers.peers[b_id].remote_accepts_tasks, 10
        ), "permission change was not pushed to the owner"

        # --- remote operations: diagnostics and logs over the peer channel ----
        node_b.ctx.peers.set_peer_permission(a_id, allow_control=True)
        b_target = node_a.ctx.peers.peers[b_id].target()
        diagnostics = await node_a.ctx.peers.client.get_json(
            b_target, "/peer/ops/diagnostics", timeout=15
        )
        assert diagnostics["node_id"] == b_id
        assert diagnostics["version"]
        assert diagnostics["service"]["mode"]
        logs = await node_a.ctx.peers.client.get_json(
            b_target, "/peer/ops/logs?lines=5", timeout=15
        )
        assert isinstance(logs["lines"], list) and len(logs["lines"]) <= 5

        node_b.ctx.peers.set_peer_permission(a_id, allow_control=False)
        with pytest.raises(PeerConnectionError, match="403"):
            await node_a.ctx.peers.client.get_json(
                b_target, "/peer/ops/diagnostics", timeout=15
            )
        node_a.ctx.cfg.update(lambda c: setattr(c.scheduling, "policy", "remote_first"))
        assert await wait_until(
            lambda: (node_a.ctx.peers.pressure_for(b_id) or {}).get("score", 1000) < 1000, 15
        )
        task = await node_a.ctx.scheduler.submit_local(
            name="cluster benchmark",
            plugin="disk_benchmark",
            spec={"size_mb": 1},
            timeout_s=120,
            max_retries=0,
        )
        task_id = task["id"]

        async def task_done() -> dict:
            return node_a.ctx.scheduler.get_task(task_id) or {}

        deadline = time.time() + 90
        final: dict = {}
        while time.time() < deadline:
            final = await task_done()
            if final.get("status") in ("completed", "failed", "cancelled"):
                break
            await asyncio.sleep(0.25)
        assert final.get("status") == "completed", final
        assert final["executor_node"] == b_id, final
        result = final["result"]
        assert result["size_bytes"] == 1024 * 1024
        assert len(result["sha256"]) == 64
        assert result["write_mbps"] > 0
        remote_row = node_b.ctx.db.get_task(task_id)
        assert remote_row is not None and remote_row["status"] == "completed"
        assert remote_row["source_node"] == a_id

        # --- rebalance: a pinned task leaves a node that is overloaded --------
        from golem_link.overload import NodePressure

        node_a.ctx.cfg.update(lambda c: setattr(c.scheduling, "policy", "balanced"))
        original_evaluate = node_a.ctx.overload.evaluate
        original_can_accept = node_a.ctx.overload.can_accept_tasks
        node_a.ctx.overload.pressure = NodePressure(
            node_id=a_id,
            cpu_percent=100.0,
            ram_percent=100.0,
            blocked_reason="cpu critical",
            score=1000.0,
        )
        node_a.ctx.overload.evaluate = lambda *args, **kwargs: None
        node_a.ctx.overload.can_accept_tasks = lambda: (False, "cpu critical")
        try:
            pinned = await node_a.ctx.scheduler.submit_local(
                name="pinned but rebalanced",
                plugin="disk_benchmark",
                spec={"size_mb": 1},
                timeout_s=60,
                max_retries=0,
                target_node=a_id,
            )
            pinned_id = pinned["id"]
            deadline = time.time() + 60
            final = {}
            while time.time() < deadline:
                final = node_a.ctx.scheduler.get_task(pinned_id) or {}
                if final.get("status") in ("completed", "failed", "cancelled"):
                    break
                await asyncio.sleep(0.25)
            assert final.get("status") == "completed", final
            assert final["executor_node"] == b_id, final
            assert "rebalanced" in [e["event"] for e in final.get("events", [])]
        finally:
            node_a.ctx.overload.evaluate = original_evaluate
            node_a.ctx.overload.can_accept_tasks = original_can_accept

        # --- upload A -> B ----------------------------------------------------
        payload_file = tmp_path / "payload.bin"
        blob = os.urandom(3 * 1024 * 1024 + 12345)
        payload_file.write_bytes(blob)
        digest = hashlib.sha256(blob).hexdigest()
        share_b_id = node_b.ctx.storage.list_share_views()[0]["id"]
        upload = await node_a.ctx.transfers.start_upload(
            b_id, share_b_id, "incoming/payload.bin", str(payload_file)
        )

        def upload_view() -> dict:
            return node_a.ctx.transfers.get_transfer(upload["id"]) or {}

        deadline = time.time() + 60
        while time.time() < deadline:
            if upload_view().get("status") in ("completed", "failed", "cancelled"):
                break
            await asyncio.sleep(0.2)
        view = upload_view()
        assert view["status"] == "completed", view
        landed = share_b / "incoming" / "payload.bin"
        assert landed.is_file()
        assert hashlib.sha256(landed.read_bytes()).hexdigest() == digest
        assert not (share_b / "incoming" / "payload.bin.glpart").exists()

        # --- download B -> A --------------------------------------------------
        download = await node_a.ctx.transfers.start_download(b_id, share_b_id, "docs/readme.txt")

        def download_view() -> dict:
            return node_a.ctx.transfers.get_transfer(download["id"]) or {}

        deadline = time.time() + 30
        while time.time() < deadline:
            if download_view().get("status") in ("completed", "failed", "cancelled"):
                break
            await asyncio.sleep(0.2)
        view = download_view()
        assert view["status"] == "completed", view
        target = (
            node_a.ctx.cfg.data_dir / "downloads" / b_id / share_b_id / "docs" / "readme.txt"
        )
        assert target.read_text(encoding="utf-8") == "hello from node B\n"

        # --- reconnection after the peer server restarts ----------------------
        await node_b.stop_server()
        assert await wait_until(
            lambda: node_a.ctx.peers.peers[b_id].status != "online", 30
        ), "A kept reporting B online after B's server stopped"
        await node_b.start_server()
        assert await wait_until(
            lambda: node_a.ctx.peers.peers[b_id].status == "online", 45
        ), "A did not reconnect after B restarted"
        assert node_a.ctx.peers.peers[b_id].secret  # pairing survived the outage
    finally:
        for node in (node_a, node_b):
            try:
                await node.service.stop()
            finally:
                try:
                    await node.stop_server()
                except Exception:
                    pass
