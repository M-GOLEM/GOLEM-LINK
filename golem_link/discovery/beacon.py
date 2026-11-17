"""UDP broadcast fallback discovery.

mDNS is not always reliable on Windows firewalls and some routers; the beacon
is a tiny JSON datagram that every agent broadcasts to its subnet broadcast
addresses. It carries no secrets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import Awaitable, Callable

from ..identity import NodeIdentity
from ..utils import get_broadcast_addresses
from ..version import BEACON_MAGIC, VERSION

log = logging.getLogger(__name__)

PeerCallback = Callable[[dict, str], Awaitable[None]]


class BeaconDiscovery:
    def __init__(
        self,
        identity: NodeIdentity,
        peer_port: int,
        dashboard_port: int,
        port: int,
        interval_s: float,
        on_peer: PeerCallback,
    ) -> None:
        self.identity = identity
        self.peer_port = peer_port
        self.dashboard_port = dashboard_port
        self.port = port
        self.interval_s = max(interval_s, 1.0)
        self.on_peer = on_peer
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._sock: socket.socket | None = None

    def _payload(self) -> bytes:
        return json.dumps(
            {
                "magic": BEACON_MAGIC.decode(),
                "node_id": self.identity.node_id,
                "name": self.identity.cfg.get().node_name,
                "os": socket.gethostname(),
                "version": VERSION,
                "peer_port": self.peer_port,
                "dashboard_port": self.dashboard_port,
                "fingerprint": self.identity.fingerprint,
                "addresses": [],
            }
        ).encode()

    async def start(self) -> None:
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._listen_loop(), name="beacon-listen"),
            asyncio.create_task(self._broadcast_loop(), name="beacon-broadcast"),
        ]
        log.info("UDP beacon active on port %s", self.port)

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None

    async def _listen_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("", self.port))
        sock.setblocking(False)
        self._sock = sock
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            try:
                data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 4096), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            except OSError:
                log.exception("beacon receive failed")
                await asyncio.sleep(2)
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if msg.get("magic") != BEACON_MAGIC.decode():
                continue
            node_id = msg.get("node_id")
            if not node_id or node_id == self.identity.node_id:
                continue
            msg["address"] = addr[0]
            msg["source"] = "beacon"
            try:
                await self.on_peer(msg, addr[0])
            except Exception:
                log.debug("beacon peer callback failed", exc_info=True)

    async def _broadcast_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            targets = get_broadcast_addresses() or ["255.255.255.255"]
            payload = self._payload()
            for target in targets:
                try:
                    await loop.sock_sendto(sock, payload, (target, self.port))
                except OSError as exc:
                    log.debug("beacon send to %s failed: %s", target, exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                pass
        sock.close()
