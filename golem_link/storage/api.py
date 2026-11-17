"""Peer-facing storage API (authenticated, path-confined, checksum-verified)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import crypto

router = APIRouter(prefix="/storage")


def _peer(request: Request):
    ctx = request.app.state.ctx
    node_id = request.headers.get("X-Golem-Node")
    token = request.headers.get("X-Golem-Token")
    if not node_id or not token:
        raise HTTPException(status_code=401, detail="missing peer credentials")
    peer = ctx.peers.peers.get(node_id)
    if peer is None:
        raise HTTPException(status_code=401, detail="unknown peer")
    if not crypto.verify_token(peer.secret, token, node_id, ctx.identity.node_id):
        raise HTTPException(status_code=401, detail="invalid or expired token")
    if not peer.allow_storage:
        raise HTTPException(status_code=403, detail="storage access is not authorized for this peer")
    peer.last_seen = time.time()
    return peer


def _path(ctx, share_id: str, rel_path: str) -> Path:
    try:
        return ctx.storage.resolve(share_id, rel_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@router.get("/{share_id}/list")
async def list_directory(share_id: str, request: Request, path: str = ""):
    _peer(request)
    ctx = request.app.state.ctx
    try:
        return {"share_id": share_id, "path": path, "entries": ctx.storage.list_dir(share_id, path)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{share_id}/stat")
async def stat_path(share_id: str, request: Request, path: str = ""):
    _peer(request)
    ctx = request.app.state.ctx
    target = _path(ctx, share_id, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="path not found")
    st = target.stat()
    return {
        "path": str(target),
        "name": target.name,
        "is_dir": target.is_dir(),
        "size": 0 if target.is_dir() else st.st_size,
        "mtime": st.st_mtime,
    }


@router.get("/{share_id}/download")
async def download(share_id: str, request: Request, path: str, offset: int = 0):
    _peer(request)
    ctx = request.app.state.ctx
    target = _path(ctx, share_id, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    size = target.stat().st_size
    digest = await asyncio.to_thread(_sha256_file, target)
    offset = max(0, min(offset, size))

    def iterator():
        with target.open("rb") as fh:
            fh.seek(offset)
            while chunk := fh.read(256 * 1024):
                yield chunk

    headers = {
        "X-Golem-Size": str(size),
        "X-Golem-Sha256": digest,
        "X-Golem-Offset": str(offset),
        "Content-Length": str(size - offset),
        "Content-Disposition": f'attachment; filename="{target.name}"',
    }
    return StreamingResponse(iterator(), media_type="application/octet-stream", headers=headers)


def _part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".glpart")


@router.get("/{share_id}/upload_status")
async def upload_status(share_id: str, request: Request, path: str):
    _peer(request)
    ctx = request.app.state.ctx
    dest = _path(ctx, share_id, path)
    part = _part_path(dest)
    return {
        "part_size": part.stat().st_size if part.exists() else 0,
        "exists": dest.exists(),
        "size": dest.stat().st_size if dest.exists() else 0,
    }


@router.put("/{share_id}/upload")
async def upload(
    share_id: str,
    request: Request,
    path: str,
    offset: int = 0,
    final: int = 0,
    overwrite: int = 0,
):
    _peer(request)
    ctx = request.app.state.ctx
    try:
        ctx.storage.require_writable(share_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dest = _path(ctx, share_id, path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = _part_path(dest)
    part_size = part.stat().st_size if part.exists() else 0
    if offset != part_size:
        raise HTTPException(status_code=409, detail={"expected_offset": part_size, "error": "offset mismatch"})
    if offset == 0 and dest.exists() and not overwrite:
        raise HTTPException(status_code=409, detail={"error": "destination exists", "expected_offset": 0})
    received = part_size
    try:
        with part.open("ab") as fh:
            async for chunk in request.stream():
                if not chunk:
                    continue
                fh.write(chunk)
                received += len(chunk)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"write failed: {exc}") from exc
    if not final:
        return {"received": received, "completed": False}
    actual = await asyncio.to_thread(_sha256_file, part)
    expected = request.headers.get("X-Golem-Sha256", "")
    if expected and actual != expected:
        part.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="checksum mismatch; partial file removed")
    if dest.exists() and not overwrite:
        raise HTTPException(status_code=409, detail={"error": "destination exists", "expected_offset": received})
    try:
        os.replace(part, dest)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"finalize failed: {exc}") from exc
    ctx.db.audit("peer", "upload-received", f"{share_id}:{path}", f"{received} bytes sha256={actual}", None)
    return {"received": received, "completed": True, "size": received, "sha256": actual}


@router.delete("/{share_id}/path")
async def delete_path(share_id: str, request: Request, path: str, force: int = 0):
    _peer(request)
    ctx = request.app.state.ctx
    try:
        ctx.storage.require_delete(share_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    target = _path(ctx, share_id, path)
    if target == Path(ctx.storage.get_share(share_id).path).resolve():
        raise HTTPException(status_code=400, detail="refusing to delete the share root")
    if not target.exists():
        raise HTTPException(status_code=404, detail="path not found")
    try:
        if target.is_dir():
            if not force:
                raise HTTPException(status_code=400, detail="directory deletion requires force=1")
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"delete failed: {exc}") from exc
    ctx.db.audit("peer", "delete", f"{share_id}:{path}", f"force={force}", None)
    return {"deleted": True}


class MkdirBody(BaseModel):
    path: str = Field(min_length=1)


@router.post("/{share_id}/mkdir")
async def mkdir(share_id: str, body: MkdirBody, request: Request):
    _peer(request)
    ctx = request.app.state.ctx
    try:
        ctx.storage.require_writable(share_id)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    target = _path(ctx, share_id, body.path)
    try:
        target.mkdir(parents=False, exist_ok=False)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="already exists") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"created": str(target)}


class RenameBody(BaseModel):
    path: str = Field(min_length=1)
    new_name: str = Field(min_length=1, max_length=255)


@router.post("/{share_id}/rename")
async def rename(share_id: str, body: RenameBody, request: Request):
    _peer(request)
    ctx = request.app.state.ctx
    try:
        ctx.storage.require_writable(share_id)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if "/" in body.new_name or "\\" in body.new_name or body.new_name in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid new name")
    source = _path(ctx, share_id, body.path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="path not found")
    dest = source.with_name(body.new_name)
    try:
        os.rename(source, dest)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"rename failed: {exc}") from exc
    return {"renamed": str(dest)}
