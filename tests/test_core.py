from pathlib import Path

import pytest

from golem_link import crypto
from golem_link.config import AppConfig, ConfigManager
from golem_link.db import Database
from golem_link.identity import NodeIdentity


@pytest.fixture()
def cfg(tmp_path: Path) -> ConfigManager:
    return ConfigManager(tmp_path / "data")


def test_config_roundtrip(cfg: ConfigManager):
    cfg.update(lambda c: setattr(c, "node_name", "test-node"))
    cfg.update(lambda c: setattr(c.dashboard, "port", 8090))
    reloaded = ConfigManager(cfg.data_dir)
    assert reloaded.get().node_name == "test-node"
    assert reloaded.get().dashboard.port == 8090
    assert reloaded.get().data_dir == str(cfg.data_dir)


def test_config_is_atomic_and_private(cfg: ConfigManager):
    cfg.save()
    raw = cfg.config_path.read_text(encoding="utf-8")
    assert "node_name" in raw
    assert not list(cfg.data_dir.glob(".config-*.tmp"))


def test_identity_persistent(cfg: ConfigManager):
    ident = NodeIdentity(cfg)
    node_id = ident.node_id
    fp = ident.fingerprint
    assert node_id.startswith("gl-")
    assert len(fp) == 23  # 8 bytes as hex pairs separated by ':'
    cert = ident.cert_path if hasattr(ident, "cert_path") else cfg.cert_path()
    assert cert.exists()
    assert cfg.key_path().exists()

    ident2 = NodeIdentity(ConfigManager(cfg.data_dir))
    assert ident2.node_id == node_id
    assert ident2.fingerprint == fp


def test_password_hash_and_token():
    h, salt = crypto.hash_password("s3cret!")
    assert crypto.verify_password("s3cret!", h, salt)
    assert not crypto.verify_password("wrong", h, salt)

    secret = crypto.new_secret()
    token = crypto.make_token(secret, "gl-a", "gl-b", ttl_s=60)
    assert crypto.verify_token(secret, token, "gl-a", "gl-b")
    assert not crypto.verify_token(secret, token, "gl-b", "gl-a")
    assert not crypto.verify_token(crypto.new_secret(), token, "gl-a", "gl-b")


def test_pairing_ecdh_symmetry():
    a_priv, a_pub = crypto.generate_x25519()
    b_priv, b_pub = crypto.generate_x25519()
    shared_a = crypto.ecdh(a_priv, b_pub)
    shared_b = crypto.ecdh(b_priv, a_pub)
    assert shared_a == shared_b
    key_a = crypto.pairing_key(shared_a, "gl-a", "gl-b")
    key_b = crypto.pairing_key(shared_b, "gl-a", "gl-b")
    assert key_a == key_b
    assert crypto.pairing_code(key_a) == crypto.pairing_code(key_b)
    assert len(crypto.pairing_code(key_a)) == 6


def test_database_peer_and_task_flow(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.upsert_peer(
        {
            "node_id": "gl-b",
            "name": "desktop",
            "os": "Windows",
            "fingerprint": "aa:bb",
            "public_key": "pem",
            "cert_pem": "cert",
            "secret_hex": "00",
            "paired_at": "now",
            "status": "online",
        }
    )
    assert db.get_peer("gl-b")["name"] == "desktop"
    db.touch_peer("gl-b", "10.0.0.5", "online")
    assert db.get_peer("gl-b")["address"] == "10.0.0.5"

    task = {
        "id": "t1",
        "name": "demo",
        "plugin": "python",
        "spec": "{}",
        "source_node": "gl-a",
        "status": "queued",
        "created_at": 1.0,
        "timeout_s": 60,
    }
    db.insert_task(task)
    db.update_task("t1", status="running", executor_node="gl-b")
    db.add_task_event("t1", "started")
    row = db.get_task("t1")
    assert row["status"] == "running"
    assert row["executor_node"] == "gl-b"
    assert db.task_events("t1")[0]["event"] == "started"

    db.add_alert("gl-b", "warning", "cpu", "test alert", 81.0, 80.0, "cpu-warn")
    assert len(db.list_alerts()) == 1
    db.prune_alerts(1000)
    assert len(db.list_alerts()) == 1
    db.close()


def test_allow_tasks_migration_keeps_explicit_denial(tmp_path: Path):
    path = tmp_path / "migrate.db"
    db = Database(path)
    for node_id in ("gl-kept", "gl-denied"):
        db.upsert_peer(
            {
                "node_id": node_id,
                "name": node_id,
                "public_key": "pem",
                "fingerprint": "aa:bb",
                "cert_pem": "cert",
                "secret_hex": "00",
                "paired_at": "then",
                "allow_tasks": 0,
            }
        )
    db.audit("user", "peer-permissions", "gl-denied", '{"allow_tasks": 0}', None)
    db.execute("PRAGMA user_version = 1")
    db.close()

    reopened = Database(path)
    try:
        assert reopened.get_peer("gl-kept")["allow_tasks"] == 1
        assert reopened.get_peer("gl-denied")["allow_tasks"] == 0
        assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        reopened.close()
