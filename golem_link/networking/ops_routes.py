"""Peer-facing operations API: diagnostics, logs and maintenance actions.

All routes require the target node to have granted ``allow_control``. The
diagnostics payload is read-only; the actions are the same ones the local
dashboard exposes (restart, rollback), so a peer can never do more than an
operator sitting at that machine's dashboard.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import remote_ops
from .update_routes import _peer

router = APIRouter(prefix="/ops")


@router.get("/diagnostics")
async def ops_diagnostics(request: Request):
    _peer(request, "control")
    ctx = request.app.state.ctx
    return remote_ops.collect(ctx)


@router.get("/logs")
async def ops_logs(request: Request, lines: int = 200):
    _peer(request, "control")
    ctx = request.app.state.ctx
    return {"lines": remote_ops.tail_log(ctx, lines)}


class ActionBody(BaseModel):
    action: Literal["restart", "rollback"]
    confirm: bool = False


@router.post("/action")
async def ops_action(body: ActionBody, request: Request):
    peer = _peer(request, "control")
    ctx = request.app.state.ctx
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirmation is required for this action")
    try:
        if body.action == "restart":
            result = remote_ops.restart(ctx)
        else:
            result = remote_ops.rollback(ctx, True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ctx.db.audit(
        f"peer:{peer.name}",
        f"remote-{body.action}",
        ctx.identity.node_id,
        None,
        request.client.host if request.client else None,
    )
    return {"ok": True, "node_id": ctx.identity.node_id, "action": body.action, "result": result}
