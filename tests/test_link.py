"""Headless pairing helper tests (no network; the dashboard client is faked)."""

from __future__ import annotations

import argparse
import time

import pytest

from golem_link.link import LinkError, find_request, run_approve, run_auto, run_init


def test_find_request_requires_live_matching_code():
    now = time.time()
    requests = [
        {"id": "old", "code": "111111", "expires_at": now - 5},
        {"id": "live", "code": "222222", "expires_at": now + 60},
    ]
    assert find_request(requests, "222222", now=now)["id"] == "live"
    with pytest.raises(LinkError):
        find_request(requests, "111111", now=now)
    with pytest.raises(LinkError):
        find_request(requests, "999999", now=now)


def test_find_request_refuses_ambiguous_codes():
    requests = [
        {"id": "a", "code": "333333", "expires_at": time.time() + 60},
        {"id": "b", "code": "333333", "expires_at": time.time() + 60},
    ]
    with pytest.raises(LinkError):
        find_request(requests, "333333")


class FakeDashboard:
    def __init__(self, *, poll_states=None, requests=None, devices=None, discovered=None):
        self.poll_states = list(poll_states or [])
        self._requests = list(requests or [])
        self._devices = list(devices or [])
        self._discovered = list(discovered or [])
        self.approved: list[str] = []
        self.begun: list[str] = []

    def begin(self, peer: str) -> dict:
        self.begun.append(peer)
        return {
            "request_id": "r1",
            "node_id": "gl-b",
            "name": "desktop",
            "address": peer,
            "code": "123456",
        }

    def poll(self, request_id: str) -> dict:
        state = self.poll_states.pop(0) if self.poll_states else "pending"
        return {"request_id": request_id, "status": state}

    def requests(self) -> list[dict]:
        return self._requests

    def approve(self, request_id: str) -> dict:
        self.approved.append(request_id)
        return {"status": "approved"}

    def devices(self) -> list[dict]:
        return self._devices

    def discovered(self) -> list[dict]:
        return self._discovered


def test_setup_accepts_password_from_environment(tmp_path, monkeypatch):
    from golem_link.api.auth import DashboardAuth
    from golem_link.cli import cmd_setup
    from golem_link.config import ConfigManager
    from golem_link.db import Database

    monkeypatch.setenv("GOLEM_ADMIN_PASSWORD", "installer-secret")
    args = argparse.Namespace(data_dir=str(tmp_path), name="left-laptop", password="")
    assert cmd_setup(args) == 0

    cfg = ConfigManager(tmp_path)
    db = Database(cfg.db_path())
    try:
        role, _ = DashboardAuth(cfg, db).login("installer-secret", "127.0.0.1")
        assert role == "admin"
        assert cfg.get().node_name == "left-laptop"
    finally:
        db.close()


def test_init_prints_code_and_waits_for_approval():
    dashboard = FakeDashboard(
        poll_states=["pending", "approved"],
        devices=[{"node_id": "gl-b", "name": "desktop", "status": "online"}],
    )
    lines: list[str] = []
    run_init(dashboard, "192.168.1.20", 30, sleep=lambda _s: None, out=lines.append)
    assert dashboard.begun == ["192.168.1.20"]
    assert any("123456" in line for line in lines)
    assert any("online" in line for line in lines)
    assert dashboard.poll_states == []


def test_init_reports_denied():
    dashboard = FakeDashboard(poll_states=["denied"])
    with pytest.raises(LinkError, match="denied"):
        run_init(dashboard, "192.168.1.20", 5, sleep=lambda _s: None, out=lambda _l: None)


def test_auto_pairs_with_the_only_discovered_machine():
    dashboard = FakeDashboard(
        discovered=[
            {
                "node_id": "gl-b",
                "name": "desktop",
                "address": "192.168.1.20",
                "port": 8443,
                "paired": False,
            }
        ],
        poll_states=["pending", "approved"],
        devices=[{"node_id": "gl-b", "name": "desktop", "status": "online"}],
    )
    lines: list[str] = []
    run_auto(
        dashboard, 30, discovery_s=0, sleep=lambda _s: None, out=lines.append
    )
    assert dashboard.begun == ["192.168.1.20:8443"]
    assert any("123456" in line for line in lines)
    assert any("online" in line for line in lines)


def test_auto_ignores_paired_and_busy_networks():
    quiet = FakeDashboard(discovered=[])
    assert run_auto(quiet, 5, discovery_s=0, sleep=lambda _s: None, out=lambda _l: None) is None
    assert quiet.begun == []

    busy = FakeDashboard(
        discovered=[
            {"node_id": "gl-b", "name": "left", "address": "192.168.1.10", "paired": False},
            {"node_id": "gl-c", "name": "right", "address": "192.168.1.11", "paired": False},
        ]
    )
    lines: list[str] = []
    assert run_auto(busy, 5, discovery_s=0, sleep=lambda _s: None, out=lines.append) is None
    assert busy.begun == []
    assert any("More than one" in line for line in lines)


def test_auto_shows_incoming_request_instead_of_duplicating_it():
    pending = {
        "id": "req-9",
        "node_id": "gl-b",
        "name": "desktop",
        "address": "192.168.1.20",
        "code": "654321",
        "expires_at": time.time() + 60,
    }
    dashboard = FakeDashboard(
        discovered=[
            {"node_id": "gl-b", "name": "desktop", "address": "192.168.1.20", "port": 8443}
        ],
        requests=[pending],
        devices=[{"node_id": "gl-b", "name": "desktop", "status": "online"}],
    )
    lines: list[str] = []
    run_auto(dashboard, 10, discovery_s=0, sleep=lambda _s: None, out=lines.append)
    assert dashboard.begun == []
    assert any("654321" in line for line in lines)
    assert any("online" in line for line in lines)


def test_approve_verifies_code_before_approving():
    now = time.time()
    pending = {
        "id": "req-1",
        "node_id": "gl-b",
        "name": "desktop",
        "address": "192.168.1.20",
        "peer_port": 8443,
        "fingerprint": "aa:bb",
        "code": "654321",
        "expires_at": now + 60,
    }
    dashboard = FakeDashboard(
        requests=[pending],
        devices=[{"node_id": "gl-b", "name": "desktop", "status": "online"}],
    )
    lines: list[str] = []
    with pytest.raises(LinkError):
        run_approve(dashboard, "000000", 5, sleep=lambda _s: None, out=lines.append)
    assert dashboard.approved == []

    run_approve(dashboard, "654321", 5, sleep=lambda _s: None, out=lines.append)
    assert dashboard.approved == ["req-1"]
    assert any("online" in line for line in lines)
