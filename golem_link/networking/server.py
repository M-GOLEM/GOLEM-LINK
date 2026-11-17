from __future__ import annotations

import logging
import time
from collections import Counter

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .. import crypto
from ..utils import is_lan_address

log = logging.getLogger(__name__)

router = APIRouter(prefix="/peer")

_pair_attempts: Counter[str] = Counter()
_pair_window_start = time.time()


class PairRequestBody(BaseModel):
    node_id: str = Field(min_length=3, max_length=80)
    name: str = Field(default="", max_length=200)
    os: str = Field(default="", max_length=120)
    os_version: str = Field(default="", max_length=200)
    hostname: str = Field(default="", max_length=200)
    cpu_model: str = Field(default="", max_length=300)
    public_key: str = Field(min_length=30)
    certificate: str = Field(min_length=100)
    peer_port: int = Field(ge=1, le=65535)
    eph_public: str = Field(min_length=20)
    req_nonce: str = Field(min_length=8)
    signature: str = Field(min_length=20)


class TaskSubmitBody(BaseModel):
    task_id: str = Field(min_length=4, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    plugin: str = Field(min_length=1, max_length=80)
    spec: dict = Field(default_factory=dict)
    priority: int = Field(default=5, ge=0, le=100)
    timeout_s: int = Field(default=3600, ge=1, le=86400 * 7)
    max_retries: int = Field(default=2, ge=0, le=10)
    source_node: str
    callback_node: str


class TaskUpdateBody(BaseModel):
    task_id: str
    status: str
    progress: float = 0.0
    result: dict | None = None
    error: str | None = None
    executor_node: str


class ClipboardBody(BaseModel):
    text: str = Field(min_length=1, max_length=1024 * 1024)
    from_name: str = Field(default="", max_length=200)


def _peer_auth(request: Request):
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
    peer.last_seen = time.time()
    return peer


def _ensure_lan(request: Request) -> None:
    host = request.client.host if request.client else ""
    if not is_lan_address(host):
        raise HTTPException(status_code=403, detail="peer API is LAN-only")


@router.get("/info")
async def peer_info(request: Request):
    _ensure_lan(request)
    return request.app.state.ctx.identity.info()


@router.get("/health")
async def peer_health(request: Request):
    _peer_auth(request)
    ctx = request.app.state.ctx
    return {
        "status": "ok",
        "node_id": ctx.identity.node_id,
        "name": ctx.cfg.get().node_name,
        "time": time.time(),
    }


@router.post("/pair/request")
async def pair_request(body: PairRequestBody, request: Request):
    _ensure_lan(request)
    global _pair_window_start
    now = time.time()
    if now - _pair_window_start > 60:
        _pair_attempts.clear()
        _pair_window_start = now
    ip = request.client.host if request.client else "?"
    _pair_attempts[ip] += 1
    if _pair_attempts[ip] > 10:
        raise HTTPException(status_code=429, detail="too many pairing attempts")
    ctx = request.app.state.ctx
    try:
        return ctx.peers.create_inbound_pair_request(body.model_dump(), ip)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/pair/status/{request_id}")
async def pair_status(request_id: str, request: Request):
    _ensure_lan(request)
    ctx = request.app.state.ctx
    req = ctx.db.get_pair_request(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="unknown pairing request")
    if req["expires_at"] < time.time() and req["status"] == "pending":
        ctx.db.set_pair_status(request_id, "expired")
        req["status"] = "expired"
    payload = {
        "status": req["status"],
        "node_id": ctx.identity.node_id,
        "expires_at": req["expires_at"],
    }
    if req["status"] == "approved":
        payload["code"] = req["code"]
        payload["certificate"] = ctx.identity.cert_pem
    return payload


@router.post("/tasks")
async def submit_task(body: TaskSubmitBody, request: Request):
    peer = _peer_auth(request)
    ctx = request.app.state.ctx
    if not peer.allow_tasks:
        raise HTTPException(status_code=403, detail="this peer is not authorized to submit tasks")
    if body.source_node != peer.node_id or body.callback_node != peer.node_id:
        raise HTTPException(status_code=400, detail="source node mismatch")
    try:
        task = await ctx.scheduler.accept_remote_task(body.model_dump())
        return {"accepted": True, "task_id": task["id"], "status": task["status"]}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tasks/update")
async def task_update(body: TaskUpdateBody, request: Request):
    peer = _peer_auth(request)
    ctx = request.app.state.ctx
    payload = body.model_dump()
    payload["executor_node"] = peer.node_id
    if body.status not in ("running", "completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail="invalid status")
    ctx.scheduler.apply_remote_update(payload)
    return {"ok": True}


@router.post("/tasks/cancel")
async def cancel_task(body: dict, request: Request):
    peer = _peer_auth(request)
    ctx = request.app.state.ctx
    task_id = body.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    try:
        return ctx.scheduler.cancel_local_task(task_id, peer.node_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tasks/{task_id}")
async def task_state(task_id: str, request: Request):
    _peer_auth(request)
    ctx = request.app.state.ctx
    task = ctx.db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    return task


@router.get("/shares")
async def list_shares(request: Request):
    peer = _peer_auth(request)
    ctx = request.app.state.ctx
    if not peer.allow_storage:
        raise HTTPException(status_code=403, detail="storage access not authorized")
    return {"shares": ctx.storage.list_share_views()}


@router.post("/clipboard")
async def peer_clipboard(body: ClipboardBody, request: Request):
    peer = _peer_auth(request)
    ctx = request.app.state.ctx
    if not peer.allow_control:
        raise HTTPException(status_code=403, detail="text relay is not allowed from this peer")
    try:
        item = await ctx.clipboard.receive_async(peer.node_id, body.from_name or peer.name, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "id": item["id"],
        "copied": item["copied"],
        "copy_method": item.get("copy_method"),
        "copy_error": item.get("copy_error"),
    }


@router.get("/capabilities")
async def capabilities(request: Request):
    peer = _peer_auth(request)
    ctx = request.app.state.ctx
    return {
        "plugins": ctx.scheduler.plugin_names(),
        "shares": [s["id"] for s in ctx.storage.list_share_views()],
        "input_sharing": ctx.cfg.get().input_sharing.model_dump(),
        # What this node grants the authenticated caller.
        "accepts_tasks": peer.allow_tasks,
        "accepts_storage": peer.allow_storage,
        "serves_updates": peer.allow_updates,
        "accepts_control": peer.allow_control,
    }


@router.websocket("/ws")
async def peer_ws(websocket: WebSocket, node: str, token: str):
    ctx = websocket.app.state.ctx
    host = websocket.client.host if websocket.client else ""
    if not is_lan_address(host):
        await websocket.close(code=4403)
        return
    peer = ctx.peers.peers.get(node)
    if peer is None or not crypto.verify_token(peer.secret, token, node, ctx.identity.node_id):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    ctx.peers.register_inbound(node, websocket)
    local = ctx.bus.latest("metrics")
    if local:
        try:
            await websocket.send_json({"type": "metrics", "data": local})
        except Exception:
            pass
    try:
        while True:
            msg = await websocket.receive_json()
            kind = msg.get("type")
            if kind == "ping":
                await websocket.send_json({"type": "pong", "ts": msg.get("ts")})
            elif kind == "metrics":
                ctx.peers.apply_inbound_metrics(node, msg.get("data") or {})
            elif kind == "hello":
                # The handshake proves liveness; metrics arrive in their own
                # message and must never be replaced with our own snapshot.
                peer.last_seen = time.time()
                if peer.status != "online":
                    peer.status = "online"
                    ctx.peers._publish_peer(peer)
            elif kind == "capabilities":
                data = msg.get("data") or {}
                if peer is not None:
                    peer.remote_accepts_tasks = bool(data.get("accepts_tasks"))
                    peer.remote_accepts_storage = bool(data.get("accepts_storage"))
                    peer.remote_allows_updates = bool(data.get("serves_updates"))
                    peer.remote_allows_control = bool(data.get("accepts_control"))
                    ctx.peers._publish_peer(peer)
            elif kind == "task_update":
                payload = msg.get("data") or {}
                payload["executor_node"] = node
                ctx.scheduler.apply_remote_update(payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("inbound websocket error", exc_info=True)
    finally:
        ctx.peers.unregister_inbound(node, websocket)


try:  # storage routes are optional at import time
    from ..storage.api import router as storage_router

    router.include_router(storage_router)
except Exception:  # pragma: no cover - avoids circular import during bootstrap
    log.debug("storage peer routes not loaded yet", exc_info=True)

from .update_routes import router as update_router  # noqa: E402 - keep after the base routes
from .ops_routes import router as ops_router  # noqa: E402 - shares the update auth

router.include_router(ops_router)

router.include_router(update_router)
