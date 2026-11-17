import json
from pathlib import Path

import pytest

from golem_link import remote_maintenance as remote
from golem_link import remote_ops
from golem_link.config import ConfigManager
from golem_link.db import Database
from golem_link.events import EventBus
from golem_link.networking.peer_manager import PeerManager, PeerRuntime
from golem_link.tasks.engine import MAX_RESULT_BYTES, Scheduler
from golem_link.update import UpdateManager


def _cfg(tmp_path):
    cfg = ConfigManager(tmp_path / "data")
    cfg.ensure_dirs()
    return cfg


def _manager(tmp_path, platform="linux", bus=None):
    return UpdateManager(
        _cfg(tmp_path), bus or EventBus(), None, platform=platform
    )


# -- rollback ---------------------------------------------------------------
def test_rollback_needs_a_backup(tmp_path):
    manager = _manager(tmp_path)
    with pytest.raises(ValueError, match="no rollback backup"):
        manager.rollback()


def test_rollback_linux_needs_the_helper(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "revert_plan", lambda: {"available": True, "path": "/opt/golem-link.previous"})
    monkeypatch.setattr(remote, "helper_ready", lambda: False)
    with pytest.raises(ValueError, match="root helper"):
        manager.rollback()


def test_rollback_refuses_an_old_helper(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "revert_plan", lambda: {"available": True, "path": "/opt/golem-link.previous"})
    monkeypatch.setattr(remote, "helper_ready", lambda: True)
    monkeypatch.setattr(remote, "helper_supports", lambda action: False)
    with pytest.raises(ValueError, match="predates rollback"):
        manager.rollback()


def test_rollback_writes_a_bounded_helper_request(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "revert_plan", lambda: {"available": True, "path": "/opt/golem-link.previous"})
    monkeypatch.setattr(remote, "helper_ready", lambda: True)
    monkeypatch.setattr(remote, "helper_supports", lambda action: action == "rollback")
    result = manager.rollback()
    assert result["ok"] is True and result["scheduled"] is True
    payload = json.loads(remote.request_path(manager.work_dir).read_text())
    assert payload["action"] == "rollback"
    assert len(payload["nonce"]) == 32
    # A rollback request names no command and no path the helper would run.
    assert set(payload) == {"action", "nonce"}
    assert any(item["event"] == "rollback" for item in manager.history)


def test_linux_restart_is_a_scheduled_exit(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    calls = []
    monkeypatch.setattr(manager, "_exit_soon", lambda delay=1.2: calls.append(delay))
    result = manager.restart_agent()
    assert result["scheduled"] is True
    assert result["mode"] == "systemd-restart"
    assert calls  # the agent asked itself to exit under Restart=always


# -- diagnostics ------------------------------------------------------------
class _FakeIdentity:
    node_id = "gl-self"


class _FakePeers:
    peers = {}


class _FakeCtx:
    def __init__(self, tmp_path, platform="linux"):
        self.cfg = _cfg(tmp_path)
        self.db = Database(self.cfg.db_path())
        self.bus = EventBus()
        self.identity = _FakeIdentity()
        self.updates = UpdateManager(self.cfg, self.bus, self.identity, platform=platform)
        self.peers = _FakePeers()


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_diagnostics_have_stable_shape(tmp_path, platform):
    ctx = _FakeCtx(tmp_path, platform)
    data = remote_ops.collect(ctx)
    for key in (
        "node_id", "version", "platform", "service", "tasks", "peers",
        "update", "disk", "data_dir", "collected_at",
    ):
        assert key in data
    assert data["tasks"]["running"] == 0
    assert data["update"]["current_version"]
    assert data["service"]["mode"] in ("systemd", "windows-service", "process")


def test_log_tail_is_bounded(tmp_path):
    ctx = _FakeCtx(tmp_path)
    log_dir = ctx.cfg.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "golem-link.log").write_text(
        "\n".join(f"line {i}" for i in range(500)) + "\n", encoding="utf-8"
    )
    lines = remote_ops.tail_log(ctx, 10)
    assert len(lines) == 10
    assert lines[-1] == "line 499"


# -- capabilities and latency ----------------------------------------------
def test_capabilities_carry_every_grant():
    peer = PeerRuntime(
        node_id="gl-peer", name="peer", address="10.0.0.2", port=8443,
        secret=b"x", cert_pem="",
    )
    PeerManager.apply_capabilities(
        peer,
        {
            "accepts_tasks": True,
            "accepts_storage": False,
            "serves_updates": True,
            "accepts_control": True,
        },
    )
    assert peer.remote_accepts_tasks is True
    assert peer.remote_accepts_storage is False
    assert peer.remote_allows_updates is True
    assert peer.remote_allows_control is True


# -- heavy task transport ---------------------------------------------------
@pytest.mark.asyncio
async def test_large_task_update_skips_the_websocket():
    sent = []
    posted = []

    class _WS:
        async def send_json(self, payload):
            sent.append(payload)

    class _Client:
        async def post_json(self, target, path, payload, timeout=None):
            posted.append((path, timeout))
            return {"ok": True}

    class _Self:
        WS_TASK_UPDATE_LIMIT = PeerManager.WS_TASK_UPDATE_LIMIT

    peer = PeerRuntime(
        node_id="gl-peer", name="peer", address="10.0.0.2", port=8443,
        secret=b"x", cert_pem="", status="online",
    )
    peer.ws = _WS()

    class _Peers(dict):
        pass

    fake = _Self()
    fake.peers = {"gl-peer": peer}
    fake.client = _Client()
    big = {"result": {"blob": "x" * (PeerManager.WS_TASK_UPDATE_LIMIT + 10)}}
    assert await PeerManager.send_task_update(fake, "gl-peer", big) is True
    assert sent == []  # never handed to the websocket
    assert posted and posted[0][0] == "/peer/tasks/update"

    small = {"status": "running", "progress": 0.5}
    assert await PeerManager.send_task_update(fake, "gl-peer", small) is True
    assert sent and sent[0]["type"] == "task_update"
    assert len(posted) == 1


def test_oversized_result_is_marked_not_silently_cut():
    raw = Scheduler._result_json({"blob": "x" * (MAX_RESULT_BYTES + 100)})
    data = json.loads(raw)
    assert data["truncated"] is True
    assert data["original_bytes"] > MAX_RESULT_BYTES
    small = Scheduler._result_json({"value": 1})
    assert json.loads(small) == {"value": 1}
