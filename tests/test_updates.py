import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from golem_link.config import ConfigManager
from golem_link.db import Database
from golem_link.events import EventBus
from golem_link.update import (
    UpdateManager,
    is_newer,
    normalize_manifest,
    parse_version,
    select_artifact,
    verify_artifact,
)
from golem_link.version import VERSION

NEWER = "99.0.0"


def _manifest(version=NEWER, blob=b"installer-bytes", channel="stable"):
    return {
        "version": version,
        "channel": channel,
        "notes": "test release",
        "published_at": "2026-11-17T00:00:00Z",
        "artifacts": {
            "windows": {
                "file": "GOLEM-LINK-Setup.exe",
                "sha256": hashlib.sha256(blob).hexdigest(),
                "size": len(blob),
            },
            "linux": {
                "file": "GOLEM-LINK-Debian13.run",
                "sha256": hashlib.sha256(blob).hexdigest(),
                "size": len(blob),
            },
        },
    }


def _cfg(tmp_path):
    cfg = ConfigManager(tmp_path / "data")
    cfg.ensure_dirs()
    return cfg


def _local_release(tmp_path, cfg, blob=b"installer-bytes", manifest=None):
    out = cfg.data_dir / "updates"
    out.mkdir(parents=True, exist_ok=True)
    (out / "GOLEM-LINK-Setup.exe").write_bytes(blob)
    (out / "GOLEM-LINK-Debian13.run").write_bytes(blob)
    (out / "manifest.json").write_text(json.dumps(manifest or _manifest(blob=blob)))
    return out


class _FakePeer:
    status = "online"
    remote_allows_updates = True
    remote_allows_control = True
    node_id = "gl-peer"
    name = "peer"

    def target(self):
        return "peer-target"


class _FakeClient:
    def __init__(self, manifest, blob):
        self.manifest = manifest
        self.blob = blob
        self.downloaded = []

    async def get_json(self, target, path, timeout=None):
        assert path == "/peer/update/manifest"
        return {"manifest": self.manifest}

    async def download_to(self, target, path, dest, *, expected_sha256=None, progress=None, **kwargs):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.blob)
        self.downloaded.append(path)
        if progress is not None:
            progress(len(self.blob), len(self.blob))
        assert hashlib.sha256(self.blob).hexdigest() == expected_sha256
        return {"path": str(dest), "size": len(self.blob), "sha256": expected_sha256}


class _FakePeers:
    def __init__(self, manifest, blob):
        self.peers = {"gl-peer": _FakePeer()}
        self.client = _FakeClient(manifest, blob)


def test_version_ordering():
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("v2.0.10") == (2, 0, 10)
    assert parse_version("1.2") is None
    assert is_newer("1.2.0", "1.1.0")
    assert not is_newer("1.1.0", "1.1.0")
    assert not is_newer("1.0.9", "1.1.0")
    assert not is_newer("not-a-version", "1.1.0")


def test_manifest_rejects_unsafe_entries():
    with pytest.raises(ValueError):
        normalize_manifest({"version": "1.2.0", "artifacts": {}})
    with pytest.raises(ValueError):
        normalize_manifest({"version": "1.2", "artifacts": {"linux": {}}})
    with pytest.raises(ValueError):
        normalize_manifest(
            {
                "version": "1.2.0",
                "artifacts": {"linux": {"file": "../evil.run", "sha256": "0" * 64}},
            }
        )
    with pytest.raises(ValueError):
        normalize_manifest(
            {
                "version": "1.2.0",
                "artifacts": {"linux": {"file": "ok.run", "sha256": "abc"}},
            }
        )
    clean = normalize_manifest(_manifest())
    assert select_artifact(clean, "linux")["file"] == "GOLEM-LINK-Debian13.run"
    assert select_artifact(clean, "plan9") is None


def test_local_check_download_and_apply_stage(tmp_path):
    cfg = _cfg(tmp_path)
    blob = b"local-installer"
    _local_release(tmp_path, cfg, blob=blob)
    manager = UpdateManager(cfg, EventBus(), platform="linux")
    status = asyncio.run(manager.check())
    assert status["state"] == "available"
    assert status["latest_version"] == NEWER
    status = asyncio.run(manager.download())
    assert status["state"] == "ready"
    staged = Path(status["artifact"]["staged"])
    assert staged.is_file()
    assert verify_artifact(staged, status["artifact"]["sha256"], status["artifact"]["size"])
    status = asyncio.run(manager.apply())
    # Linux never self-elevates: the update is verified and waits for one sudo.
    assert status["state"] == "ready"
    assert status["install"]["mode"] == "operator-sudo"
    assert status["install"]["requires_operator"] is True
    assert "sudo" in status["install"]["command"]
    assert "--upgrade" in status["install"]["command"]


def test_tampered_artifact_is_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    _local_release(tmp_path, cfg, blob=b"the-real-bytes")
    # Replace the file after the manifest was written.
    (cfg.data_dir / "updates" / "GOLEM-LINK-Debian13.run").write_bytes(b"tampered")
    manager = UpdateManager(cfg, EventBus(), platform="linux")
    asyncio.run(manager.check())
    status = asyncio.run(manager.download())
    assert status["state"] == "error"
    assert "hash" in (status["error"] or "").lower()
    assert status["artifact"]["staged"] is None


def test_peer_manifest_and_verified_download(tmp_path):
    cfg = _cfg(tmp_path)
    blob = b"peer-installer"
    manifest = normalize_manifest(_manifest(blob=blob))
    manager = UpdateManager(cfg, EventBus(), platform="windows")
    manager.install_dir = lambda: tmp_path / "Program Files" / "GOLEM LINK"  # type: ignore[method-assign]
    manager.peers = _FakePeers(manifest, blob)
    status = asyncio.run(manager.check())
    assert status["state"] == "available"
    assert status["artifact"]["source"] == "peer"
    assert status["artifact"]["source_node"] == "gl-peer"
    status = asyncio.run(manager.download())
    assert status["state"] == "ready"
    assert manager.peers.client.downloaded == ["/peer/update/artifact/GOLEM-LINK-Setup.exe"]
    assert verify_artifact(Path(status["artifact"]["staged"]), status["artifact"]["sha256"])


def test_peer_without_grant_is_ignored(tmp_path):
    cfg = _cfg(tmp_path)
    peers = _FakePeers(normalize_manifest(_manifest()), b"peer-installer")
    peers.peers["gl-peer"].remote_allows_updates = False
    manager = UpdateManager(cfg, EventBus(), platform="linux")
    manager.peers = peers
    status = asyncio.run(manager.check())
    assert status["state"] == "up_to_date"
    assert peers.client.downloaded == []


def test_other_channel_is_ignored(tmp_path):
    cfg = _cfg(tmp_path)
    _local_release(tmp_path, cfg, manifest=_manifest(channel="nightly"))
    manager = UpdateManager(cfg, EventBus(), platform="linux")
    status = asyncio.run(manager.check())
    assert status["state"] == "up_to_date"


def test_windows_install_plan_and_detached_launch(tmp_path):
    cfg = _cfg(tmp_path)
    _local_release(tmp_path, cfg)
    launched = {}

    def launcher(staged, script):
        launched["staged"] = staged
        launched["script"] = script

    manager = UpdateManager(
        cfg, EventBus(), platform="windows", installer_launcher=launcher
    )
    manager.install_dir = lambda: tmp_path / "Program Files" / "GOLEM LINK"  # type: ignore[method-assign]
    asyncio.run(manager.check())
    asyncio.run(manager.download())
    status = asyncio.run(manager.apply())
    assert status["state"] == "installing"
    assert launched["staged"] == Path(status["artifact"]["staged"])
    script = launched["script"].read_text(encoding="utf-8")
    assert "/Update" in script and "/Silent" in script
    assert "GolemLinkUpdate" in script
    assert "/Update" in status["install"]["command"]
    assert status["install"]["requires_operator"] is False


def test_interrupted_install_is_resolved(tmp_path):
    cfg = _cfg(tmp_path)
    work = cfg.data_dir / "updates"
    work.mkdir(parents=True, exist_ok=True)
    (work / "state.json").write_text(
        json.dumps({"state": "installing", "latest": {"version": VERSION}, "artifact": None})
    )
    manager = UpdateManager(cfg, EventBus(), platform="windows")
    assert manager.state == "updated"

    (work / "state.json").write_text(
        json.dumps({"state": "installing", "latest": {"version": "99.0.0"}, "artifact": None})
    )
    manager = UpdateManager(cfg, EventBus(), platform="windows")
    assert manager.state == "error"
    assert "did not finish" in (manager.error or "")


def test_linux_restart_uses_systemd(tmp_path, monkeypatch):
    manager = UpdateManager(_cfg(tmp_path), EventBus(), platform="linux")
    exits: list[float] = []
    # Never let a unit test schedule a real process exit.
    monkeypatch.setattr(manager, "_exit_soon", lambda delay=1.2: exits.append(delay))
    result = manager.restart_agent()
    assert result["ok"] is True
    assert result["scheduled"] is True
    assert result["mode"] == "systemd-restart"
    assert result["requires_operator"] is False
    assert exits and exits[0] > 0


def test_peer_control_defaults_are_denied(tmp_path):
    db = Database(_cfg(tmp_path).db_path())
    db.upsert_peer(
        {
            "node_id": "gl-peer",
            "name": "peer",
            "public_key": "k",
            "fingerprint": "f",
            "cert_pem": "c",
            "secret_hex": "00",
            "paired_at": "now",
        }
    )
    row = db.get_peer("gl-peer")
    assert row["allow_updates"] == 1
    assert row["allow_control"] == 0
    db.close()


def test_update_defaults_are_autopilot(tmp_path):
    from golem_link.config import UpdateConfig

    defaults = UpdateConfig()
    assert defaults.auto_check is True
    assert defaults.auto_stage is True
    assert defaults.auto_install is True


def test_status_reports_autostage(tmp_path):
    manager = UpdateManager(_cfg(tmp_path), EventBus(), platform="linux")
    status = manager.status()
    assert status["auto_stage"] is True
    assert status["auto_install"] is True
    assert status["auto_install_supported"] is False


def test_pre_130_config_gets_autopilot_once(tmp_path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    (data / "config.json").write_text(
        json.dumps({"node_name": "old-node", "update": {"enabled": True, "auto_check": True, "auto_install": False}}),
        encoding="utf-8",
    )
    cfg = ConfigManager(data)
    assert cfg.get().update.auto_stage is True
    # The migration is persisted, so an explicit opt-out survives the next load.
    cfg.update(lambda c: setattr(c.update, "auto_stage", False))
    again = ConfigManager(data)
    assert again.get().update.auto_stage is False


def test_helper_install_plan_and_request(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _local_release(tmp_path, cfg)
    monkeypatch.setattr("golem_link.update.remote.helper_ready", lambda: True)
    manager = UpdateManager(cfg, EventBus(), platform="linux")
    status = manager.status()
    assert status["remote_install"] is True
    assert status["remote_install_supported"] is True
    assert status["auto_install_supported"] is True
    asyncio.run(manager.check())
    asyncio.run(manager.download())

    async def no_wait(nonce, timeout=45.0):
        return None

    manager._wait_helper_result = no_wait  # type: ignore[method-assign]
    status = asyncio.run(manager.apply())
    assert status["state"] == "installing"
    assert status["install"]["mode"] == "privileged-helper"
    assert status["install"]["requires_operator"] is False
    request = json.loads((cfg.data_dir / "updates" / "remote-update.request").read_text())
    assert request["version"] == NEWER
    assert Path(request["artifact"]) == Path(status["artifact"]["staged"])
    assert request["sha256"] == status["artifact"]["sha256"]
    assert request["size"] == status["artifact"]["size"]
    assert len(request["nonce"]) == 32


def test_helper_failure_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("golem_link.update.remote.helper_ready", lambda: True)
    manager = UpdateManager(_cfg(tmp_path), EventBus(), platform="linux")
    manager.work_dir.mkdir(parents=True, exist_ok=True)
    nonce = "a" * 32
    (manager.work_dir / "remote-update.result").write_text(
        json.dumps({"ok": False, "rc": 2, "nonce": nonce, "error": "bad hash"})
    )
    with pytest.raises(ValueError, match="bad hash"):
        asyncio.run(manager._wait_helper_result(nonce, timeout=5))
    assert manager.state == "error"
    assert "bad hash" in (manager.error or "")


def test_missing_helper_trigger_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("golem_link.update.remote.helper_ready", lambda: True)
    manager = UpdateManager(_cfg(tmp_path), EventBus(), platform="linux")
    manager.work_dir.mkdir(parents=True, exist_ok=True)
    (manager.work_dir / "remote-update.request").write_text("{}")
    with pytest.raises(ValueError, match="did not pick up"):
        asyncio.run(manager._wait_helper_result("b" * 32, timeout=1.5))
    assert not (manager.work_dir / "remote-update.request").exists()
    assert manager.state == "error"


def test_helper_ready_needs_marker_and_executable(tmp_path, monkeypatch):
    from golem_link import remote_maintenance as remote

    marker = tmp_path / "marker"
    helper = tmp_path / "helper"
    monkeypatch.setattr(remote, "MARKER", marker)
    monkeypatch.setattr(remote, "HELPER", helper)
    monkeypatch.setattr(remote.os, "name", "posix")
    assert remote.helper_ready() is False
    marker.write_text("", encoding="utf-8")
    assert remote.helper_ready() is False
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    assert remote.helper_ready() is True


def test_new_peer_gets_control_when_helper_enabled(tmp_path, monkeypatch):
    from golem_link.identity import NodeIdentity
    from golem_link.networking.peer_manager import PeerManager

    monkeypatch.setattr("golem_link.networking.peer_manager.remote.helper_ready", lambda: True)
    cfg = _cfg(tmp_path)
    db = Database(cfg.db_path())
    overload = type("Overload", (), {"pressure": type("Pressure", (), {"as_dict": lambda self: {}})()})()

    async def pair():
        manager = PeerManager(NodeIdentity(cfg), cfg, db, EventBus(), overload)
        manager._save_paired_peer(
            node_id="gl-new",
            name="new",
            address="192.168.1.9",
            port=8443,
            public_key="k",
            cert_pem="cert",
            fingerprint="f",
            secret=b"\x01" * 32,
        )
        peer = manager.peers["gl-new"]
        peer.monitor_task.cancel()
        return manager

    manager = asyncio.run(pair())
    row = db.get_peer("gl-new")
    assert row["allow_control"] == 1
    assert manager.peers["gl-new"].allow_control is True
    db.close()
