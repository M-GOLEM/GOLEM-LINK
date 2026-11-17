"""Regression tests for behaviours that used to fail in the field.

Covers alert de-duplication, validation-error serialization, idempotent pairing
polling, reconnect hysteresis and bounded remote-dispatch failures.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from golem_link.app import build_context
from golem_link.config import ConfigManager, ScreenConfig, ThresholdConfig
from golem_link.db import Database
from golem_link.input_sharing.deskflow import InputSharingManager
from golem_link.networking.peer_manager import PeerRuntime


def test_open_alert_dedupe_survives_ack(tmp_path: Path):
    db = Database(tmp_path / "alerts.db")
    first = db.add_alert("gl-a", "warning", "cpu", "CPU high", 81.0, 80.0, "cpu-warn-50")
    assert db.has_open_alert("cpu-warn-50")
    assert not db.has_open_alert("cpu-warn-99")

    db.ack_alert(first)
    assert not db.has_open_alert("cpu-warn-50")
    # A fresh occurrence after acknowledgement is recorded and deduped again.
    db.add_alert("gl-a", "warning", "cpu", "CPU high", 82.0, 80.0, "cpu-warn-50")
    assert db.has_open_alert("cpu-warn-50")
    assert len(db.list_alerts(only_open=True)) == 1
    db.close()


def test_validation_detail_is_json_serializable():
    from golem_link.api.dashboard import _validation_detail

    with pytest.raises(ValidationError) as caught:
        ThresholdConfig(cpu_warn_percent=90.0, cpu_crit_percent=80.0)
    detail = _validation_detail(caught.value)
    assert detail and "cpu_crit_percent" in detail[0]["loc"]
    # The validator context holds the original ValueError; it must be stripped.
    json.dumps(detail)
    assert "ctx" not in detail[0]


@pytest.mark.asyncio
async def test_pairing_poll_is_idempotent_and_expires(tmp_path: Path):
    ctx = build_context(tmp_path / "node")
    peers = ctx.peers
    try:
        peers._complete_outgoing("req-ok", {"request_id": "req-ok", "status": "approved"})
        assert (await peers.poll_pairing("req-ok"))["status"] == "approved"
        # A second dashboard poll observes the same terminal answer.
        assert (await peers.poll_pairing("req-ok"))["status"] == "approved"

        peers._completed_out["req-old"] = (
            time.time() - 1,
            {"request_id": "req-old", "status": "denied"},
        )
        with pytest.raises(ValueError):
            await peers.poll_pairing("req-old")
        assert "req-old" not in peers._completed_out

        with pytest.raises(ValueError):
            await peers.poll_pairing("req-never-seen")
    finally:
        ctx.db.close()


@pytest.mark.asyncio
async def test_transient_reconnect_keeps_peer_online(tmp_path: Path):
    ctx = build_context(tmp_path / "node")
    peers = ctx.peers
    peer = PeerRuntime(
        node_id="gl-test-peer",
        name="test-peer",
        address="127.0.0.1",
        port=1,
        secret=b"\x00" * 32,
        cert_pem="",
        fingerprint="aa",
        status="online",
        last_seen=time.time(),
    )
    peers.peers[peer.node_id] = peer
    try:
        peers._note_connection_failure(peer)
        assert peer.status == "online", "a single reconnect must not mark a live peer offline"
        peers._note_connection_failure(peer)
        assert peer.status == "offline", "consecutive failures must mark the peer offline"

        # Silence longer than the discovery window is conclusive on its own.
        peer.status = "online"
        peer.failures = 0
        peer.last_seen = time.time() - ctx.cfg.get().discovery.offline_after_s - 1
        peers._note_connection_failure(peer)
        assert peer.status == "offline"

        # Any message on the outbound session clears the failure streak.
        peer.failures = 1
        await peers._handle_peer_message(peer, {"type": "pong", "ts": time.time()})
        assert peer.failures == 0
    finally:
        ctx.db.close()


@pytest.mark.asyncio
async def test_transfer_start_waits_for_reconnect(tmp_path: Path):
    ctx = build_context(tmp_path / "node")
    peers = ctx.peers
    peer = PeerRuntime(
        node_id="gl-test-peer",
        name="test-peer",
        address="127.0.0.1",
        port=1,
        secret=b"\x00" * 32,
        cert_pem="",
        fingerprint="aa",
        status="offline",
        last_seen=time.time(),
    )
    peers.peers[peer.node_id] = peer
    ctx.transfers.peers = peers
    try:
        async def reconnect() -> None:
            await asyncio.sleep(0.3)
            peer.status = "online"

        reconnecting = asyncio.create_task(reconnect())
        started = time.monotonic()
        assert await asyncio.wait_for(ctx.transfers._wait_for_peer(peer.node_id), 3) is peer
        assert time.monotonic() - started >= 0.2
        await reconnecting

        # A peer that has been silent past the discovery window fails at once.
        peer.status = "offline"
        peer.last_seen = time.time() - ctx.cfg.get().discovery.offline_after_s - 1
        started = time.monotonic()
        with pytest.raises(ValueError):
            await ctx.transfers._wait_for_peer(peer.node_id)
        assert time.monotonic() - started < 1.0
    finally:
        ctx.db.close()


def test_input_sharing_generates_reconnecting_scripts(tmp_path: Path):
    cfg = ConfigManager(tmp_path / "data")
    cfg.update(
        lambda c: setattr(
            c.input_sharing,
            "screens",
            [
                ScreenConfig(node_id="gl-a", name="Laptop", side="left", width=1920, height=1080),
                ScreenConfig(node_id="gl-b", name="Desktop", side="right", width=2560, height=1440),
            ],
        )
    )
    cfg.update(lambda c: setattr(c.input_sharing, "server_node_id", "gl-a"))
    manager = InputSharingManager(cfg)

    result = manager.generate()
    assert result["auto_reconnect"] is True
    assert set(result["scripts"]) == {"linux_server", "linux_client", "windows_server", "windows_client"}

    conf = Path(result["server_config_path"]).read_text(encoding="utf-8")
    assert "Laptop:" in conf and "Desktop:" in conf
    assert "right = Desktop" in conf and "left = Laptop" in conf
    assert "clipboardSharing = true" in conf

    server_sh = Path(result["scripts"]["linux_server"])
    raw = server_sh.read_bytes()
    assert b"\r\n" not in raw  # must stay runnable on Linux
    text = raw.decode("utf-8")
    assert "while true" in text and "sleep 3" in text
    assert "deskflow-server" in text and "Laptop" in text

    client_sh = Path(result["scripts"]["linux_client"]).read_text(encoding="utf-8")
    assert 'SERVER="${1:-Laptop.local}"' in client_sh
    assert "deskflow-client" in client_sh and "Desktop" in client_sh

    client_ps1 = Path(result["scripts"]["windows_client"]).read_text(encoding="utf-8")
    assert "while ($true)" in client_ps1 and "deskflow-client.exe" in client_ps1

    cfg.update(lambda c: setattr(c.input_sharing, "auto_reconnect", False))
    plain = manager.generate()
    assert plain["auto_reconnect"] is False
    assert "while true" not in Path(plain["scripts"]["linux_server"]).read_text(encoding="utf-8")

    cfg.update(
        lambda c: setattr(
            c.input_sharing,
            "screens",
            [
                ScreenConfig(node_id="gl-a", name="Laptop", side="left"),
                ScreenConfig(node_id="gl-a", name="Duplicate", side="right"),
            ],
        )
    )
    with pytest.raises(ValueError):
        manager.generate()


@pytest.mark.asyncio
async def test_dispatch_failure_is_bounded(tmp_path: Path):
    ctx = build_context(tmp_path / "node")
    ctx.cfg.update(lambda c: setattr(c.scheduling, "retry_backoff_s", 0.01))

    class RefusingPeers:
        async def submit_task(self, peer_id: str, payload: dict) -> None:
            raise RuntimeError("task execution is not authorized by the remote node")

    ctx.scheduler.peers = RefusingPeers()
    try:
        task = await ctx.scheduler.submit_local(
            name="refused",
            plugin="disk_benchmark",
            spec={"size_mb": 1},
            max_retries=2,
        )
        row = ctx.db.get_task(task["id"])
        await ctx.scheduler._dispatch_remote(row, "gl-fake-peer")

        after = ctx.db.get_task(task["id"])
        assert after["status"] == "queued"  # first failure schedules a retry
        assert after["retries"] == 1
        assert after["executor_node"] is None
        assert "not authorized" in (after["error"] or "")
        assert task["id"] in ctx.scheduler._cooldowns

        # Exhaust the retry budget: the task must end failed, never loop forever.
        for _ in range(2):
            ctx.scheduler._cooldowns.pop(task["id"], None)
            ctx.db.update_task(task["id"], status="queued", executor_node=None)
            await ctx.scheduler._dispatch_remote(ctx.db.get_task(task["id"]), "gl-fake-peer")
        final = ctx.db.get_task(task["id"])
        assert final["status"] == "failed"
        assert final["retries"] == 2
    finally:
        ctx.db.close()
