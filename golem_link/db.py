"""SQLite persistence for GOLEM LINK (peers, tasks, alerts, transfers, audit)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS peers (
    node_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    os TEXT,
    os_version TEXT,
    hostname TEXT,
    cpu_model TEXT,
    address TEXT,
    peer_port INTEGER,
    public_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    cert_pem TEXT NOT NULL,
    secret_hex TEXT NOT NULL,
    paired_at TEXT NOT NULL,
    last_seen REAL,
    auto_connect INTEGER NOT NULL DEFAULT 1,
    allow_tasks INTEGER NOT NULL DEFAULT 1,
    allow_storage INTEGER NOT NULL DEFAULT 1,
    allow_updates INTEGER NOT NULL DEFAULT 1,
    allow_control INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'offline'
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plugin TEXT NOT NULL,
    spec TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 5,
    source_node TEXT NOT NULL,
    executor_node TEXT,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    timeout_s INTEGER NOT NULL,
    retries INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 2,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    preempt_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    result TEXT,
    authorized_by TEXT,
    resource_profile TEXT,
    target_node TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    ts REAL NOT NULL,
    event TEXT NOT NULL,
    detail TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    node_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL,
    threshold REAL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    acknowledged_at REAL,
    dedupe_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);

CREATE TABLE IF NOT EXISTS transfers (
    id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    peer_node TEXT,
    share_id TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    transferred INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    sha256 TEXT,
    started_at REAL NOT NULL,
    finished_at REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_transfers_started ON transfers(started_at);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);

CREATE TABLE IF NOT EXISTS pair_requests (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    name TEXT NOT NULL,
    os TEXT,
    os_version TEXT,
    hostname TEXT,
    cpu_model TEXT,
    fingerprint TEXT NOT NULL,
    public_key TEXT NOT NULL,
    cert_pem TEXT NOT NULL,
    address TEXT,
    peer_port INTEGER,
    eph_public TEXT NOT NULL,
    pair_key_hex TEXT NOT NULL,
    code TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS clipboard_items (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    from_node TEXT NOT NULL,
    from_name TEXT NOT NULL,
    text TEXT NOT NULL,
    chars INTEGER NOT NULL,
    copied_at REAL,
    copy_method TEXT,
    copy_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_clipboard_ts ON clipboard_items(ts);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the first schema version."""
        cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(pair_requests)")}
        if "peer_port" not in cols:
            self._conn.execute("ALTER TABLE pair_requests ADD COLUMN peer_port INTEGER")
        for column in ("os_version", "hostname", "cpu_model"):
            if column not in cols:
                self._conn.execute(f"ALTER TABLE pair_requests ADD COLUMN {column} TEXT")
        peer_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(peers)")}
        if "cpu_model" not in peer_cols:
            self._conn.execute("ALTER TABLE peers ADD COLUMN cpu_model TEXT")
        if "allow_updates" not in peer_cols:
            self._conn.execute(
                "ALTER TABLE peers ADD COLUMN allow_updates INTEGER NOT NULL DEFAULT 1"
            )
        if "allow_control" not in peer_cols:
            # Remote update/restart commands stay off until the operator grants
            # them per peer; serving artifacts is inert and on by default.
            self._conn.execute(
                "ALTER TABLE peers ADD COLUMN allow_control INTEGER NOT NULL DEFAULT 0"
            )
        task_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(tasks)")}
        if "preempt_count" not in task_cols:
            # Bounds automatic requeue-to-peer: a task that already froze the
            # host twice is not thrown back into the same loop again.
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN preempt_count INTEGER NOT NULL DEFAULT 0"
            )
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            # 1.0.0 hardcoded task sharing off at pairing time, which silently
            # blocked load sharing. Turn it on for peers the operator never
            # explicitly restricted; leave deliberate denials alone.
            denied = {
                row["target"]
                for row in self._conn.execute(
                    "SELECT DISTINCT target FROM audit WHERE action = 'peer-permissions' "
                    "AND detail LIKE '%\"allow_tasks\": 0%' AND target IS NOT NULL"
                )
            }
            for row in self._conn.execute(
                "SELECT node_id FROM peers WHERE allow_tasks = 0"
            ).fetchall():
                if row["node_id"] not in denied:
                    self._conn.execute(
                        "UPDATE peers SET allow_tasks = 1 WHERE node_id = ?", (row["node_id"],)
                    )
            self._conn.execute("PRAGMA user_version = 2")

    # -- generic -----------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- peers -------------------------------------------------------------
    def upsert_peer(self, peer: dict) -> None:
        defaults = {
            "auto_connect": 1,
            "allow_tasks": 1,
            "allow_storage": 1,
            "allow_updates": 1,
            "allow_control": 0,
            "status": "offline",
        }
        peer = {**defaults, **peer}
        cols = [
            "node_id", "name", "os", "os_version", "hostname", "cpu_model", "address", "peer_port",
            "public_key", "fingerprint", "cert_pem", "secret_hex", "paired_at",
            "last_seen", "auto_connect", "allow_tasks", "allow_storage",
            "allow_updates", "allow_control", "status",
        ]
        values = [peer.get(c) for c in cols]
        self.execute(
            f"INSERT INTO peers ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
            "ON CONFLICT(node_id) DO UPDATE SET "
            + ",".join(f"{c}=excluded.{c}" for c in cols if c != "node_id"),
            values,
        )

    def get_peer(self, node_id: str) -> dict | None:
        return self.query_one("SELECT * FROM peers WHERE node_id = ?", (node_id,))

    def list_peers(self) -> list[dict]:
        return self.query("SELECT * FROM peers ORDER BY name")

    def delete_peer(self, node_id: str) -> None:
        self.execute("DELETE FROM peers WHERE node_id = ?", (node_id,))

    def touch_peer(self, node_id: str, address: str | None, status: str = "online") -> None:
        self.execute(
            "UPDATE peers SET last_seen = ?, status = ?, address = COALESCE(?, address) "
            "WHERE node_id = ?",
            (time.time(), status, address, node_id),
        )

    # -- tasks -------------------------------------------------------------
    def insert_task(self, task: dict) -> None:
        task = {
            "priority": 5,
            "progress": 0,
            "retries": 0,
            "max_retries": 2,
            "cancel_requested": 0,
            "preempt_count": 0,
            **task,
        }
        cols = [
            "id", "name", "plugin", "spec", "priority", "source_node", "executor_node",
            "status", "progress", "created_at", "started_at", "finished_at", "timeout_s",
            "retries", "max_retries", "cancel_requested", "preempt_count", "error", "result",
            "authorized_by", "resource_profile", "target_node",
        ]
        self.execute(
            f"INSERT INTO tasks ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [task.get(c) for c in cols],
        )

    def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.execute(
            f"UPDATE tasks SET {assignments} WHERE id = ?", [*fields.values(), task_id]
        )

    def get_task(self, task_id: str) -> dict | None:
        return self.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))

    def list_tasks(self, limit: int = 200, status: str | None = None) -> list[dict]:
        if status:
            return self.query(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        return self.query(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        )

    def add_task_event(self, task_id: str, event: str, detail: str | None = None) -> None:
        self.execute(
            "INSERT INTO task_events (task_id, ts, event, detail) VALUES (?,?,?,?)",
            (task_id, time.time(), event, detail),
        )

    def task_events(self, task_id: str) -> list[dict]:
        return self.query(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY ts", (task_id,)
        )

    # -- alerts ------------------------------------------------------------
    def add_alert(
        self,
        node_id: str,
        severity: str,
        category: str,
        message: str,
        value: float | None = None,
        threshold: float | None = None,
        dedupe_key: str | None = None,
    ) -> int:
        cur = self.execute(
            "INSERT INTO alerts (ts, node_id, severity, category, message, value, threshold, dedupe_key) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), node_id, severity, category, message, value, threshold, dedupe_key),
        )
        return int(cur.lastrowid)

    def has_open_alert(self, dedupe_key: str) -> bool:
        rows = self.query(
            "SELECT 1 FROM alerts WHERE acknowledged = 0 AND dedupe_key = ? LIMIT 1",
            (dedupe_key,),
        )
        return bool(rows)

    def list_alerts(self, limit: int = 200, only_open: bool = False) -> list[dict]:
        where = "WHERE acknowledged = 0" if only_open else ""
        return self.query(
            f"SELECT * FROM alerts {where} ORDER BY ts DESC LIMIT ?", (limit,)
        )

    def ack_alert(self, alert_id: int) -> None:
        self.execute(
            "UPDATE alerts SET acknowledged = 1, acknowledged_at = ? WHERE id = ?",
            (time.time(), alert_id),
        )

    def ack_all_alerts(self) -> None:
        self.execute(
            "UPDATE alerts SET acknowledged = 1, acknowledged_at = ? WHERE acknowledged = 0",
            (time.time(),),
        )

    def prune_alerts(self, keep: int) -> None:
        self.execute(
            "DELETE FROM alerts WHERE id NOT IN (SELECT id FROM alerts ORDER BY ts DESC LIMIT ?)",
            (keep,),
        )

    # -- transfers ---------------------------------------------------------
    def insert_transfer(self, transfer: dict) -> None:
        cols = [
            "id", "direction", "peer_node", "share_id", "rel_path", "size",
            "transferred", "status", "sha256", "started_at", "finished_at", "error",
        ]
        self.execute(
            f"INSERT INTO transfers ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [transfer.get(c) for c in cols],
        )

    def update_transfer(self, transfer_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.execute(
            f"UPDATE transfers SET {assignments} WHERE id = ?",
            [*fields.values(), transfer_id],
        )

    def list_transfers(self, limit: int = 100) -> list[dict]:
        return self.query(
            "SELECT * FROM transfers ORDER BY started_at DESC LIMIT ?", (limit,)
        )

    # -- audit -------------------------------------------------------------
    def audit(self, actor: str, action: str, target: str | None, detail: str | None, ip: str | None) -> None:
        self.execute(
            "INSERT INTO audit (ts, actor, action, target, detail, ip) VALUES (?,?,?,?,?,?)",
            (time.time(), actor, action, target, detail, ip),
        )

    def list_audit(self, limit: int = 200) -> list[dict]:
        return self.query("SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (limit,))

    def prune_audit(self, keep: int) -> None:
        self.execute(
            "DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY ts DESC LIMIT ?)",
            (keep,),
        )

    # -- clipboard relay ---------------------------------------------------
    def insert_clipboard_item(self, item: dict) -> None:
        cols = [
            "id", "ts", "from_node", "from_name", "text", "chars",
            "copied_at", "copy_method", "copy_error",
        ]
        self.execute(
            f"INSERT INTO clipboard_items ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [item.get(c) for c in cols],
        )

    def get_clipboard_item(self, item_id: str) -> dict | None:
        return self.query_one("SELECT * FROM clipboard_items WHERE id = ?", (item_id,))

    def list_clipboard_items(self, limit: int = 20) -> list[dict]:
        return self.query(
            "SELECT * FROM clipboard_items ORDER BY ts DESC LIMIT ?", (max(1, limit),)
        )

    def mark_clipboard_copied(
        self, item_id: str, method: str | None, error: str | None = None
    ) -> None:
        self.execute(
            "UPDATE clipboard_items SET copied_at = ?, copy_method = ?, copy_error = ? WHERE id = ?",
            (time.time() if method else None, method, error, item_id),
        )

    def prune_clipboard_items(self, keep: int) -> None:
        self.execute(
            "DELETE FROM clipboard_items WHERE id NOT IN "
            "(SELECT id FROM clipboard_items ORDER BY ts DESC LIMIT ?)",
            (max(1, keep),),
        )

    # -- pairing -----------------------------------------------------------
    def create_pair_request(self, req: dict) -> None:
        cols = [
            "id", "node_id", "name", "os", "os_version", "hostname", "cpu_model",
            "fingerprint", "public_key", "cert_pem", "address", "peer_port",
            "eph_public", "pair_key_hex", "code", "created_at", "expires_at", "status",
        ]
        self.execute(
            f"INSERT INTO pair_requests ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [req.get(c) for c in cols],
        )

    def get_pair_request(self, request_id: str) -> dict | None:
        return self.query_one("SELECT * FROM pair_requests WHERE id = ?", (request_id,))

    def list_pair_requests(self) -> list[dict]:
        return self.query(
            "SELECT * FROM pair_requests WHERE status = 'pending' ORDER BY created_at DESC"
        )

    def set_pair_status(self, request_id: str, status: str) -> None:
        self.execute(
            "UPDATE pair_requests SET status = ? WHERE id = ?", (status, request_id)
        )

    def prune_pair_requests(self, now: float) -> None:
        self.execute(
            "DELETE FROM pair_requests WHERE expires_at < ? OR status != 'pending'", (now,)
        )

    @staticmethod
    def encode_json(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), default=str)
