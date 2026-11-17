"""Dashboard REST API (localhost/LAN UI, cookie sessions, CSRF-protected writes)."""

from __future__ import annotations

import json
import logging
import os
import platform
import secrets
import socket
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError

from .. import crypto, remote_ops
from ..config import AppConfig, InputSharingConfig
from ..networking.client import PeerAuthError, PeerConnectionError
from ..storage import smb
from ..tasks.plugins import PLUGINS
from ..utils import is_lan_address
from ..version import VERSION
from .auth import COOKIE_NAME, AuthError, RateLimited

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# -- dependencies -----------------------------------------------------------
def ctx(request: Request):
    return request.app.state.ctx


def session(request: Request) -> dict:
    data = ctx(request).auth.verify_session(request.cookies.get(COOKIE_NAME))
    if data is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return data


def _validation_detail(exc: ValidationError) -> list[dict]:
    # ``ctx`` carries the original ValueError instance from validators, which
    # JSON cannot encode; keep only loc/msg/type so the response serializes.
    return exc.errors(include_url=False, include_context=False, include_input=False)


def admin(request: Request) -> dict:
    data = session(request)
    if data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return data


def csrf(request: Request, data: dict) -> None:
    header = request.headers.get("X-CSRF-Token", "")
    if not header or not secrets.compare_digest(header, data.get("csrf", "")):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


def write_guard(request: Request) -> dict:
    data = admin(request)
    csrf(request, data)
    return data


def _cookie(response: Response, session_data: dict) -> None:
    response.set_cookie(
        COOKIE_NAME,
        session_data["token"],
        max_age=int(session_data["exp"] - time.time()),
        httponly=True,
        samesite="strict",
        path="/",
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# -- auth -------------------------------------------------------------------
class SetupBody(BaseModel):
    node_name: str = Field(default="", max_length=64)
    password: str = Field(min_length=8, max_length=200)


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class PasswordBody(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=200)


class ViewerPasswordBody(BaseModel):
    password: str | None = None


@router.get("/setup/status")
async def setup_status(request: Request):
    c = ctx(request)
    return {
        "needs_setup": c.auth.needs_setup(),
        "node_name": c.cfg.get().node_name,
        "node_id": c.identity.node_id,
        "version": VERSION,
    }


@router.post("/setup")
async def setup(body: SetupBody, request: Request, response: Response):
    c = ctx(request)
    if not is_lan_address(_client_ip(request)):
        raise HTTPException(status_code=403, detail="dashboard is LAN-only")
    try:
        data = c.auth.setup(body.node_name, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _cookie(response, data)
    return {"ok": True, "role": data["role"], "csrf": data["csrf"], "node_name": c.cfg.get().node_name}


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response):
    c = ctx(request)
    ip = _client_ip(request)
    if not is_lan_address(ip):
        raise HTTPException(status_code=403, detail="dashboard is LAN-only")
    try:
        role, data = c.auth.login(body.password, ip)
    except RateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _cookie(response, data)
    return {"ok": True, "role": role, "csrf": data["csrf"]}


@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/me")
async def me(request: Request):
    data = session(request)
    c = ctx(request)
    return {
        "role": data.get("role"),
        "csrf": data.get("csrf"),
        "node_id": c.identity.node_id,
        "node_name": c.cfg.get().node_name,
        "fingerprint": c.identity.fingerprint,
        "version": VERSION,
        "setup_complete": c.cfg.get().first_run_complete,
    }


@router.post("/auth/password")
async def change_password(body: PasswordBody, request: Request):
    data = write_guard(request)
    if data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    try:
        ctx(request).auth.change_admin_password(body.old_password, body.new_password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/auth/viewer-password")
async def viewer_password(body: ViewerPasswordBody, request: Request):
    write_guard(request)
    try:
        ctx(request).auth.set_viewer_password(body.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "viewer_enabled": bool(body.password)}


# -- overview / cluster -----------------------------------------------------
@router.get("/overview")
async def overview(request: Request):
    session(request)
    c = ctx(request)
    cluster = c.peers.cluster_payload()
    local_metrics = c.bus.latest("metrics") or c.monitor.last() or {}
    online_peers = [p for p in cluster["peers"] if p["status"] == "online"]
    cores = int(local_metrics.get("cpu", {}).get("cores_logical", 0))
    ram_total = int(local_metrics.get("ram", {}).get("total", 0))
    ram_used = int(local_metrics.get("ram", {}).get("used", 0))
    for peer in online_peers:
        m = peer.get("metrics") or {}
        cores += int(m.get("cpu", {}).get("cores_logical", 0))
        ram_total += int(m.get("ram", {}).get("total", 0))
        ram_used += int(m.get("ram", {}).get("used", 0))
    shares = [s for s in c.storage.list_share_views() if s["exists"]]
    storage_total = sum(s["usage"]["total"] for s in shares if s.get("usage"))
    storage_free = sum(s["usage"]["free"] for s in shares if s.get("usage"))
    counts = {row["status"]: row["n"] for row in c.db.query(
        "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
    latencies = [p["latency_ms"] for p in online_peers if p.get("latency_ms") is not None]
    return {
        "nodes_total": 1 + len(cluster["peers"]),
        "nodes_online": 1 + len(online_peers),
        "cores_total": cores,
        "cores_note": "aggregate of logical cores across online nodes, not merged",
        "ram_total": ram_total,
        "ram_used": ram_used,
        "ram_note": "aggregate physical RAM across online nodes, not shared memory",
        "storage_total": storage_total,
        "storage_free": storage_free,
        "storage_note": "approved shares on this node",
        "network": {
            "online": 1 + len(online_peers),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "interfaces": local_metrics.get("net", {}).get("interfaces", []),
        },
        "tasks": {
            "running": counts.get("running", 0) + counts.get("dispatching", 0),
            "queued": counts.get("queued", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0),
            "total": sum(counts.values()),
        },
        "alerts_open": len(c.db.list_alerts(limit=500, only_open=True)),
        "input_sharing_enabled": c.cfg.get().input_sharing.enabled,
        "node_id": c.identity.node_id,
        "uptime_s": local_metrics.get("uptime_s"),
    }


@router.get("/cluster")
async def cluster(request: Request):
    session(request)
    c = ctx(request)
    payload = c.peers.cluster_payload()
    payload["identity"] = c.identity.info()
    payload["shares"] = c.storage.list_share_views()
    return payload


@router.get("/devices")
async def devices(request: Request):
    session(request)
    c = ctx(request)
    local_metrics = c.bus.latest("metrics") or c.monitor.last() or {}
    info = c.identity.info()
    local = {
        **info,
        "status": "online",
        "self": True,
        "metrics": local_metrics,
        "pressure": c.overload.pressure.as_dict(),
        "shares": len(c.cfg.get().shares),
        "input_role": "server" if c.cfg.get().input_sharing.server_node_id == c.identity.node_id else "client",
    }
    peers = []
    for row in c.db.list_peers():
        runtime = c.peers.peers.get(row["node_id"])
        info = (runtime.info if runtime else None) or {}
        peers.append(
            {
                "node_id": row["node_id"],
                "name": runtime.name if runtime else row["name"],
                # A live handshake outranks the row written at pairing time.
                "os": info.get("os") or row["os"],
                "os_version": info.get("os_version") or row["os_version"] or "",
                "hostname": info.get("hostname") or row["hostname"] or "",
                "cpu_model": info.get("cpu_model") or row["cpu_model"] or "",
                "cores_physical": info.get("cpu_cores_physical"),
                "cores_logical": info.get("cpu_cores_logical"),
                "version": info.get("version", ""),
                "address": (runtime.address if runtime and runtime.address else row["address"]),
                "peer_port": (runtime.port if runtime and runtime.port else row["peer_port"]),
                "status": runtime.status if runtime else "offline",
                "last_seen": (runtime.last_seen if runtime else None),
                "latency_ms": runtime.latency_ms if runtime else None,
                "failures": runtime.failures if runtime else 0,
                "reconnects": runtime.reconnects if runtime else 0,
                "last_error": runtime.last_error if runtime else None,
                "fingerprint": row["fingerprint"],
                "metrics": runtime.metrics if runtime else None,
                "pressure": c.peers.pressure_for(row["node_id"]),
                "allow_tasks": bool(row["allow_tasks"]),
                "allow_storage": bool(row["allow_storage"]),
                "allow_updates": bool(row.get("allow_updates", 1)),
                "allow_control": bool(row.get("allow_control", 0)),
                "auto_connect": bool(row["auto_connect"]),
                "remote_accepts_tasks": bool(runtime.remote_accepts_tasks) if runtime else False,
                "remote_accepts_storage": bool(runtime.remote_accepts_storage) if runtime else False,
                "remote_allows_updates": bool(runtime.remote_allows_updates) if runtime else False,
                "remote_allows_control": bool(runtime.remote_allows_control) if runtime else False,
                "info": runtime.info if runtime else None,
            }
        )
    return {"local": local, "peers": peers, "discovered": c.peers.discovered_payload()}


@router.get("/network")
async def network(request: Request):
    session(request)
    c = ctx(request)
    payload = c.peers.cluster_payload()
    payload["interfaces"] = (c.bus.latest("metrics") or {}).get("net", {}).get("interfaces", [])
    return payload


# -- metrics ----------------------------------------------------------------
@router.get("/metrics/latest")
async def metrics_latest(request: Request):
    session(request)
    c = ctx(request)
    return {**c.identity.info(), "metrics": c.bus.latest("metrics") or c.monitor.last()}


@router.get("/metrics/history")
async def metrics_history(request: Request, limit: int = 300):
    session(request)
    limit = max(1, min(limit, 300))
    return {"samples": ctx(request).monitor.history_payload(limit)}


# -- tasks ------------------------------------------------------------------
class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    plugin: str = Field(min_length=1, max_length=80)
    spec: dict = Field(default_factory=dict)
    priority: int = Field(default=5, ge=0, le=100)
    timeout_s: int | None = Field(default=None, ge=1, le=86400 * 7)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    target_node: str | None = None


@router.get("/tasks")
async def list_tasks(request: Request, limit: int = 200, status: str | None = None):
    session(request)
    if status and status not in ("queued", "dispatching", "running", "completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail="invalid status filter")
    return {"tasks": ctx(request).scheduler.list_tasks(limit=max(1, min(limit, 500)), status=status)}


@router.post("/tasks")
async def create_task(body: TaskCreate, request: Request):
    admin_data = write_guard(request)
    c = ctx(request)
    try:
        task = await c.scheduler.submit_local(
            name=body.name,
            plugin=body.plugin,
            spec=body.spec,
            priority=body.priority,
            timeout_s=body.timeout_s,
            max_retries=body.max_retries,
            target_node=body.target_node,
            authorized_by=f"dashboard:{admin_data.get('role')}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return task


@router.get("/tasks/{task_id}")
async def task_detail(task_id: str, request: Request):
    session(request)
    task = ctx(request).scheduler.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    return task


@router.post("/tasks/{task_id}/cancel")
async def task_cancel(task_id: str, request: Request):
    write_guard(request)
    try:
        return await ctx(request).scheduler.cancel(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/retry")
async def task_retry(task_id: str, request: Request):
    write_guard(request)
    try:
        return await ctx(request).scheduler.retry(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/pause")
async def task_pause(task_id: str, request: Request):
    write_guard(request)
    raise HTTPException(status_code=409, detail="pause/resume is not supported by any enabled plugin")


@router.get("/plugins")
async def plugins(request: Request):
    session(request)
    return {"plugins": ctx(request).scheduler.catalog()}


# -- storage ----------------------------------------------------------------
class ShareCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    path: str = Field(min_length=2)
    read_only: bool = False
    allow_delete: bool = False
    confirm_root: bool = False


class SharePatch(BaseModel):
    name: str | None = None
    read_only: bool | None = None
    allow_delete: bool | None = None


class DownloadBody(BaseModel):
    node_id: str
    share_id: str
    path: str = Field(min_length=1)


class UploadBody(BaseModel):
    node_id: str
    share_id: str
    path: str = Field(min_length=1)
    local_path: str = Field(min_length=1)
    overwrite: bool = False


@router.get("/storage/volumes")
async def storage_volumes(request: Request):
    session(request)
    return {"volumes": ctx(request).storage.list_volumes()}


@router.get("/storage/shares")
async def storage_shares(request: Request):
    session(request)
    return {"shares": ctx(request).storage.list_share_views()}


@router.post("/storage/shares")
async def storage_add_share(body: ShareCreate, request: Request):
    write_guard(request)
    try:
        share = ctx(request).storage.add_share(
            name=body.name,
            path=body.path,
            read_only=body.read_only,
            allow_delete=body.allow_delete,
            confirm_root=body.confirm_root,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return share


@router.patch("/storage/shares/{share_id}")
async def storage_patch_share(share_id: str, body: SharePatch, request: Request):
    write_guard(request)
    try:
        return ctx(request).storage.update_share(
            share_id,
            name=body.name,
            read_only=body.read_only,
            allow_delete=body.allow_delete,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/storage/shares/{share_id}")
async def storage_remove_share(share_id: str, request: Request):
    write_guard(request)
    try:
        ctx(request).storage.remove_share(share_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/storage/browse")
async def storage_browse(request: Request, share_id: str, path: str = ""):
    session(request)
    try:
        return {"entries": ctx(request).storage.list_dir(share_id, path)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/storage/remote/{node_id}/shares")
async def remote_shares(node_id: str, request: Request):
    session(request)
    c = ctx(request)
    peer = c.peers.peers.get(node_id)
    if peer is None or peer.status != "online":
        raise HTTPException(status_code=400, detail="peer is offline")
    try:
        return await c.peers.client.get_json(peer.target(), "/peer/shares", timeout=10)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"remote shares unavailable: {exc}") from exc


@router.get("/storage/remote/{node_id}/list")
async def remote_list(node_id: str, request: Request, share_id: str, path: str = ""):
    session(request)
    c = ctx(request)
    peer = c.peers.peers.get(node_id)
    if peer is None or peer.status != "online":
        raise HTTPException(status_code=400, detail="peer is offline")
    try:
        return await c.peers.client.get_json(
            peer.target(),
            f"/peer/storage/{share_id}/list?path={_q(path)}",
            timeout=15,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"remote browse failed: {exc}") from exc


def _q(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


@router.post("/storage/download")
async def storage_download(body: DownloadBody, request: Request):
    write_guard(request)
    try:
        return await ctx(request).transfers.start_download(body.node_id, body.share_id, body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/storage/upload")
async def storage_upload(body: UploadBody, request: Request):
    write_guard(request)
    try:
        return await ctx(request).transfers.start_upload(
            body.node_id, body.share_id, body.path, body.local_path, body.overwrite
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/storage/transfers")
async def transfers(request: Request, limit: int = 100):
    session(request)
    return {"transfers": ctx(request).transfers.list_transfers(limit=max(1, min(limit, 500)))}


@router.post("/storage/transfers/{transfer_id}/cancel")
async def transfer_cancel(transfer_id: str, request: Request):
    write_guard(request)
    try:
        return ctx(request).transfers.cancel(transfer_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class SmbBody(BaseModel):
    remote_host: str = ""


@router.post("/storage/smb/scripts")
async def smb_scripts(body: SmbBody, request: Request):
    session(request)
    c = ctx(request)
    shares = c.cfg.get().shares
    if not shares:
        raise HTTPException(status_code=400, detail="no approved shares yet")
    return {
        "windows": smb.windows_share_script(shares),
        "debian": smb.debian_samba_setup(shares),
        "windows_mount": smb.windows_mount_script(body.remote_host, shares) if body.remote_host else None,
        "debian_mount": smb.debian_mount_script(body.remote_host, shares) if body.remote_host else None,
        "warning": "These are optional OS-level helpers. Review before running. GOLEM LINK transfers work without SMB.",
    }


# -- pairing / peers --------------------------------------------------------
class PairBegin(BaseModel):
    node_id: str | None = None
    address: str | None = None


class ManualPeer(BaseModel):
    address: str = Field(min_length=7, max_length=64)


@router.get("/pairing/discovered")
async def pairing_discovered(request: Request):
    session(request)
    return {"discovered": ctx(request).peers.discovered_payload()}


@router.post("/pairing/begin")
async def pairing_begin(body: PairBegin, request: Request):
    write_guard(request)
    try:
        return await ctx(request).peers.begin_pairing(body.node_id, body.address)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"pairing failed: {exc}") from exc


@router.get("/pairing/poll/{request_id}")
async def pairing_poll(request_id: str, request: Request):
    session(request)
    try:
        return await ctx(request).peers.poll_pairing(request_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"pairing status failed: {exc}") from exc


@router.post("/pairing/cancel/{request_id}")
async def pairing_cancel(request_id: str, request: Request):
    write_guard(request)
    ctx(request).peers.cancel_outgoing(request_id)
    return {"ok": True}


@router.get("/pairing/requests")
async def pairing_requests(request: Request):
    session(request)
    rows = ctx(request).db.list_pair_requests()
    return {
        "requests": [
            {
                "id": r["id"],
                "node_id": r["node_id"],
                "name": r["name"],
                "os": r["os"],
                "fingerprint": r["fingerprint"],
                "address": r["address"],
                "code": r["code"],
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
            }
            for r in rows
        ]
    }


class ApproveBody(BaseModel):
    approve: bool


@router.post("/pairing/requests/{request_id}")
async def pairing_approve(request_id: str, body: ApproveBody, request: Request):
    write_guard(request)
    try:
        return ctx(request).peers.approve_pair(request_id, body.approve)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pairing/manual-peer")
async def manual_peer(body: ManualPeer, request: Request):
    write_guard(request)
    c = ctx(request)
    address = body.address.strip()
    address_only, _, port_s = address.partition(":")
    if not is_lan_address(address_only):
        raise HTTPException(status_code=400, detail="manual peers must be on the local network")
    entry = address
    peers_cfg = c.cfg.get().discovery.manual_peers
    if entry not in peers_cfg:
        c.cfg.update(lambda cfg: cfg.discovery.manual_peers.append(entry))
        c.db.audit("user", "manual-peer", address, None, _client_ip(request))
    return {"ok": True, "manual_peers": c.cfg.get().discovery.manual_peers}


@router.patch("/peers/{node_id}/permissions")
async def peer_permissions(node_id: str, request: Request):
    write_guard(request)
    body = await request.json()
    allowed = {
        k: bool(v)
        for k, v in body.items()
        if k in ("allow_tasks", "allow_storage", "allow_updates", "allow_control", "auto_connect")
    }
    try:
        ctx(request).peers.set_peer_permission(node_id, **allowed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/peers/{node_id}")
async def peer_remove(node_id: str, request: Request):
    write_guard(request)
    ctx(request).peers.remove_peer(node_id)
    return {"ok": True}


# -- updates / remote maintenance -------------------------------------------
def _peer_or_400(c, node_id: str):
    peer = c.peers.peers.get(node_id)
    if peer is None:
        raise HTTPException(status_code=404, detail="unknown device")
    if peer.status != "online":
        raise HTTPException(status_code=409, detail="that device is offline")
    return peer


async def _control_node(c, node_id: str, action: str, confirm: bool) -> dict:
    if action not in ("check", "apply", "restart", "rollback", "status"):
        raise HTTPException(status_code=400, detail="unsupported action")
    if action in ("apply", "restart", "rollback") and not confirm:
        raise HTTPException(status_code=400, detail="confirmation is required for this action")
    if node_id == c.identity.node_id:
        try:
            if action == "check":
                update = await c.updates.check()
            elif action == "apply":
                update = await c.updates.apply()
            elif action == "rollback":
                return {
                    "ok": True,
                    "node_id": node_id,
                    "action": action,
                    "result": remote_ops.rollback(c, confirm),
                }
            elif action == "restart":
                return {
                    "ok": True,
                    "node_id": node_id,
                    "action": action,
                    "result": remote_ops.restart(c),
                }
            else:
                update = c.updates.status()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "node_id": node_id, "version": VERSION, "update": update}
    peer = _peer_or_400(c, node_id)
    if action == "rollback":
        try:
            return await c.peers.client.post_json(
                peer.target(),
                "/peer/ops/action",
                {"action": "rollback", "confirm": confirm},
                timeout=30,
            )
        except PeerAuthError as exc:
            raise HTTPException(status_code=403, detail="that device rejected this node") from exc
        except PeerConnectionError as exc:
            raise _control_error(peer, exc) from exc
    try:
        return await c.peers.client.post_json(
            peer.target(),
            "/peer/update/control",
            {"action": action, "confirm": confirm},
            timeout=120,
        )
    except PeerAuthError as exc:
        raise HTTPException(status_code=403, detail="that device rejected this node") from exc
    except PeerConnectionError as exc:
        raise _control_error(peer, exc) from exc


def _control_error(peer, exc: PeerConnectionError) -> HTTPException:
    detail = str(exc)
    if "403" in detail:
        return HTTPException(
            status_code=403,
            detail=(
                f"{peer.name} has not granted remote control. Open Updates on that "
                "device (or Devices > permissions) and allow this node first."
            ),
        )
    return HTTPException(status_code=502, detail=f"{peer.name}: {detail}")


async def _fleet_rows(c) -> list[dict]:
    rows: list[dict] = [
        {
            "node_id": c.identity.node_id,
            "name": c.cfg.get().node_name,
            "self": True,
            "status": "online",
            "version": VERSION,
            "update": c.updates.status(),
            "error": None,
            "can_control": True,
        }
    ]
    online = [p for p in c.peers.peers.values() if p.status == "online"]
    offline = [p for p in c.peers.peers.values() if p.status != "online"]
    for peer in online:
        row = {
            "node_id": peer.node_id,
            "name": peer.name,
            "self": False,
            "status": peer.status,
            "version": (peer.info or {}).get("version", ""),
            "update": None,
            "error": None,
            "can_control": bool(peer.remote_allows_control),
        }
        try:
            payload = await c.peers.client.get_json(
                peer.target(), "/peer/update/status", timeout=8
            )
            row["update"] = payload.get("update")
            row["version"] = payload.get("version") or row["version"]
        except PeerConnectionError as exc:
            row["error"] = (
                "this device runs an older agent; update it once with the installer"
                if "404" in str(exc)
                else str(exc)
            )
        except Exception as exc:  # pragma: no cover - defensive at the boundary
            row["error"] = str(exc)
        rows.append(row)
    for peer in offline:
        rows.append(
            {
                "node_id": peer.node_id,
                "name": peer.name,
                "self": False,
                "status": peer.status,
                "version": (peer.info or {}).get("version", ""),
                "update": None,
                "error": "offline",
                "can_control": bool(peer.remote_allows_control),
            }
        )
    return rows


class UpdateApplyBody(BaseModel):
    confirm: bool = False


class ControlBody(BaseModel):
    action: str
    confirm: bool = False


class FleetBody(BaseModel):
    action: str = "check"
    confirm: bool = False


@router.get("/update")
async def update_status(request: Request):
    session(request)
    return ctx(request).updates.status()


@router.post("/update/check")
async def update_check(request: Request):
    write_guard(request)
    c = ctx(request)
    result = await c.updates.check()
    c.db.audit("user", "update-check", "local", None, _client_ip(request))
    return result


@router.post("/update/download")
async def update_download(request: Request):
    write_guard(request)
    c = ctx(request)
    try:
        result = await c.updates.download()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    c.db.audit("user", "update-download", "local", None, _client_ip(request))
    return result


@router.post("/update/apply")
async def update_apply(body: UpdateApplyBody, request: Request):
    write_guard(request)
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirmation is required to install an update")
    c = ctx(request)
    try:
        result = await c.updates.apply()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    c.db.audit("user", "update-apply", "local", None, _client_ip(request))
    return result


@router.get("/update/fleet")
async def update_fleet(request: Request):
    session(request)
    c = ctx(request)
    return {"rows": await _fleet_rows(c)}


@router.post("/update/fleet")
async def update_fleet_action(body: FleetBody, request: Request):
    write_guard(request)
    c = ctx(request)
    if body.action not in ("check", "apply"):
        raise HTTPException(status_code=400, detail="action must be 'check' or 'apply'")
    if body.action == "apply" and not body.confirm:
        raise HTTPException(status_code=400, detail="confirmation is required to update the fleet")
    confirm = bool(body.confirm)
    results: list[dict] = []
    # Peers first: this node may be the one serving the artifact, so it should
    # restart last and stay available while the others upgrade.
    peers = [p for p in c.peers.peers.values() if p.status == "online"]
    for peer in peers:
        if body.action == "apply" and not peer.remote_allows_control:
            results.append(
                {
                    "node_id": peer.node_id,
                    "name": peer.name,
                    "ok": False,
                    "error": "remote control not granted on that device",
                }
            )
            continue
        try:
            payload = await _control_node(c, peer.node_id, body.action, confirm)
            results.append({"node_id": peer.node_id, "name": peer.name, "ok": True, "result": payload})
        except HTTPException as exc:
            results.append(
                {"node_id": peer.node_id, "name": peer.name, "ok": False, "error": exc.detail}
            )
    if body.action == "check" or confirm:
        try:
            payload = await _control_node(c, c.identity.node_id, body.action, confirm)
            results.append(
                {
                    "node_id": c.identity.node_id,
                    "name": c.cfg.get().node_name,
                    "ok": True,
                    "result": payload,
                    "self": True,
                }
            )
        except HTTPException as exc:
            results.append(
                {
                    "node_id": c.identity.node_id,
                    "name": c.cfg.get().node_name,
                    "ok": False,
                    "error": exc.detail,
                    "self": True,
                }
            )
    c.db.audit("user", f"fleet-{body.action}", "fleet", json.dumps(results)[:4000], _client_ip(request))
    return {"ok": True, "action": body.action, "results": results, "rows": await _fleet_rows(c)}


@router.post("/devices/{node_id}/control")
async def device_control(node_id: str, body: ControlBody, request: Request):
    write_guard(request)
    c = ctx(request)
    result = await _control_node(c, node_id, body.action, body.confirm)
    c.db.audit(
        "user", f"device-{body.action}", node_id, None, _client_ip(request)
    )
    return result


@router.get("/devices/{node_id}/diagnostics")
async def device_diagnostics(node_id: str, request: Request):
    session(request)
    c = ctx(request)
    if node_id == c.identity.node_id:
        return remote_ops.collect(c)
    peer = _peer_or_400(c, node_id)
    try:
        return await c.peers.client.get_json(
            peer.target(), "/peer/ops/diagnostics", timeout=15
        )
    except PeerAuthError as exc:
        raise HTTPException(status_code=403, detail="that device rejected this node") from exc
    except PeerConnectionError as exc:
        raise _control_error(peer, exc) from exc


@router.get("/devices/{node_id}/logs")
async def device_logs(node_id: str, request: Request, lines: int = 200):
    session(request)
    c = ctx(request)
    if node_id == c.identity.node_id:
        return {"lines": remote_ops.tail_log(c, lines)}
    peer = _peer_or_400(c, node_id)
    try:
        return await c.peers.client.get_json(
            peer.target(), f"/peer/ops/logs?lines={max(1, min(int(lines), 1000))}", timeout=15
        )
    except PeerAuthError as exc:
        raise HTTPException(status_code=403, detail="that device rejected this node") from exc
    except PeerConnectionError as exc:
        raise _control_error(peer, exc) from exc


# -- alerts -----------------------------------------------------------------
@router.get("/alerts")
async def alerts(request: Request, open_only: bool = False, limit: int = 200):
    session(request)
    return {"alerts": ctx(request).db.list_alerts(limit=max(1, min(limit, 1000)), only_open=open_only)}


@router.post("/alerts/{alert_id}/ack")
async def alert_ack(alert_id: int, request: Request):
    write_guard(request)
    ctx(request).db.ack_alert(alert_id)
    return {"ok": True}


@router.post("/alerts/ack-all")
async def alerts_ack_all(request: Request):
    write_guard(request)
    ctx(request).db.ack_all_alerts()
    return {"ok": True}


# -- settings ---------------------------------------------------------------
MUTABLE_SECTIONS = {
    "thresholds",
    "scheduling",
    "discovery",
    "input_sharing",
    "clipboard",
    "shares",
    "update",
}
MUTABLE_TOP = {"node_name", "auto_start", "log_level"}


def _sanitized_config(cfg: AppConfig) -> dict:
    data = cfg.model_dump()
    for section in ("dashboard",):
        for key in list(data.get(section, {})):
            if "password" in key:
                data[section][key] = None
    data["dashboard"]["password_set"] = bool(cfg.dashboard.admin_password_hash)
    data["dashboard"]["viewer_enabled"] = bool(cfg.dashboard.viewer_password_hash)
    return data


@router.get("/settings")
async def get_settings(request: Request):
    session(request)
    c = ctx(request)
    return {
        "config": _sanitized_config(c.cfg.get()),
        "plugins": c.scheduler.catalog(),
        "input_sharing_status": c.input_sharing.status(),
        "data_dir": str(c.cfg.data_dir),
        "restart_required_after": ["peer.port", "dashboard.port", "discovery.mdns_enabled", "discovery.beacon_enabled"],
    }


@router.patch("/settings")
async def patch_settings(request: Request):
    data = write_guard(request)
    c = ctx(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="settings payload must be an object")

    current = c.cfg.get().model_dump()
    changed: list[str] = []
    for key, value in body.items():
        if key in MUTABLE_TOP:
            current[key] = value
            changed.append(key)
        elif key in MUTABLE_SECTIONS:
            if not isinstance(value, dict):
                raise HTTPException(status_code=400, detail=f"{key} must be an object")
            current[key].update(value)
            changed.append(key)
        elif key == "dashboard":
            allowed = {"session_ttl_s", "login_max_attempts", "login_window_s", "port"}
            for k, v in (value or {}).items():
                if k in allowed:
                    current["dashboard"][k] = v
                    changed.append(f"dashboard.{k}")
        else:
            raise HTTPException(status_code=400, detail=f"setting '{key}' cannot be changed here")
    try:
        validated = AppConfig.model_validate(current)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=_validation_detail(exc)) from exc
    for plugin_name in validated.scheduling.enabled_plugins:
        if plugin_name not in PLUGINS:
            raise HTTPException(status_code=400, detail=f"unknown plugin '{plugin_name}'")
    for entry in validated.discovery.manual_peers:
        host = entry.partition(":")[0]
        if not is_lan_address(host):
            raise HTTPException(status_code=400, detail=f"manual peer '{entry}' is not a LAN address")
    c.cfg.replace(validated)
    c.db.audit("user", "settings-update", "config", json.dumps({"changed": changed}), _client_ip(request))
    c.bus.publish("settings", _sanitized_config(validated))
    return {
        "ok": True,
        "changed": changed,
        "restart_required": bool(set(changed) & {"peer.port", "dashboard.port", "discovery.mdns_enabled", "discovery.beacon_enabled"}),
    }


# -- input sharing ----------------------------------------------------------
class LayoutBody(BaseModel):
    enabled: bool = False
    tool: str = "deskflow"
    server_node_id: str | None = None
    screens: list[dict] = Field(default_factory=list)
    clipboard_sharing: bool = True
    auto_reconnect: bool = True


@router.get("/input-sharing")
async def input_sharing(request: Request):
    session(request)
    return ctx(request).input_sharing.status()


@router.post("/input-sharing")
async def save_input_sharing(body: LayoutBody, request: Request):
    write_guard(request)
    c = ctx(request)
    try:
        layout = InputSharingConfig.model_validate(body.model_dump())
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=_validation_detail(exc)) from exc
    return c.input_sharing.save_layout(layout)


@router.post("/input-sharing/generate")
async def generate_input_sharing(request: Request):
    write_guard(request)
    try:
        return ctx(request).input_sharing.generate()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# -- clipboard text relay ---------------------------------------------------
class ClipboardSendBody(BaseModel):
    peer_id: str = Field(min_length=3, max_length=80)
    text: str = Field(min_length=1, max_length=1024 * 1024)


class ClipboardCopyBody(BaseModel):
    text: str = Field(min_length=1, max_length=1024 * 1024)


@router.get("/clipboard")
async def clipboard_inbox(request: Request, limit: int = 20):
    session(request)
    c = ctx(request)
    return {
        "items": c.clipboard.recent(max(1, min(limit, 100))),
        "status": c.clipboard.status(),
    }


@router.post("/clipboard/send")
async def clipboard_send(body: ClipboardSendBody, request: Request):
    write_guard(request)
    c = ctx(request)
    try:
        result = await c.clipboard.send(c.peers, body.peer_id, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    c.db.audit(
        "user", "clipboard-send", body.peer_id, f"{len(body.text)} chars", _client_ip(request)
    )
    return result


@router.post("/clipboard/copy")
async def clipboard_copy_local(body: ClipboardCopyBody, request: Request):
    write_guard(request)
    c = ctx(request)
    result = await c.clipboard.copy_local_async(body.text)
    c.db.audit(
        "user", "clipboard-copy", "local", f"{len(body.text)} chars", _client_ip(request)
    )
    if not result["copied"]:
        result["error"] = result.get("error") or "the desktop clipboard was not reachable"
    return result


@router.post("/clipboard/read")
async def clipboard_read_local(request: Request):
    write_guard(request)
    c = ctx(request)
    try:
        result = await c.clipboard.read_local_async()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    c.db.audit(
        "user", "clipboard-read", "local", f"{result['chars']} chars", _client_ip(request)
    )
    return result


@router.post("/clipboard/{item_id}/copy")
async def clipboard_copy_item(item_id: str, request: Request):
    write_guard(request)
    c = ctx(request)
    try:
        result = await c.clipboard.copy_item_async(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not result["copied"]:
        result["error"] = result.get("error") or "the desktop clipboard was not reachable"
    return result


# -- diagnostics ------------------------------------------------------------
@router.get("/audit")
async def audit_log(request: Request, limit: int = 200):
    session(request)
    return {"audit": ctx(request).db.list_audit(limit=max(1, min(limit, 2000)))}


@router.get("/logs")
async def logs(request: Request, lines: int = 200):
    session(request)
    return {"lines": remote_ops.tail_log(ctx(request), lines)}


@router.get("/diagnostics")
async def diagnostics(request: Request):
    session(request)
    c = ctx(request)
    db_size = Path(c.cfg.db_path()).stat().st_size if Path(c.cfg.db_path()).exists() else 0
    return {
        "version": VERSION,
        "node_id": c.identity.node_id,
        "node_name": c.cfg.get().node_name,
        "os": platform.platform(),
        "hostname": socket.gethostname(),
        "data_dir": str(c.cfg.data_dir),
        "db_size": db_size,
        "dashboard_port": c.cfg.get().dashboard.port,
        "peer_port": c.cfg.get().peer.port,
        "peers_paired": len(c.db.list_peers()),
        "peers_online": sum(1 for p in c.peers.peers.values() if p.status == "online"),
        "mdns_enabled": c.cfg.get().discovery.mdns_enabled,
        "beacon_enabled": c.cfg.get().discovery.beacon_enabled,
        "plugins": c.scheduler.catalog(),
        "python": platform.python_version(),
        "uptime_s": (c.bus.latest("metrics") or {}).get("uptime_s"),
        "fingerprint": c.identity.fingerprint,
        "host_guard": c.host_guard.status(),
        "scheduler_relief": c.scheduler.relief_status(),
        "monitor_interval_s": c.monitor.current_interval,
        "clipboard": c.clipboard.status(),
    }
