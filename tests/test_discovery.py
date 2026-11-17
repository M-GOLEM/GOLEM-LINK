import asyncio

from zeroconf import ServiceStateChange

from golem_link.config import ConfigManager
from golem_link.discovery.mdns import MDNSDiscovery
from golem_link.identity import NodeIdentity


def _discovery(tmp_path, seen):
    cfg = ConfigManager(tmp_path / "data")
    cfg.ensure_dirs()

    async def on_peer(payload, address):
        seen.append(payload["node_id"])

    async def on_lost(node_id):
        seen.append(f"lost:{node_id}")

    return MDNSDiscovery(NodeIdentity(cfg), 8443, 8080, on_peer, on_lost)


def test_dispatch_change_schedules_coroutine_on_loop(tmp_path):
    seen: list[str] = []

    async def scenario() -> None:
        discovery = _discovery(tmp_path, seen)
        discovery._loop = asyncio.get_running_loop()
        discovery._zc = object()  # sentinel: _on_change only checks for None
        discovery._dispatch_change(
            None, "_golem-link._tcp.local.", "GOLEM gl-peer._golem-link._tcp.local.", ServiceStateChange.Removed
        )
        await asyncio.sleep(0.05)
        assert seen == ["lost:gl-peer"]

    asyncio.run(scenario())


def test_dispatch_change_without_loop_is_a_noop(tmp_path):
    seen: list[str] = []
    discovery = _discovery(tmp_path, seen)
    discovery._dispatch_change(
        None, "_golem-link._tcp.local.", "GOLEM gl-peer._golem-link._tcp.local.", ServiceStateChange.Removed
    )
    assert seen == []


def test_node_id_from_name():
    assert MDNSDiscovery._node_id_from_name("GOLEM gl-abc._golem-link._tcp.local.") == "gl-abc"
    assert MDNSDiscovery._node_id_from_name("other") is None
