from __future__ import annotations

import hashlib
import os
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import aiohttp

from .. import crypto
from ..config import ConfigManager
from ..identity import NodeIdentity


class PeerAuthError(Exception):
    pass


class PeerConnectionError(Exception):
    pass


@dataclass
class PeerTarget:
    node_id: str
    address: str
    port: int
    cert_pem: str | None = None
    secret: bytes | None = None
    fingerprint: str | None = None


@dataclass
class PairResult:
    request_id: str
    code: str
    key_hex: str
    expires_at: float
    info: dict[str, Any] = field(default_factory=dict)


class PeerClient:
    """HTTP/WS client for peer APIs.

    Paired calls use TLS with the peer certificate pinned (the exact
    self-signed certificate is the trust anchor) plus an HMAC token. Calls made
    while pairing use TLS without verification, because no trust exists yet;
    the human-compared 6-digit code is what binds the pairing.
    """

    def __init__(self, identity: NodeIdentity, cfg: ConfigManager) -> None:
        self.identity = identity
        self.cfg = cfg
        self._session: aiohttp.ClientSession | None = None
        self._pending_pair: dict[str, PairResult] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20, connect=6)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # -- TLS contexts ------------------------------------------------------
    def _pinned_ssl(self, target: PeerTarget) -> ssl.SSLContext:
        if not target.cert_pem:
            raise PeerConnectionError("missing pinned certificate for peer")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=target.cert_pem)
        return ctx

    @staticmethod
    def _trusting_ssl() -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _base(self, target: PeerTarget) -> str:
        return f"https://{target.address}:{target.port}"

    def pinned_ssl(self, target: PeerTarget) -> ssl.SSLContext:
        return self._pinned_ssl(target)

    def auth_headers(self, target: PeerTarget) -> dict[str, str]:
        return self._auth_headers(target)

    def base_url(self, target: PeerTarget) -> str:
        return self._base(target)

    def _auth_headers(self, target: PeerTarget) -> dict[str, str]:
        if target.secret is None:
            raise PeerAuthError(f"peer {target.node_id} is not paired")
        token = crypto.make_token(target.secret, self.identity.node_id, target.node_id, ttl_s=600)
        return {"X-Golem-Node": self.identity.node_id, "X-Golem-Token": token}

    # -- unauthenticated ---------------------------------------------------
    async def fetch_info(self, address: str, port: int) -> dict:
        session = await self._get_session()
        try:
            async with session.get(
                f"https://{address}:{port}/peer/info", ssl=self._trusting_ssl()
            ) as resp:
                if resp.status != 200:
                    raise PeerConnectionError(f"info request failed: HTTP {resp.status}")
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise PeerConnectionError(str(exc)) from exc

    # -- pairing -----------------------------------------------------------
    async def request_pair(self, address: str, port: int, info: dict) -> PairResult:
        session = await self._get_session()
        eph_priv, eph_pub = crypto.generate_x25519()
        # `info` describes the machine we are pairing *with*; our own metadata
        # must come from our identity or the responder stores it as its own.
        own = self.identity.info()
        payload = {
            "node_id": self.identity.node_id,
            "name": self.cfg.get().node_name,
            "os": own.get("os") or "",
            "os_version": own.get("os_version") or "",
            "hostname": own.get("hostname") or "",
            "cpu_model": own.get("cpu_model") or "",
            "public_key": self.identity.public_pem,
            "certificate": self.identity.cert_pem,
            "peer_port": self.cfg.get().peer.port,
            "eph_public": crypto.b64e(eph_pub),
            "req_nonce": crypto.b64e(crypto.new_secret(16)),
        }
        unsigned = "|".join(
            [payload["node_id"], payload["eph_public"], payload["req_nonce"]]
        )
        payload["signature"] = self.identity.sign(unsigned.encode())
        try:
            async with session.post(
                f"https://{address}:{port}/peer/pair/request",
                json=payload,
                ssl=self._trusting_ssl(),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise PeerConnectionError(f"pairing rejected: HTTP {resp.status} {text[:200]}")
                data = await resp.json()
                if data.get("status") == "already-paired":
                    raise PeerConnectionError(
                        "that machine is already paired with this node; remove the peer "
                        "in Devices if you need to pair it again"
                    )
        except aiohttp.ClientError as exc:
            raise PeerConnectionError(str(exc)) from exc

        peer_eph = crypto.b64d(data["eph_public"])
        shared = crypto.ecdh(eph_priv, peer_eph)
        key = crypto.pairing_key(shared, self.identity.node_id, info["node_id"])
        result = PairResult(
            request_id=data["request_id"],
            code=crypto.pairing_code(key),
            key_hex=key.hex(),
            expires_at=float(data["expires_at"]),
            info=info,
        )
        self._pending_pair[result.request_id] = result
        return result

    async def pair_status(self, address: str, port: int, request_id: str) -> dict:
        session = await self._get_session()
        try:
            async with session.get(
                f"https://{address}:{port}/peer/pair/status/{request_id}",
                ssl=self._trusting_ssl(),
            ) as resp:
                if resp.status != 200:
                    raise PeerConnectionError(f"pair status failed: HTTP {resp.status}")
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise PeerConnectionError(str(exc)) from exc

    def pending_pair(self, request_id: str) -> PairResult | None:
        return self._pending_pair.get(request_id)

    def drop_pending(self, request_id: str) -> None:
        self._pending_pair.pop(request_id, None)

    # -- authenticated -----------------------------------------------------
    async def get_json(self, target: PeerTarget, path: str, timeout: float | None = None) -> Any:
        session = await self._get_session()
        kwargs = {"headers": self._auth_headers(target), "ssl": self._pinned_ssl(target)}
        if timeout:
            kwargs["timeout"] = aiohttp.ClientTimeout(total=timeout)
        try:
            async with session.get(f"{self._base(target)}{path}", **kwargs) as resp:
                if resp.status == 401:
                    raise PeerAuthError("peer rejected authentication")
                if resp.status != 200:
                    raise PeerConnectionError(f"HTTP {resp.status}: {await resp.text()}")
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise PeerConnectionError(str(exc)) from exc

    async def post_json(
        self, target: PeerTarget, path: str, payload: dict, timeout: float = 60
    ) -> Any:
        session = await self._get_session()
        try:
            async with session.post(
                f"{self._base(target)}{path}",
                json=payload,
                headers=self._auth_headers(target),
                ssl=self._pinned_ssl(target),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 401:
                    raise PeerAuthError("peer rejected authentication")
                if resp.status != 200:
                    raise PeerConnectionError(f"HTTP {resp.status}: {await resp.text()}")
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise PeerConnectionError(str(exc)) from exc

    async def download_to(
        self,
        target: PeerTarget,
        path: str,
        dest: Path,
        *,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> dict:
        """Stream a peer file to ``dest`` and verify it before publishing it.

        Nothing is ever returned under ``dest``: data lands in ``dest.part``
        and is renamed only after the size and SHA-256 match.
        """
        session = await self._get_session()
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        digest = hashlib.sha256()
        total = 0
        try:
            async with session.get(
                f"{self._base(target)}{path}",
                headers=self._auth_headers(target),
                ssl=self._pinned_ssl(target),
                timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=120),
            ) as resp:
                if resp.status == 401:
                    raise PeerAuthError("peer rejected authentication")
                if resp.status != 200:
                    raise PeerConnectionError(f"HTTP {resp.status}: {await resp.text()}")
                length = resp.content_length
                with part.open("wb") as handle:
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        handle.write(chunk)
                        digest.update(chunk)
                        total += len(chunk)
                        if progress is not None:
                            progress(total, length)
            if expected_size is not None and total != int(expected_size):
                raise PeerConnectionError(
                    f"download size mismatch: expected {expected_size}, received {total}"
                )
            actual = digest.hexdigest()
            if expected_sha256 and actual != expected_sha256.lower():
                raise PeerConnectionError("download failed SHA-256 verification")
            os.replace(part, dest)
            return {"path": str(dest), "size": total, "sha256": actual}
        except aiohttp.ClientError as exc:
            part.unlink(missing_ok=True)
            raise PeerConnectionError(str(exc)) from exc
        except Exception:
            part.unlink(missing_ok=True)
            raise

    async def open_ws(self, target: PeerTarget):
        session = await self._get_session()
        token = crypto.make_token(target.secret or b"", self.identity.node_id, target.node_id, ttl_s=600)
        url = (
            f"wss://{target.address}:{target.port}/peer/ws"
            f"?node={self.identity.node_id}&token={token}"
        )
        return await session.ws_connect(
            url,
            ssl=self._pinned_ssl(target),
            heartbeat=15,
            timeout=10,
            max_msg_size=2 * 1024 * 1024,
        )

    async def ping(self, target: PeerTarget) -> float:
        """Round-trip latency in ms, measured with the public info endpoint."""
        import time

        start = time.perf_counter()
        await self.fetch_info(target.address, target.port)
        return (time.perf_counter() - start) * 1000.0

    def cert_path_for(self, node_id: str) -> Path:
        return self.cfg.peers_dir / f"{node_id}.pem"

    def write_pinned_cert(self, node_id: str, cert_pem: str) -> None:
        path = self.cert_path_for(node_id)
        path.write_text(cert_pem, encoding="ascii")
        try:
            import os

            os.chmod(path, 0o600)
        except OSError:
            pass
