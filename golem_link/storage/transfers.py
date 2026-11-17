"""Chunked, resumable, checksum-verified file transfers between paired peers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import aiohttp

from ..config import ConfigManager
from ..db import Database
from ..events import EventBus
from ..utils import human_bytes, safe_resolve
from .shares import StorageManager

log = logging.getLogger(__name__)

CHUNK = 4 * 1024 * 1024


class TransferManager:
    def __init__(
        self,
        cfg: ConfigManager,
        db: Database,
        bus: EventBus,
        storage: StorageManager,
    ) -> None:
        self.cfg = cfg
        self.db = db
        self.bus = bus
        self.storage = storage
        self.peers = None  # set by app
        self._tasks: dict[str, asyncio.Task] = {}
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=10))
        return self._session

    async def close(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        if self._session and not self._session.closed:
            await self._session.close()

    # -- views -------------------------------------------------------------
    def _view(self, row: dict) -> dict:
        view = dict(row)
        elapsed = max((view.get("finished_at") or time.time()) - view["started_at"], 0.001)
        view["speed_bps"] = round(view["transferred"] / elapsed, 1) if view["transferred"] else 0
        view["human_size"] = human_bytes(view["size"])
        view["human_transferred"] = human_bytes(view["transferred"])
        if self.peers is not None:
            peer = self.peers.peers.get(view.get("peer_node") or "")
            view["peer_name"] = peer.name if peer else view.get("peer_node")
        return view

    def list_transfers(self, limit: int = 100) -> list[dict]:
        return [self._view(r) for r in self.db.list_transfers(limit)]

    def get_transfer(self, transfer_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM transfers WHERE id = ?", (transfer_id,))
        return self._view(row) if row else None

    def _update(self, transfer_id: str, **fields: Any) -> None:
        self.db.update_transfer(transfer_id, **fields)
        row = self.db.query_one("SELECT * FROM transfers WHERE id = ?", (transfer_id,))
        if row:
            self.bus.publish("transfer", self._view(row))

    # -- download ----------------------------------------------------------
    async def start_download(self, peer_node: str, share_id: str, rel_path: str) -> dict:
        peer = await self._wait_for_peer(peer_node, need_storage=True)
        local_root = (self.cfg.data_dir / "downloads" / peer_node / share_id).resolve()
        local_root.mkdir(parents=True, exist_ok=True)
        rel = Path(rel_path.lstrip("/\\"))
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("invalid relative path")
        dest = safe_resolve(rel, local_root)
        transfer_id = f"x-{uuid.uuid4().hex[:12]}"
        self.db.insert_transfer(
            {
                "id": transfer_id,
                "direction": "download",
                "peer_node": peer_node,
                "share_id": share_id,
                "rel_path": rel_path,
                "size": 0,
                "transferred": 0,
                "status": "running",
                "started_at": time.time(),
            }
        )
        task = asyncio.create_task(
            self._download(transfer_id, peer, share_id, rel_path, dest),
            name=f"dl-{transfer_id}",
        )
        self._tasks[transfer_id] = task
        self._publish_start(transfer_id)
        return self.get_transfer(transfer_id) or {}

    def _publish_start(self, transfer_id: str) -> None:
        view = self.get_transfer(transfer_id)
        if view:
            self.bus.publish("transfer", view)

    def _peer(self, node_id: str, need_storage: bool = False):
        if self.peers is None:
            raise ValueError("peer manager not ready")
        peer = self.peers.peers.get(node_id)
        if peer is None:
            raise ValueError("unknown peer")
        if peer.status != "online":
            raise ValueError("peer is offline")
        if need_storage and not peer.remote_accepts_storage:
            raise ValueError("storage access is not authorized by the remote node")
        return peer

    async def _wait_for_peer(self, node_id: str, need_storage: bool = False):
        # The reconnect loop may briefly report a reachable node as offline
        # while it retries. Give a recently-seen peer a few seconds to come
        # back instead of failing a transfer that would start fine.
        deadline = time.monotonic() + 5.0
        while True:
            try:
                return self._peer(node_id, need_storage)
            except ValueError:
                peer = self.peers.peers.get(node_id) if self.peers else None
                stale_after = self.cfg.get().discovery.offline_after_s
                recently_seen = peer is not None and time.time() - peer.last_seen <= stale_after
                if peer is None or peer.status != "offline" or not recently_seen:
                    raise
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.15)

    async def _download(self, transfer_id: str, peer, share_id: str, rel_path: str, dest: Path) -> None:
        part = dest.with_name(dest.name + ".glpart")
        dest.parent.mkdir(parents=True, exist_ok=True)
        offset = part.stat().st_size if part.exists() else 0
        session = await self._get_session()
        url = (
            f"{self.peers.client.base_url(peer.target())}/peer/storage/{share_id}/download"
        )
        try:
            params = {"path": rel_path, "offset": str(offset)}
            async with session.get(
                url,
                params=params,
                headers=self.peers.client.auth_headers(peer.target()),
                ssl=self.peers.client.pinned_ssl(peer.target()),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"download failed: HTTP {resp.status} {body[:300]}")
                size = int(resp.headers.get("X-Golem-Size", "0"))
                expected_sha = resp.headers.get("X-Golem-Sha256", "")
                server_offset = int(resp.headers.get("X-Golem-Offset", "0"))
                if server_offset != offset:
                    offset = server_offset
                self._update(transfer_id, size=size, transferred=offset)
                mode = "r+b" if part.exists() else "wb"
                with part.open(mode) as fh:
                    fh.seek(offset)
                    fh.truncate(offset)
                    transferred = offset
                    last = time.time()
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        transferred += len(chunk)
                        if time.time() - last > 0.5:
                            last = time.time()
                            self._update(transfer_id, transferred=transferred)
                if transferred != size:
                    raise RuntimeError(f"incomplete download: {transferred}/{size} bytes")
            actual = await asyncio.to_thread(_sha256_file, part)
            if expected_sha and actual != expected_sha:
                part.unlink(missing_ok=True)
                raise RuntimeError("checksum mismatch; partial file discarded")
            os.replace(part, dest)
            self._update(
                transfer_id,
                status="completed",
                transferred=size,
                sha256=actual,
                finished_at=time.time(),
            )
            self.db.audit("peer", "download", f"{share_id}:{rel_path}", f"{size} bytes from {peer.name}", None)
        except asyncio.CancelledError:
            self._update(transfer_id, status="cancelled", error="cancelled by user", finished_at=time.time())
            raise
        except Exception as exc:
            log.warning("download %s failed: %s", transfer_id, exc)
            self._update(transfer_id, status="failed", error=str(exc)[:500], finished_at=time.time())
        finally:
            self._tasks.pop(transfer_id, None)

    # -- upload ------------------------------------------------------------
    async def start_upload(
        self, peer_node: str, share_id: str, rel_path: str, local_path: str, overwrite: bool = False
    ) -> dict:
        peer = await self._wait_for_peer(peer_node, need_storage=True)
        source = Path(local_path).resolve()
        if not source.is_file():
            raise ValueError("local file not found")
        size = source.stat().st_size
        transfer_id = f"x-{uuid.uuid4().hex[:12]}"
        self.db.insert_transfer(
            {
                "id": transfer_id,
                "direction": "upload",
                "peer_node": peer_node,
                "share_id": share_id,
                "rel_path": rel_path,
                "size": size,
                "transferred": 0,
                "status": "running",
                "started_at": time.time(),
            }
        )
        task = asyncio.create_task(
            self._upload(transfer_id, peer, share_id, rel_path, source, size, overwrite),
            name=f"ul-{transfer_id}",
        )
        self._tasks[transfer_id] = task
        self._publish_start(transfer_id)
        return self.get_transfer(transfer_id) or {}

    async def _upload(
        self,
        transfer_id: str,
        peer,
        share_id: str,
        rel_path: str,
        source: Path,
        size: int,
        overwrite: bool,
    ) -> None:
        session = await self._get_session()
        target = peer.target()
        base = self.peers.client.base_url(target)
        url = f"{base}/peer/storage/{share_id}/upload"
        try:
            digest = await asyncio.to_thread(_sha256_file, source)
            sent = 0
            attempts = 0
            with source.open("rb") as fh:
                while sent < size:
                    fh.seek(sent)
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    final = sent + len(chunk) >= size
                    params = {
                        "path": rel_path,
                        "offset": str(sent),
                        "final": "1" if final else "0",
                        "overwrite": "1" if overwrite else "0",
                    }
                    headers = self.peers.client.auth_headers(target)
                    headers["X-Golem-Sha256"] = digest
                    try:
                        async with session.put(
                            url, params=params, data=chunk, headers=headers,
                            ssl=self.peers.client.pinned_ssl(target),
                            timeout=aiohttp.ClientTimeout(total=300),
                        ) as resp:
                            if resp.status == 409:
                                info = await resp.json()
                                expected = int(info.get("expected_offset", sent))
                                if expected != sent:
                                    sent = expected
                                    attempts = 0
                                    continue
                            if resp.status != 200:
                                body = await resp.text()
                                raise RuntimeError(f"upload failed: HTTP {resp.status} {body[:300]}")
                            data = await resp.json()
                            sent = int(data.get("received", sent + len(chunk)))
                            attempts = 0
                            self._update(transfer_id, transferred=sent)
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        attempts += 1
                        if attempts > 3:
                            raise
                        log.info("upload %s retry %s: %s", transfer_id, attempts, exc)
                        await asyncio.sleep(min(2.0 * attempts, 8.0))
                        status = await self._upload_status(session, base, share_id, rel_path, target)
                        sent = int(status.get("part_size", sent))
            if sent < size:
                raise RuntimeError(f"upload incomplete: {sent}/{size}")
            self._update(
                transfer_id,
                status="completed",
                transferred=size,
                sha256=digest,
                finished_at=time.time(),
            )
            self.db.audit("peer", "upload", f"{share_id}:{rel_path}", f"{size} bytes to {peer.name}", None)
        except asyncio.CancelledError:
            self._update(transfer_id, status="cancelled", error="cancelled by user", finished_at=time.time())
            raise
        except Exception as exc:
            log.warning("upload %s failed: %s", transfer_id, exc)
            self._update(transfer_id, status="failed", error=str(exc)[:500], finished_at=time.time())
        finally:
            self._tasks.pop(transfer_id, None)

    async def _upload_status(self, session, base: str, share_id: str, rel_path: str, target) -> dict:
        try:
            async with session.get(
                f"{base}/peer/storage/{share_id}/upload_status",
                params={"path": rel_path},
                headers=self.peers.client.auth_headers(target),
                ssl=self.peers.client.pinned_ssl(target),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        return {}

    # -- control -----------------------------------------------------------
    def cancel(self, transfer_id: str) -> dict:
        task = self._tasks.get(transfer_id)
        if task is None:
            row = self.db.query_one("SELECT * FROM transfers WHERE id = ?", (transfer_id,))
            if row is None:
                raise ValueError("unknown transfer")
            return self._view(row)
        task.cancel()
        return {"id": transfer_id, "status": "cancelling"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
