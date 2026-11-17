"""Peer lifecycle: discovery intake, pairing, connections, heartbeat, cluster view."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType

from .. import crypto
from .. import remote_maintenance as remote
from ..config import ConfigManager
from ..db import Database
from ..events import EventBus
from ..identity import NodeIdentity
from ..overload import OverloadEngine
from ..utils import is_lan_address
from ..version import VERSION
from .client import PeerClient, PeerConnectionError, PeerTarget

log = logging.getLogger(__name__)

PAIR_TTL_S = 300


@dataclass
class PeerRuntime:
    node_id: str
    name: str
    address: str
    port: int
    secret: bytes
    cert_pem: str
    fingerprint: str = ""
    status: str = "offline"
    last_seen: float = 0.0
    latency_ms: float | None = None
    metrics: dict[str, Any] | None = None
    info: dict[str, Any] | None = None
    ws: Any = None
    ws_task: asyncio.Task | None = None
    monitor_task: asyncio.Task | None = None
    ping_task: asyncio.Task | None = None
    failures: int = 0
    reconnects: int = 0
    last_error: str | None = None
    online_since: float | None = None
    allow_tasks: bool = True
    allow_storage: bool = True
    allow_updates: bool = True
    allow_control: bool = False
    auto_connect: bool = True
    # What the *remote* node allows us to do (learned from its capabilities
    # endpoint and pushed live when its operator changes permissions).
    remote_accepts_tasks: bool = False
    remote_accepts_storage: bool = False
    remote_allows_updates: bool = False
    remote_allows_control: bool = False

    def target(self) -> PeerTarget:
        return PeerTarget(
            node_id=self.node_id,
            address=self.address,
            port=self.port,
            cert_pem=self.cert_pem,
            secret=self.secret,
            fingerprint=self.fingerprint,
        )


@dataclass
class DiscoveredNode:
    node_id: str
    name: str
    address: str
    port: int
    os: str = ""
    version: str = ""
    fingerprint: str = ""
    source: str = "unknown"
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class PeerManager:
    # Task updates above this size travel over HTTP; the websocket stays for
    # heartbeats, metrics and small progress messages.
    WS_TASK_UPDATE_LIMIT = 512 * 1024

    def __init__(
        self,
        identity: NodeIdentity,
        cfg: ConfigManager,
        db: Database,
        bus: EventBus,
        overload: OverloadEngine,
    ) -> None:
        self.identity = identity
        self.cfg = cfg
        self.db = db
        self.bus = bus
        self.overload = overload
        self.client = PeerClient(identity, cfg)
        self.peers: dict[str, PeerRuntime] = {}
        self.discovered: dict[str, DiscoveredNode] = {}
        self.inbound_ws: dict[str, Any] = {}
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._pending_out: dict[str, dict[str, Any]] = {}
        # Terminal results outlive the pending entry so overlapping dashboard
        # polls observe the same answer instead of a spurious "unknown request".
        self._completed_out: dict[str, tuple[float, dict[str, Any]]] = {}
        # Filled in by the application once those subsystems exist.
        self.on_remote_task: Any = None
        self.on_remote_task_update: Any = None
        self.on_peer_connected: Any = None
        self._started = False

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        self._stop.clear()
        self._started = True
        for row in self.db.list_peers():
            runtime = PeerRuntime(
                node_id=row["node_id"],
                name=row["name"],
                address=row["address"] or "",
                port=int(row["peer_port"] or 0),
                secret=bytes.fromhex(row["secret_hex"]),
                cert_pem=row["cert_pem"],
                fingerprint=row["fingerprint"],
                allow_tasks=bool(row["allow_tasks"]),
                allow_storage=bool(row["allow_storage"]),
                allow_updates=bool(row.get("allow_updates", 1)),
                allow_control=bool(row.get("allow_control", 0)),
                auto_connect=bool(row["auto_connect"]),
            )
            self.peers[runtime.node_id] = runtime
            runtime.monitor_task = asyncio.create_task(
                self._peer_loop(runtime), name=f"peer-{runtime.node_id}"
            )
        self._tasks.append(
            asyncio.create_task(self._manual_peer_loop(), name="manual-peers")
        )
        self._tasks.append(
            asyncio.create_task(self._bus_loop(), name="peer-bus")
        )
        self._tasks.append(
            asyncio.create_task(self._pair_cleanup_loop(), name="pair-cleanup")
        )
        log.info("peer manager started with %d paired peer(s)", len(self.peers))

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for peer in list(self.peers.values()):
            if peer.monitor_task:
                peer.monitor_task.cancel()
            if peer.ws is not None:
                try:
                    await peer.ws.close()
                except Exception:
                    pass
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.client.close()
        self._tasks.clear()
        self._started = False

    # -- discovery intake --------------------------------------------------
    async def handle_discovered(self, payload: dict, address: str) -> None:
        node_id = payload.get("node_id")
        if not node_id or node_id == self.identity.node_id:
            return
        if not is_lan_address(address):
            log.debug("ignoring discovery from non-LAN address %s", address)
            return
        existing = self.peers.get(node_id)
        now = time.time()
        if existing:
            # Discovery is spoofable: never rewrite a paired peer's address
            # from an unauthenticated datagram. It is only used for liveness UI.
            existing.last_seen = max(existing.last_seen, now)
            return
        port = int(payload.get("peer_port") or 0)
        if not port:
            return
        entry = self.discovered.get(node_id)
        if entry is None:
            entry = DiscoveredNode(
                node_id=node_id,
                name=str(payload.get("name") or node_id),
                address=address,
                port=port,
            )
            self.discovered[node_id] = entry
        entry.name = str(payload.get("name") or entry.name)
        entry.address = address
        entry.port = port
        entry.os = str(payload.get("os") or entry.os)
        entry.version = str(payload.get("version") or entry.version)
        entry.fingerprint = str(payload.get("fingerprint") or entry.fingerprint)
        entry.source = str(payload.get("source") or entry.source)
        entry.last_seen = now
        self.bus.publish("discovered", self.discovered_payload())

    async def handle_lost(self, node_id: str) -> None:
        entry = self.discovered.pop(node_id, None)
        if entry:
            self.bus.publish("discovered", self.discovered_payload())

    def discovered_payload(self) -> list[dict]:
        now = time.time()
        # Drop stale unpaired announcements after two minutes.
        for node_id in [k for k, v in self.discovered.items() if now - v.last_seen > 120]:
            self.discovered.pop(node_id, None)
        return [
            {
                "node_id": d.node_id,
                "name": d.name,
                "address": d.address,
                "port": d.port,
                "os": d.os,
                "version": d.version,
                "fingerprint": d.fingerprint,
                "source": d.source,
                "last_seen": d.last_seen,
                "paired": d.node_id in self.peers,
            }
            for d in self.discovered.values()
        ]

    # -- pairing -----------------------------------------------------------
    async def begin_pairing(self, node_id: str | None, address: str | None) -> dict:
        target: DiscoveredNode | None = None
        if node_id and node_id in self.discovered:
            target = self.discovered[node_id]
        elif address:
            host, _, port_s = address.partition(":")
            port = int(port_s) if port_s.isdigit() else self.cfg.get().peer.port
            if not is_lan_address(host):
                raise ValueError("manual pairing is restricted to LAN addresses")
            candidates = [d for d in self.discovered.values() if d.address == host]
            target = candidates[0] if candidates else None
            if target is None:
                info = await self.client.fetch_info(host, port)
                target = DiscoveredNode(
                    node_id=info["node_id"],
                    name=info.get("name", info["node_id"]),
                    address=host,
                    port=int(info.get("peer_port") or port),
                    os=info.get("os", ""),
                    version=info.get("version", ""),
                    fingerprint=info.get("fingerprint", ""),
                )
                self.discovered[target.node_id] = target
        if target is None:
            raise ValueError("node not discovered; refresh discovery or add its IP")

        info = await self.client.fetch_info(target.address, target.port)
        if info.get("node_id") != target.node_id:
            raise ValueError("discovered node identity changed during pairing")
        info.setdefault("peer_port", target.port)
        result = await self.client.request_pair(target.address, target.port, info)
        self._pending_out[result.request_id] = {
            "request_id": result.request_id,
            "node_id": target.node_id,
            "address": target.address,
            "port": target.port,
            "key_hex": result.key_hex,
            "code": result.code,
            "expires_at": result.expires_at,
            "info": info,
        }
        self.db.audit(
            "user", "pair-request", target.node_id,
            f"code shown locally; awaiting approval on {target.name}", None,
        )
        return {
            "request_id": result.request_id,
            "node_id": target.node_id,
            "name": target.name,
            "address": target.address,
            "code": result.code,
            "fingerprint": crypto.fingerprint(info["public_key"]),
            "expires_at": result.expires_at,
        }

    def outgoing_request(self, request_id: str) -> dict | None:
        return self._pending_out.get(request_id)

    def cancel_outgoing(self, request_id: str) -> None:
        self._pending_out.pop(request_id, None)
        self.client.drop_pending(request_id)

    def _complete_outgoing(self, request_id: str, result: dict[str, Any]) -> dict[str, Any]:
        self.cancel_outgoing(request_id)
        now = time.time()
        expired = [key for key, (until, _) in self._completed_out.items() if until <= now]
        for key in expired:
            self._completed_out.pop(key, None)
        self._completed_out[request_id] = (now + 300.0, dict(result))
        return result

    async def poll_pairing(self, request_id: str) -> dict:
        cached = self._completed_out.get(request_id)
        if cached is not None:
            if cached[0] > time.time():
                return dict(cached[1])
            self._completed_out.pop(request_id, None)
        pending = self._pending_out.get(request_id)
        if pending is None:
            raise ValueError("unknown pairing request")
        if pending["expires_at"] < time.time():
            return self._complete_outgoing(
                request_id, {"request_id": request_id, "status": "expired"}
            )
        status = await self.client.pair_status(
            pending["address"], pending["port"], request_id
        )
        state = status.get("status", "pending")
        if state == "approved":
            if status.get("code") and status["code"] != pending["code"]:
                self.cancel_outgoing(request_id)
                raise ValueError("pairing code mismatch; pairing aborted")
            info = pending["info"]
            self._save_paired_peer(
                node_id=pending["node_id"],
                name=info.get("name", pending["node_id"]),
                address=pending["address"],
                port=pending["port"],
                public_key=info["public_key"],
                cert_pem=status.get("certificate") or info["certificate"],
                fingerprint=crypto.fingerprint(info["public_key"]),
                secret=bytes.fromhex(pending["key_hex"]),
                os=info.get("os", ""),
                os_version=info.get("os_version", ""),
                hostname=info.get("hostname", ""),
                cpu_model=info.get("cpu_model", ""),
            )
            self.db.audit("user", "pair-approved", pending["node_id"], "paired from local request", None)
            return self._complete_outgoing(
                request_id,
                {"request_id": request_id, "status": "approved", "node_id": pending["node_id"]},
            )
        if state in ("denied", "expired"):
            return self._complete_outgoing(
                request_id, {"request_id": request_id, "status": state}
            )
        return {"request_id": request_id, "status": state}

    def create_inbound_pair_request(self, payload: dict, remote_ip: str) -> dict:
        if not is_lan_address(remote_ip):
            raise ValueError("pairing is only allowed from LAN addresses")
        required = ("node_id", "public_key", "certificate", "eph_public", "req_nonce", "signature")
        for key in required:
            if not payload.get(key):
                raise ValueError(f"pairing request missing {key}")
        node_id = payload["node_id"]
        if node_id == self.identity.node_id:
            raise ValueError("cannot pair with self")
        if self.db.get_peer(node_id):
            return {"status": "already-paired"}
        unsigned = f"{node_id}|{payload['eph_public']}|{payload['req_nonce']}".encode()
        try:
            pub = crypto.load_pub_pem(payload["public_key"])
        except Exception as exc:
            raise ValueError("invalid public key") from exc
        if not crypto.verify(pub, crypto.b64d(payload["signature"]), unsigned):
            raise ValueError("invalid pairing signature")

        eph_priv, eph_pub = crypto.generate_x25519()
        initiator_eph = crypto.b64d(payload["eph_public"])
        shared = crypto.ecdh(eph_priv, initiator_eph)
        key = crypto.pairing_key(shared, node_id, self.identity.node_id)
        now = time.time()
        request_id = crypto.new_share_token()
        self.db.create_pair_request(
            {
                "id": request_id,
                "node_id": node_id,
                "name": payload.get("name") or node_id,
                "os": payload.get("os") or "",
                "os_version": payload.get("os_version") or "",
                "hostname": payload.get("hostname") or "",
                "cpu_model": payload.get("cpu_model") or "",
                "fingerprint": crypto.fingerprint(payload["public_key"]),
                "public_key": payload["public_key"],
                "cert_pem": payload["certificate"],
                # The remote IP is authoritative; the initiator-supplied address
                # would be our own address from their point of view.
                "address": remote_ip,
                "peer_port": int(payload.get("peer_port") or 0),
                "eph_public": crypto.b64e(eph_pub),
                "pair_key_hex": key.hex(),
                "code": crypto.pairing_code(key),
                "created_at": now,
                "expires_at": now + PAIR_TTL_S,
                "status": "pending",
            }
        )
        self.bus.publish("pair_requests", self.db.list_pair_requests())
        self.db.audit("system", "pair-request-in", node_id, "pending local approval", remote_ip)
        return {
            "request_id": request_id,
            "eph_public": crypto.b64e(eph_pub),
            "expires_at": now + PAIR_TTL_S,
            "server_node_id": self.identity.node_id,
        }

    def approve_pair(self, request_id: str, approve: bool) -> dict:
        req = self.db.get_pair_request(request_id)
        if req is None:
            raise ValueError("unknown pairing request")
        if req["expires_at"] < time.time():
            self.db.set_pair_status(request_id, "expired")
            raise ValueError("pairing request expired")
        if not approve:
            self.db.set_pair_status(request_id, "denied")
            self.bus.publish("pair_requests", self.db.list_pair_requests())
            self.db.audit("user", "pair-denied", req["node_id"], None, None)
            return {"status": "denied"}
        secret = bytes.fromhex(req["pair_key_hex"])
        self._save_paired_peer(
            node_id=req["node_id"],
            name=req["name"],
            address=req["address"] or "",
            port=int(req.get("peer_port") or self.cfg.get().peer.port),
            public_key=req["public_key"],
            cert_pem=req["cert_pem"],
            fingerprint=req["fingerprint"],
            secret=secret,
            os=req["os"] or "",
            os_version=req.get("os_version") or "",
            hostname=req.get("hostname") or "",
            cpu_model=req.get("cpu_model") or "",
        )
        self.db.set_pair_status(request_id, "approved")
        self.bus.publish("pair_requests", self.db.list_pair_requests())
        self.db.audit("user", "pair-approve", req["node_id"], "pairing approved", None)
        return {"status": "approved", "certificate": self.identity.cert_pem, "code": req["code"]}

    def _save_paired_peer(
        self,
        *,
        node_id: str,
        name: str,
        address: str,
        port: int,
        public_key: str,
        cert_pem: str,
        fingerprint: str,
        secret: bytes,
        os: str = "",
        os_version: str = "",
        hostname: str = "",
        cpu_model: str = "",
    ) -> None:
        if port <= 0:
            port = self.cfg.get().peer.port
        # On a node installed with --allow-remote-updates the root helper is
        # the operator's standing grant, so new peers may maintain it too.
        allow_control = remote.helper_ready()
        self.client.write_pinned_cert(node_id, cert_pem)
        self.db.upsert_peer(
            {
                "node_id": node_id,
                "name": name,
                "os": os,
                "os_version": os_version,
                "hostname": hostname,
                "cpu_model": cpu_model,
                "address": address,
                "peer_port": port,
                "public_key": public_key,
                "fingerprint": fingerprint,
                "cert_pem": cert_pem,
                "secret_hex": secret.hex(),
                "paired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_seen": None,
                "auto_connect": 1,
                "allow_tasks": 1,
                "allow_storage": 1,
                "allow_updates": 1,
                "allow_control": int(allow_control),
                "status": "offline",
            }
        )
        runtime = PeerRuntime(
            node_id=node_id,
            name=name,
            address=address,
            port=port,
            secret=secret,
            cert_pem=cert_pem,
            fingerprint=fingerprint,
            allow_tasks=True,
            allow_control=allow_control,
        )
        self.peers[node_id] = runtime
        self.discovered.pop(node_id, None)
        runtime.monitor_task = asyncio.create_task(
            self._peer_loop(runtime), name=f"peer-{node_id}"
        )
        self.bus.publish("peers", self.cluster_payload())

    def remove_peer(self, node_id: str) -> None:
        peer = self.peers.pop(node_id, None)
        if peer and peer.monitor_task:
            peer.monitor_task.cancel()
        self.db.delete_peer(node_id)
        cert = self.client.cert_path_for(node_id)
        if cert.exists():
            cert.unlink()
        self.db.audit("user", "peer-remove", node_id, None, None)
        self.bus.publish("peers", self.cluster_payload())

    def set_peer_permission(self, node_id: str, *, allow_tasks: bool | None = None,
                            allow_storage: bool | None = None,
                            allow_updates: bool | None = None,
                            allow_control: bool | None = None,
                            auto_connect: bool | None = None) -> None:
        peer = self.peers.get(node_id)
        if peer is None:
            raise ValueError("unknown peer")
        fields: dict[str, Any] = {}
        if allow_tasks is not None:
            peer.allow_tasks = allow_tasks
            fields["allow_tasks"] = int(allow_tasks)
        if allow_storage is not None:
            peer.allow_storage = allow_storage
            fields["allow_storage"] = int(allow_storage)
        if allow_updates is not None:
            peer.allow_updates = allow_updates
            fields["allow_updates"] = int(allow_updates)
        if allow_control is not None:
            peer.allow_control = allow_control
            fields["allow_control"] = int(allow_control)
        if auto_connect is not None:
            peer.auto_connect = auto_connect
            fields["auto_connect"] = int(auto_connect)
        if fields:
            self.db.execute(
                f"UPDATE peers SET {', '.join(f'{k} = ?' for k in fields)} WHERE node_id = ?",
                [*fields.values(), node_id],
            )
            self.db.audit("user", "peer-permissions", node_id, json.dumps(fields), None)
        self.bus.publish("peers", self.cluster_payload())
        self.push_capabilities(node_id)

    def push_capabilities(self, node_id: str) -> None:
        """Tell a peer what we currently allow it to do (permissions are
        authoritative on the node granting them, so the other side caches)."""
        peer = self.peers.get(node_id)
        if peer is None:
            return
        payload = {
            "type": "capabilities",
            "data": {
                "accepts_tasks": peer.allow_tasks,
                "accepts_storage": peer.allow_storage,
                "serves_updates": peer.allow_updates,
                "accepts_control": peer.allow_control,
            },
        }
        asyncio.create_task(self._send_capabilities(peer, payload), name=f"caps-{node_id}")

    async def _send_capabilities(self, peer: PeerRuntime, payload: dict) -> None:
        sent = False
        if peer.ws is not None:
            try:
                await peer.ws.send_json(payload)
                sent = True
            except Exception:
                pass
        ws = self.inbound_ws.get(peer.node_id)
        if ws is not None:
            try:
                await ws.send_json(payload)
                sent = True
            except Exception:
                pass
        if not sent:
            log.debug("capabilities for %s not pushed (peer offline)", peer.node_id)

    # -- connections -------------------------------------------------------
    async def _peer_loop(self, peer: PeerRuntime) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if not peer.auto_connect:
                await self._sleep(5)
                continue
            try:
                await self._connect_once(peer)
                backoff = 1.0
                await self._ws_session(peer)
                # A peer that closes the websocket immediately must not turn
                # the reconnect loop into a busy loop.
                await self._sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._note_connection_failure(peer)
                peer.last_error = str(exc)[:300]
                log.info("peer %s connection failed: %s", peer.name, exc)
                await self._sleep(min(backoff, 15.0))
                backoff = min(backoff * 1.6, 15.0)

    def _note_connection_failure(self, peer: PeerRuntime) -> None:
        # One dropped websocket is normal on a busy LAN. Flipping to offline on
        # the first error made transfers and scheduling treat a peer that
        # reconnects a second later as unreachable, so require two consecutive
        # failures unless the peer has been silent past the discovery window.
        peer.failures += 1
        stale_after = self.cfg.get().discovery.offline_after_s
        if peer.status == "offline":
            return
        if time.time() - peer.last_seen <= stale_after and peer.failures < 2:
            return
        peer.status = "offline"
        self._publish_peer(peer)

    async def _connect_once(self, peer: PeerRuntime) -> None:
        if not peer.address:
            entry = self.discovered.get(peer.node_id)
            if entry:
                peer.address = entry.address
            else:
                raise PeerConnectionError("no known address")
        start = time.perf_counter()
        info = await self.client.fetch_info(peer.address, peer.port)
        if info.get("node_id") != peer.node_id:
            raise PeerConnectionError("peer identity mismatch (possible spoofing)")
        latency = (time.perf_counter() - start) * 1000.0
        # Authenticated health check proves the pinned secret still matches.
        await self.client.get_json(peer.target(), "/peer/health", timeout=8)
        peer.info = info
        self._sync_peer_metadata(peer, info)
        peer.latency_ms = round(latency, 1)
        peer.last_seen = time.time()
        peer.failures = 0
        try:
            caps = await self.client.get_json(peer.target(), "/peer/capabilities", timeout=8)
            self.apply_capabilities(peer, caps)
        except Exception:
            log.debug("capability sync with %s failed", peer.node_id, exc_info=True)
        if peer.status != "online":
            if peer.online_since is not None:
                peer.reconnects += 1
            peer.status = "online"
            peer.online_since = time.time()
            peer.last_error = None
            self.overload.evaluate_network(peer.node_id, peer.latency_ms)
            self._publish_peer(peer)
            if self.on_peer_connected:
                try:
                    result = self.on_peer_connected(peer.node_id)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    log.debug("on_peer_connected hook failed", exc_info=True)

    @staticmethod
    def apply_capabilities(peer: PeerRuntime, data: dict) -> None:
        peer.remote_accepts_tasks = bool(data.get("accepts_tasks"))
        peer.remote_accepts_storage = bool(data.get("accepts_storage"))
        peer.remote_allows_updates = bool(data.get("serves_updates"))
        peer.remote_allows_control = bool(data.get("accepts_control"))

    def _sync_peer_metadata(self, peer: PeerRuntime, info: dict) -> None:
        """Repair the stored peer row from the live handshake.

        Builds before 1.0.1 echoed the responder's own info in the pairing
        request, so rows paired by them can carry the wrong OS/hostname.
        """
        row = self.db.get_peer(peer.node_id) or {}
        changes: dict[str, Any] = {}
        for key in ("os", "os_version", "hostname", "cpu_model"):
            fresh = (info.get(key) or "").strip()
            if fresh and (row.get(key) or "") != fresh:
                changes[key] = fresh
        name = (info.get("name") or "").strip()
        if name and name != peer.name:
            changes["name"] = name
            peer.name = name
        if not changes:
            return
        self.db.execute(
            f"UPDATE peers SET {', '.join(f'{k} = ?' for k in changes)} WHERE node_id = ?",
            [*changes.values(), peer.node_id],
        )
        log.info("repaired metadata for %s from live handshake", peer.node_id)

    async def _ws_session(self, peer: PeerRuntime) -> None:
        ws = await self.client.open_ws(peer.target())
        peer.ws = ws
        peer.ping_task = asyncio.create_task(
            self._ping_loop(peer, ws), name=f"ping-{peer.node_id}"
        )
        try:
            await ws.send_json(
                {
                    "type": "hello",
                    "node_id": self.identity.node_id,
                    "info": self.identity.info(),
                    "version": VERSION,
                }
            )
            local = self.bus.latest("metrics")
            if local:
                await ws.send_json({"type": "metrics", "data": local})
            while not self._stop.is_set():
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=25)
                except asyncio.TimeoutError:
                    stale_after = self.cfg.get().discovery.offline_after_s
                    if time.time() - peer.last_seen > stale_after:
                        raise PeerConnectionError("heartbeat timeout")
                    try:
                        await ws.send_json({"type": "ping", "ts": time.time()})
                    except Exception as exc:
                        raise PeerConnectionError(f"ping failed: {exc}") from exc
                    continue
                if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED):
                    break
                if msg.type == WSMsgType.ERROR:
                    raise PeerConnectionError("websocket error")
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                await self._handle_peer_message(peer, data)
        finally:
            peer.ws = None
            if peer.ping_task is not None:
                peer.ping_task.cancel()
                peer.ping_task = None
            try:
                await ws.close()
            except Exception:
                pass

    async def _ping_loop(self, peer: PeerRuntime, ws: Any) -> None:
        """Sample round-trip latency even while metrics keep the socket busy.

        Without this the displayed latency is whatever the handshake measured,
        which is stale the moment the link degrades.
        """
        while not self._stop.is_set() and peer.ws is ws:
            await self._sleep(10.0)
            if peer.ws is not ws:
                return
            try:
                await ws.send_json({"type": "ping", "ts": time.time()})
            except Exception:
                return

    async def _handle_peer_message(self, peer: PeerRuntime, msg: dict) -> None:
        kind = msg.get("type")
        peer.last_seen = time.time()
        peer.failures = 0
        if kind == "metrics":
            peer.metrics = msg.get("data")
            if peer.status != "online":
                peer.status = "online"
            self.bus.publish(f"peer_metrics:{peer.node_id}", peer.metrics)
            self._publish_peer(peer)
        elif kind == "pong":
            sent = msg.get("ts")
            if isinstance(sent, (int, float)):
                peer.latency_ms = round((time.time() - sent) * 1000.0, 1)
                self._publish_peer(peer)
        elif kind == "hello":
            peer.info = msg.get("info")
            if peer.info:
                self._sync_peer_metadata(peer, peer.info)
            self._publish_peer(peer)
        elif kind == "ping":
            if peer.ws is not None:
                try:
                    await peer.ws.send_json({"type": "pong", "ts": msg.get("ts")})
                except Exception:
                    pass
        elif kind == "capabilities":
            self.apply_capabilities(peer, msg.get("data") or {})
            self._publish_peer(peer)
        elif kind == "task_update" and self.on_remote_task_update:
            try:
                result = self.on_remote_task_update(peer.node_id, msg.get("data") or {})
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("remote task update failed")

    async def broadcast_metrics(self, snapshot: dict) -> None:
        for peer in self.peers.values():
            if peer.ws is not None:
                try:
                    await peer.ws.send_json({"type": "metrics", "data": snapshot})
                except Exception:
                    pass
        for node_id, ws in list(self.inbound_ws.items()):
            try:
                await ws.send_json({"type": "metrics", "data": snapshot})
            except Exception:
                self.inbound_ws.pop(node_id, None)

    def register_inbound(self, node_id: str, ws: Any) -> None:
        self.inbound_ws[node_id] = ws
        peer = self.peers.get(node_id)
        if peer:
            peer.status = "online"
            peer.last_seen = time.time()
            self._publish_peer(peer)

    def unregister_inbound(self, node_id: str, ws: Any) -> None:
        if self.inbound_ws.get(node_id) is ws:
            self.inbound_ws.pop(node_id, None)

    def apply_inbound_metrics(self, node_id: str, data: dict) -> None:
        peer = self.peers.get(node_id)
        if peer is None:
            return
        peer.metrics = data
        peer.last_seen = time.time()
        if peer.status != "online":
            peer.status = "online"
        self.bus.publish(f"peer_metrics:{node_id}", data)
        self._publish_peer(peer)

    async def send_task_update(self, node_id: str, payload: dict) -> bool:
        peer = self.peers.get(node_id)
        if peer is None or peer.status != "online":
            return False
        # Results can be much larger than a heartbeat. Sending those over the
        # websocket would trip the receiver's frame limit and take the whole
        # session down, so anything past the threshold goes over HTTP.
        size = len(json.dumps(payload, default=str).encode("utf-8"))
        if (
            peer.ws is not None
            and size <= self.WS_TASK_UPDATE_LIMIT
        ):
            try:
                await peer.ws.send_json({"type": "task_update", "data": payload})
                return True
            except Exception:
                pass
        try:
            await self.client.post_json(
                peer.target(),
                "/peer/tasks/update",
                payload,
                timeout=60 if size > self.WS_TASK_UPDATE_LIMIT else 15,
            )
            return True
        except Exception as exc:
            log.info("task update to %s failed: %s", node_id, exc)
            return False

    async def submit_task(self, node_id: str, payload: dict) -> dict:
        peer = self.peers.get(node_id)
        if peer is None:
            raise ValueError("unknown peer")
        if peer.status != "online":
            raise ValueError("peer is offline")
        if not peer.remote_accepts_tasks:
            raise ValueError("task execution is not authorized by the remote node")
        return await self.client.post_json(peer.target(), "/peer/tasks", payload, timeout=20)

    # -- periodic tasks ----------------------------------------------------
    async def _bus_loop(self) -> None:
        queue = await self.bus.subscribe()
        try:
            while not self._stop.is_set():
                event = await queue.get()
                if event["topic"] == "metrics":
                    await self.broadcast_metrics(event["data"])
        except asyncio.CancelledError:
            pass
        finally:
            self.bus.unsubscribe(queue)

    async def _manual_peer_loop(self) -> None:
        while not self._stop.is_set():
            for entry in list(self.cfg.get().discovery.manual_peers):
                address, _, port_s = entry.partition(":")
                port = int(port_s) if port_s.isdigit() else self.cfg.get().peer.port
                if not is_lan_address(address):
                    continue
                if any(p.address == address for p in self.peers.values()):
                    continue
                try:
                    info = await self.client.fetch_info(address, port)
                    if info.get("node_id") == self.identity.node_id:
                        continue
                    await self.handle_discovered(
                        {
                            "node_id": info["node_id"],
                            "name": info.get("name", info["node_id"]),
                            "peer_port": int(info.get("peer_port") or port),
                            "os": info.get("os", ""),
                            "version": info.get("version", ""),
                            "fingerprint": info.get("fingerprint", ""),
                            "source": "manual",
                        },
                        address,
                    )
                except Exception:
                    log.debug("manual peer probe %s failed", address)
            await self._sleep(20)

    async def _pair_cleanup_loop(self) -> None:
        while not self._stop.is_set():
            self.db.prune_pair_requests(time.time())
            for rid, pending in list(self._pending_out.items()):
                if pending["expires_at"] < time.time():
                    self._pending_out.pop(rid, None)
            await self._sleep(30)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # -- views -------------------------------------------------------------
    def _publish_peer(self, peer: PeerRuntime) -> None:
        self.bus.publish("peer_status", self._peer_view(peer))
        self.bus.publish("peers", self.cluster_payload())

    def _peer_view(self, peer: PeerRuntime) -> dict:
        info = peer.info or {}
        return {
            "node_id": peer.node_id,
            "name": peer.name,
            "address": peer.address,
            "peer_port": peer.port,
            "status": peer.status,
            "last_seen": peer.last_seen,
            "online_since": peer.online_since,
            "latency_ms": peer.latency_ms,
            "failures": peer.failures,
            "reconnects": peer.reconnects,
            "last_error": peer.last_error,
            "fingerprint": peer.fingerprint,
            "os": info.get("os", ""),
            "os_version": info.get("os_version", ""),
            "hostname": info.get("hostname", ""),
            "cpu_model": info.get("cpu_model", ""),
            "cores_physical": info.get("cpu_cores_physical"),
            "cores_logical": info.get("cpu_cores_logical"),
            "version": info.get("version", ""),
            "allow_tasks": peer.allow_tasks,
            "allow_storage": peer.allow_storage,
            "allow_updates": peer.allow_updates,
            "allow_control": peer.allow_control,
            "remote_accepts_tasks": peer.remote_accepts_tasks,
            "remote_accepts_storage": peer.remote_accepts_storage,
            "remote_allows_updates": peer.remote_allows_updates,
            "remote_allows_control": peer.remote_allows_control,
            "auto_connect": peer.auto_connect,
            "metrics": peer.metrics,
            "pressure": self.pressure_for(peer.node_id),
        }

    def pressure_for(self, node_id: str) -> dict | None:
        peer = self.peers.get(node_id)
        if peer is None or not peer.metrics:
            return None
        m = peer.metrics
        cpu = float(m.get("cpu", {}).get("percent", 0))
        ram = float(m.get("ram", {}).get("percent", 0))
        temps = m.get("temps") or []
        max_temp = max((t["current"] for t in temps), default=None)
        return {
            "node_id": node_id,
            "cpu_percent": cpu,
            "ram_percent": ram,
            "max_temp_c": max_temp,
            "score": round(cpu * 0.55 + ram * 0.35 + (max(0.0, (max_temp - 50) / 50 * 20) if max_temp else 0.0), 2),
        }

    def cluster_payload(self) -> dict:
        peers = [self._peer_view(p) for p in self.peers.values()]
        local_metrics = self.bus.latest("metrics")
        return {
            "local": {
                "node_id": self.identity.node_id,
                "name": self.cfg.get().node_name,
                "metrics": local_metrics,
                "pressure": self.overload.pressure.as_dict(),
                "fingerprint": self.identity.fingerprint,
            },
            "peers": peers,
            "discovered": self.discovered_payload(),
            "online": 1 + sum(1 for p in self.peers.values() if p.status == "online"),
            "paired": len(self.peers),
        }
