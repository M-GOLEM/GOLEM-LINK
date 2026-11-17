"""mDNS/DNS-SD advertisement and browsing for GOLEM LINK peers."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Awaitable, Callable

from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from ..identity import NodeIdentity
from ..utils import get_lan_ips
from ..version import MDNS_SERVICE_TYPE, VERSION

log = logging.getLogger(__name__)

PeerCallback = Callable[[dict, str], Awaitable[None]]
LostCallback = Callable[[str], Awaitable[None]]


class MDNSDiscovery:
    def __init__(
        self,
        identity: NodeIdentity,
        peer_port: int,
        dashboard_port: int,
        on_peer: PeerCallback,
        on_lost: LostCallback,
    ) -> None:
        self.identity = identity
        self.peer_port = peer_port
        self.dashboard_port = dashboard_port
        self.on_peer = on_peer
        self.on_lost = on_lost
        self._zc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None
        self._info: AsyncServiceInfo | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._service_name = f"GOLEM {identity.node_id}._golem-link._tcp.local."

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        addresses = get_lan_ips() or ["127.0.0.1"]
        props = {
            b"node_id": self.identity.node_id.encode(),
            b"name": self.identity.cfg.get().node_name.encode(),
            b"os": socket.gethostname().encode(),
            b"version": VERSION.encode(),
            b"peer_port": str(self.peer_port).encode(),
            b"dash_port": str(self.dashboard_port).encode(),
            b"fp": self.identity.fingerprint.encode(),
        }
        self._zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
        self._info = AsyncServiceInfo(
            MDNS_SERVICE_TYPE,
            self._service_name,
            addresses=[socket.inet_aton(ip) for ip in addresses],
            port=self.peer_port,
            properties=props,
            server=f"{socket.gethostname()}.local.",
        )
        await self._zc.async_register_service(self._info, allow_name_change=True)
        self._browser = AsyncServiceBrowser(
            self._zc.zeroconf, MDNS_SERVICE_TYPE, handlers=[self._dispatch_change]
        )
        log.info("mDNS registered %s on ports peer=%s dashboard=%s", self._service_name, self.peer_port, self.dashboard_port)

    async def stop(self) -> None:
        if self._browser:
            await self._browser.async_cancel()
            self._browser = None
        if self._zc:
            if self._info:
                try:
                    await self._zc.async_unregister_service(self._info)
                except Exception:
                    log.debug("mDNS unregister failed", exc_info=True)
            await self._zc.async_close()
            self._zc = None

    # -- callbacks ---------------------------------------------------------
    def _dispatch_change(self, zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        # zeroconf calls handlers from its own thread, so hopping onto the
        # asyncio loop is mandatory; previously the coroutine was dropped with
        # "coroutine was never awaited" and discovery callbacks never ran.
        if self._loop is None or self._loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(
            self._on_change(zeroconf, service_type, name, state_change), self._loop
        )
        future.add_done_callback(self._log_failure)

    @staticmethod
    def _log_failure(future: "asyncio.Future") -> None:
        try:
            future.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("mDNS change handler failed", exc_info=True)

    async def _on_change(self, zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if self._zc is None:
            return
        try:
            if state_change is ServiceStateChange.Removed:
                node_id = self._node_id_from_name(name)
                if node_id and node_id != self.identity.node_id:
                    await self.on_lost(node_id)
                return
            info = AsyncServiceInfo(service_type, name)
            ok = await info.async_request(self._zc.zeroconf, timeout=2500)
            if not ok or not info.addresses:
                return
            props = {
                (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                for k, v in (info.properties or {}).items()
            }
            node_id = props.get("node_id") or self._node_id_from_name(name)
            if not node_id or node_id == self.identity.node_id:
                return
            address = socket.inet_ntoa(info.addresses[0])
            payload = {
                "node_id": node_id,
                "name": props.get("name") or name.split(".")[0],
                "os": props.get("os") or "",
                "version": props.get("version") or "",
                "peer_port": int(props.get("peer_port") or info.port or 8443),
                "dashboard_port": int(props.get("dash_port") or 8080),
                "address": address,
                "fingerprint": props.get("fp") or "",
                "source": "mdns",
            }
            await self.on_peer(payload, address)
        except Exception:
            log.debug("mDNS callback failed for %s", name, exc_info=True)

    @staticmethod
    def _node_id_from_name(name: str) -> str | None:
        if name.startswith("GOLEM "):
            return name[6:].split(".")[0]
        return None
