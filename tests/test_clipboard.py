from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from golem_link import clipboard as clipboard_module
from golem_link.clipboard import ClipboardBridge
from golem_link.clipboard_session import (
    ClipboardUnavailable,
    _parse_sessions,
    get_clipboard_script,
    set_clipboard_script,
)
from golem_link.config import ConfigManager
from golem_link.db import Database
from golem_link.events import EventBus


def make_bridge(tmp_path: Path, **changes) -> ClipboardBridge:
    cfg = ConfigManager(tmp_path)
    cfg.ensure_dirs()
    if changes:
        cfg.update(
            lambda c: [
                setattr(c.clipboard, key, value) for key, value in changes.items()
            ]
        )
    db = Database(cfg.db_path())
    return ClipboardBridge(cfg, db, EventBus())


class FakeClient:
    def __init__(self, answer=None, error: Exception | None = None) -> None:
        self.answer = answer or {"copied": True, "copy_method": "console-session"}
        self.error = error
        self.calls: list[tuple] = []

    async def post_json(self, target, path, payload, timeout=60):
        self.calls.append((target, path, payload))
        if self.error:
            raise self.error
        return dict(self.answer)


def fake_peers(**peer_changes):
    fields = {
        "node_id": "gl-peer",
        "name": "GOLEM",
        "status": "online",
        "remote_allows_control": True,
    }
    fields.update(peer_changes)
    peer = SimpleNamespace(target=lambda: "target", **fields)
    return SimpleNamespace(peers={"gl-peer": peer}, client=FakeClient())


def test_receive_stores_text_when_auto_copy_is_off(tmp_path):
    bridge = make_bridge(tmp_path, auto_copy=False)
    item = bridge.receive("gl-a", "Laptop", "مرحبا من اللابتوب")
    assert item["copied"] is False
    assert item["chars"] == len("مرحبا من اللابتوب")
    recent = bridge.recent(5)
    assert recent[0]["id"] == item["id"]
    assert recent[0]["text"] == "مرحبا من اللابتوب"
    assert bridge.status()["last_item"]["preview"].startswith("مرحبا")


def test_receive_rejects_empty_long_and_disabled(tmp_path):
    bridge = make_bridge(tmp_path, auto_copy=False, max_chars=10)
    with pytest.raises(ValueError, match="empty"):
        bridge.receive("gl-a", "Laptop", "   ")
    with pytest.raises(ValueError, match="longer than 10"):
        bridge.receive("gl-a", "Laptop", "x" * 11)
    bridge.cfg.update(lambda c: setattr(c.clipboard, "enabled", False))
    with pytest.raises(ValueError, match="disabled"):
        bridge.receive("gl-a", "Laptop", "hello")


def test_receive_auto_copies_and_records_the_method(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path)
    monkeypatch.setattr(clipboard_module, "copy_text", lambda text, data_dir: "console-session")
    item = bridge.receive("gl-a", "Laptop", "copied text")
    assert item["copied"] is True
    row = bridge.recent(1)[0]
    assert row["copy_method"] == "console-session"
    assert row["copy_error"] is None


def test_receive_reports_an_unreachable_desktop(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path)

    def unavailable(text, data_dir):
        raise ClipboardUnavailable("no user is logged on at this machine")

    monkeypatch.setattr(clipboard_module, "copy_text", unavailable)
    item = bridge.receive("gl-a", "Laptop", "hello")
    assert item["copied"] is False
    assert "logged on" in (item["copy_error"] or "")
    assert len(bridge.recent(1)) == 1


def test_items_are_pruned_to_the_keep_limit(tmp_path):
    bridge = make_bridge(tmp_path, auto_copy=False, keep_items=3)
    ids = [bridge.receive("gl-a", "Laptop", f"text {i}")["id"] for i in range(6)]
    recent = bridge.recent(10)
    assert len(recent) == 3
    assert {row["id"] for row in recent} == set(ids[-3:])


def test_send_requires_an_online_peer_that_grants_control(tmp_path):
    bridge = make_bridge(tmp_path)

    async def run():
        with pytest.raises(ValueError, match="unknown peer"):
            await bridge.send(fake_peers(), "gl-missing", "hello")
        offline = fake_peers()
        offline.peers["gl-peer"].status = "offline"
        with pytest.raises(ValueError, match="offline"):
            await bridge.send(offline, "gl-peer", "hello")
        denied = fake_peers(remote_allows_control=False)
        with pytest.raises(ValueError, match="does not grant control"):
            await bridge.send(denied, "gl-peer", "hello")

    asyncio.run(run())


def test_send_posts_the_text_and_reports_the_remote_copy(tmp_path):
    bridge = make_bridge(tmp_path)
    peers = fake_peers()
    result = asyncio.run(bridge.send(peers, "gl-peer", "hello there"))
    assert result["ok"] is True
    assert result["peer"] == "GOLEM"
    assert result["copied"] is True
    target, path, payload = peers.client.calls[0]
    assert target == "target"
    assert path == "/peer/clipboard"
    assert payload["text"] == "hello there"
    assert payload["from_name"] == bridge.cfg.get().node_name


def test_send_explains_an_older_peer(tmp_path):
    bridge = make_bridge(tmp_path)
    peers = fake_peers()
    peers.client = FakeClient(error=RuntimeError("HTTP 404: Not Found"))
    # The bridge catches every transport error and turns a 404 into a clear
    # message about the peer's older release.
    with pytest.raises(ValueError, match="older GOLEM LINK"):
        asyncio.run(bridge.send(peers, "gl-peer", "hello"))


def test_copy_local_and_copy_item_surface_the_failure(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, auto_copy=False)

    def unavailable(text, data_dir):
        raise ClipboardUnavailable("no active desktop session was found")

    monkeypatch.setattr(clipboard_module, "copy_text", unavailable)
    assert bridge.copy_local("hello") == {
        "copied": False,
        "method": None,
        "error": "no active desktop session was found",
    }
    item = bridge.receive("gl-a", "Laptop", "stored text")
    retried = bridge.copy_item(item["id"])
    assert retried["copied"] is False
    assert "desktop session" in (retried["error"] or "")


def test_copy_item_marks_a_retry_as_copied(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, auto_copy=False)
    item = bridge.receive("gl-a", "Laptop", "stored text")
    monkeypatch.setattr(clipboard_module, "copy_text", lambda text, data_dir: "xclip")
    assert bridge.copy_item(item["id"])["copied"] is True
    row = bridge.recent(1)[0]
    assert row["copied"] is True
    assert row["copy_method"] == "xclip"


def test_read_local_returns_this_machine_clipboard(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path)
    monkeypatch.setattr(clipboard_module, "read_text", lambda data_dir: "نص من الحافظة")
    result = bridge.read_local()
    assert result["text"] == "نص من الحافظة"
    assert result["chars"] == len("نص من الحافظة")


def test_read_local_reports_empty_long_and_disabled(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, max_chars=5)
    monkeypatch.setattr(clipboard_module, "read_text", lambda data_dir: "")
    assert bridge.read_local() == {"text": "", "chars": 0}
    monkeypatch.setattr(clipboard_module, "read_text", lambda data_dir: "x" * 6)
    with pytest.raises(ValueError, match="longer than 5"):
        bridge.read_local()
    bridge.cfg.update(lambda c: setattr(c.clipboard, "enabled", False))
    with pytest.raises(ValueError, match="disabled"):
        bridge.read_local()


def test_read_local_explains_an_unreachable_desktop(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path)

    def unavailable(data_dir):
        raise ClipboardUnavailable("no active desktop session was found")

    monkeypatch.setattr(clipboard_module, "read_text", unavailable)
    with pytest.raises(ValueError, match="desktop session"):
        bridge.read_local()


def test_read_script_targets_gettext_and_utf8():
    script = get_clipboard_script(r"C:\ProgramData\GolemLink\clipboard\read-1.txt")
    assert r"C:\ProgramData\GolemLink\clipboard\read-1.txt" in script
    assert "[System.Windows.Forms.Clipboard]::GetText()" in script
    assert "WriteAllText" in script
    assert "UTF8Encoding" in script
    assert ".err" in script


def test_windows_script_carries_the_path_and_the_error_file():
    script = set_clipboard_script(r"C:\ProgramData\GolemLink\clipboard\clip-1.txt")
    assert r"C:\ProgramData\GolemLink\clipboard\clip-1.txt" in script
    assert "[System.Windows.Forms.Clipboard]::SetText" in script
    assert ".err" in script
    assert "Remove-Item" in script


def test_loginctl_legend_is_parsed():
    legend = (
        "  2 1000 golem seat0 tty2\n"
        "  5 0 root seat0 tty1\n"
        "\n"
    )
    sessions = _parse_sessions(legend)
    assert sessions[0] == {"id": "2", "uid": "1000", "user": "golem"}
    assert len(sessions) == 2
