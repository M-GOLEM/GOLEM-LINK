"""Peer-facing update API: manifest, verified artifact download and remote maintenance.

Two separate grants keep this honest:

* ``allow_updates`` (default on) lets a paired node read the manifest and
  download an artifact. Artifacts are inert installers that still have to pass
  SHA-256 on the receiving side.
* ``allow_control`` (default off) lets a paired node ask this node to check,
  apply an update or restart. The operator opts in per device.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import crypto
from ..update import verify_artifact
from ..version import VERSION

router = APIRouter(prefix="/update")


def _peer(request: Request, permission: str | None = None):
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
    if permission == "updates" and not peer.allow_updates:
        raise HTTPException(status_code=403, detail="this peer may not pull updates from this node")
    if permission == "control" and not peer.allow_control:
        raise HTTPException(
            status_code=403,
            detail="remote control is not granted; enable it for this device on the target node",
        )
    peer.last_seen = time.time()
    return peer


@router.get("/manifest")
async def update_manifest(request: Request):
    _peer(request, "updates")
    ctx = request.app.state.ctx
    manifest = ctx.updates.read_local_manifest()
    if manifest is None:
        raise HTTPException(status_code=404, detail="this node has no update manifest")
    return {
        "manifest": manifest,
        "node_id": ctx.identity.node_id,
        "name": ctx.cfg.get().node_name,
        "version": VERSION,
    }


@router.get("/artifact/{name}")
async def update_artifact(name: str, request: Request):
    _peer(request, "updates")
    ctx = request.app.state.ctx
    manager = ctx.updates
    manifest = manager.read_local_manifest()
    if manifest is None:
        raise HTTPException(status_code=404, detail="this node has no update manifest")
    entry = next(
        (item for item in manifest["artifacts"].values() if item["file"] == name), None
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown update artifact")
    # ``name`` is a basename validated by normalize_manifest; source_dir is the
    # operator's update folder, never a request-controlled path.
    path = manager.source_dir / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact file is missing on this node")
    if not verify_artifact(path, entry["sha256"], entry.get("size")):
        raise HTTPException(status_code=409, detail="artifact does not match the published SHA-256")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,
        headers={"X-Golem-Sha256": entry["sha256"], "X-Golem-Version": manifest["version"]},
    )


@router.get("/status")
async def update_status(request: Request):
    _peer(request)
    ctx = request.app.state.ctx
    return {
        "node_id": ctx.identity.node_id,
        "name": ctx.cfg.get().node_name,
        "version": VERSION,
        "update": ctx.updates.status(),
    }


class ControlBody(BaseModel):
    action: Literal["check", "apply", "restart"]
    confirm: bool = False


@router.post("/control")
async def update_control(body: ControlBody, request: Request):
    peer = _peer(request, "control")
    ctx = request.app.state.ctx
    if body.action in ("apply", "restart") and not body.confirm:
        raise HTTPException(status_code=400, detail="confirmation is required for this action")
    if body.action == "check":
        result = await ctx.updates.check()
    elif body.action == "apply":
        try:
            result = await ctx.updates.apply()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        try:
            result = ctx.updates.restart_agent()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    ctx.db.audit(
        f"peer:{peer.name}",
        f"remote-{body.action}",
        ctx.identity.node_id,
        None,
        request.client.host if request.client else None,
    )
    return {
        "ok": True,
        "node_id": ctx.identity.node_id,
        "action": body.action,
        "result": result,
    }
