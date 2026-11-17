"""Application wiring: services, FastAPI apps, uvicorn servers and dashboard WS."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import dashboard as dashboard_api
from .api.auth import COOKIE_NAME, DashboardAuth
from .clipboard import ClipboardBridge
from .config import ConfigManager
from .db import Database
from .discovery.beacon import BeaconDiscovery
from .discovery.mdns import MDNSDiscovery
from .events import EventBus
from .host_guard import HostGuard
from .identity import NodeIdentity
from .input_sharing.deskflow import InputSharingManager
from .networking.peer_manager import PeerManager
from .networking.server import router as peer_router
from .overload import OverloadEngine
from .resource_monitor import ResourceMonitor
from .storage.shares import StorageManager
from .storage.transfers import TransferManager
from .tasks.engine import Scheduler
from .update import UpdateManager
from .utils import is_lan_address
from .version import VERSION

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    cfg: ConfigManager
    db: Database
    identity: NodeIdentity
    bus: EventBus
    host_guard: HostGuard
    monitor: ResourceMonitor
    overload: OverloadEngine
    scheduler: Scheduler
    storage: StorageManager
    transfers: TransferManager
    peers: PeerManager
    input_sharing: InputSharingManager
    clipboard: ClipboardBridge
    auth: DashboardAuth
    updates: UpdateManager
    started_at: float = field(default_factory=time.time)


def build_context(data_dir: str | Path | None = None) -> AppContext:
    cfg = ConfigManager(data_dir)
    cfg.ensure_dirs()
    identity = NodeIdentity(cfg)
    db = Database(cfg.db_path())
    bus = EventBus()
    host_guard = HostGuard(cfg, bus)
    storage = StorageManager(cfg, db, bus)
    monitor = ResourceMonitor(identity, cfg, bus)
    overload = OverloadEngine(cfg, db, bus, identity.node_id)
    scheduler = Scheduler(identity, cfg, db, bus, overload)
    transfers = TransferManager(cfg, db, bus, storage)
    peers = PeerManager(identity, cfg, db, bus, overload)
    auth = DashboardAuth(cfg, db)
    input_sharing = InputSharingManager(cfg)
    clipboard = ClipboardBridge(cfg, db, bus)
    updates = UpdateManager(cfg, bus, identity)
    return AppContext(
        cfg=cfg,
        db=db,
        identity=identity,
        bus=bus,
        host_guard=host_guard,
        monitor=monitor,
        overload=overload,
        scheduler=scheduler,
        storage=storage,
        transfers=transfers,
        peers=peers,
        input_sharing=input_sharing,
        clipboard=clipboard,
        auth=auth,
        updates=updates,
    )


class GolemService:
    """Owns all background subsystems (everything except the HTTP servers)."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._tasks: list[asyncio.Task] = []
        self._mdns: MDNSDiscovery | None = None
        self._beacon: BeaconDiscovery | None = None
        self._started = False

    async def start(self) -> None:
        ctx = self.ctx
        ctx.bus.attach_loop(asyncio.get_running_loop())
        ctx.scheduler.peers = ctx.peers
        ctx.scheduler.storage = ctx.storage
        ctx.transfers.peers = ctx.peers
        ctx.peers.on_remote_task_update = ctx.scheduler.apply_remote_update
        ctx.peers.on_peer_connected = ctx.scheduler.reconcile_peer
        ctx.updates.peers = ctx.peers
        self._tasks.append(asyncio.create_task(ctx.monitor.run(), name="monitor"))
        self._tasks.append(asyncio.create_task(ctx.updates.run(), name="updates"))
        self._tasks.append(asyncio.create_task(self._overload_loop(), name="overload"))
        await ctx.scheduler.start()
        await ctx.peers.start()
        cfg = ctx.cfg.get()
        if cfg.discovery.mdns_enabled:
            try:
                self._mdns = MDNSDiscovery(
                    ctx.identity,
                    cfg.peer.port,
                    cfg.dashboard.port,
                    ctx.peers.handle_discovered,
                    ctx.peers.handle_lost,
                )
                await self._mdns.start()
            except Exception:
                log.exception("mDNS discovery failed to start; the UDP beacon still works")
        if cfg.discovery.beacon_enabled:
            try:
                self._beacon = BeaconDiscovery(
                    ctx.identity,
                    cfg.peer.port,
                    cfg.dashboard.port,
                    cfg.discovery.beacon_port,
                    cfg.discovery.beacon_interval_s,
                    ctx.peers.handle_discovered,
                )
                await self._beacon.start()
            except Exception:
                log.exception("UDP beacon failed to start")
        ctx.db.prune_alerts(cfg.alert_history_limit)
        ctx.db.prune_audit(cfg.audit_history_limit)
        self._started = True
        log.info("GOLEM LINK services started (node %s)", ctx.identity.node_id)

    async def stop(self) -> None:
        ctx = self.ctx
        if self._mdns:
            await self._mdns.stop()
        if self._beacon:
            await self._beacon.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await ctx.scheduler.stop()
        await ctx.peers.stop()
        await ctx.transfers.close()
        try:
            ctx.db.close()
        except Exception:
            pass
        self._started = False
        log.info("GOLEM LINK services stopped")

    async def _overload_loop(self) -> None:
        ctx = self.ctx
        queue = await ctx.bus.subscribe()
        try:
            while True:
                event = await queue.get()
                if event["topic"] != "metrics":
                    continue
                rows = ctx.db.query(
                    "SELECT COUNT(*) AS n FROM tasks WHERE status = 'running' AND executor_node = ?",
                    (ctx.identity.node_id,),
                )
                active = int(rows[0]["n"]) if rows else 0
                ctx.overload.evaluate(event["data"], active)
                if ctx.host_guard.pressure_reason(event["data"]):
                    try:
                        await asyncio.to_thread(ctx.host_guard.maybe_relieve, event["data"])
                    except Exception:
                        log.exception("host relief failed")
        except asyncio.CancelledError:
            pass
        finally:
            ctx.bus.unsubscribe(queue)


# -- HTTP apps --------------------------------------------------------------
def _web_dir() -> Path:
    env = __import__("os").environ.get("GOLEM_WEB_DIR")
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).resolve().parent / "web")
    candidates.append(Path(__file__).resolve().parents[1] / "apps" / "dashboard" / "dist")
    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return candidates[-1]


def _lan_guard(app: FastAPI) -> None:
    @app.middleware("http")
    async def _middleware(request, call_next):
        host = request.client.host if request.client else ""
        if host and not is_lan_address(host):
            return JSONResponse({"detail": "GOLEM LINK is LAN-only"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def create_dashboard_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="GOLEM LINK Dashboard", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    _lan_guard(app)

    @app.middleware("http")
    async def _csp(request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/api"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'"
            )
        return response

    app.include_router(dashboard_api.router)

    @app.websocket("/ws")
    async def dashboard_ws(websocket: WebSocket):
        session_data = ctx.auth.verify_session(websocket.cookies.get(COOKIE_NAME))
        # Accept first so the browser receives the 4401 close code instead of
        # an opaque HTTP 403; the dashboard then returns to the login screen
        # instead of reconnecting every few seconds forever.
        await websocket.accept()
        if session_data is None:
            await websocket.close(code=4401)
            return
        queue = await ctx.bus.subscribe()
        try:
            initial = {
                "type": "snapshot",
                "data": {
                    "metrics": ctx.bus.latest("metrics"),
                    "cluster": ctx.peers.cluster_payload(),
                    "alerts": ctx.db.list_alerts(limit=50, only_open=True),
                    "transfers": ctx.transfers.list_transfers(20),
                    "tasks": ctx.scheduler.list_tasks(50),
                    "pair_requests": ctx.db.list_pair_requests(),
                    "shares": ctx.storage.list_share_views(),
                    "update": ctx.updates.status(),
                    "host_guard": ctx.host_guard.status(),
                    "clipboard": ctx.clipboard.recent(10),
                },
            }
            await websocket.send_json(initial)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    await websocket.send_json({"topic": "ping", "data": time.time()})
                    continue
                await websocket.send_json({"topic": event["topic"], "data": event["data"]})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            ctx.bus.unsubscribe(queue)

    web_dir = _web_dir()
    if (web_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="dashboard")
    else:
        @app.get("/")
        async def no_dashboard():
            return JSONResponse(
                {
                    "message": "GOLEM LINK agent is running, but the dashboard bundle was not found.",
                    "api": "/api/setup/status",
                    "hint": "Build the dashboard: cd apps/dashboard && npm install && npm run build",
                }
            )

    return app


def create_peer_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="GOLEM LINK Peer API", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    _lan_guard(app)
    app.include_router(peer_router)
    return app


class GolemNode:
    """Full node: context + background services + two HTTP listeners."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.ctx = build_context(data_dir)
        self.service = GolemService(self.ctx)
        self.dashboard_app = create_dashboard_app(self.ctx)
        self.peer_app = create_peer_app(self.ctx)
        self._servers: list[uvicorn.Server] = []

    async def start(self) -> None:
        await self.service.start()

    async def stop(self) -> None:
        await self.service.stop()

    async def serve(self) -> None:
        cfg = self.ctx.cfg.get()
        dash_config = uvicorn.Config(
            self.dashboard_app,
            host=cfg.dashboard.host,
            port=cfg.dashboard.port,
            log_level=cfg.log_level.lower(),
            access_log=False,
            lifespan="off",
        )
        peer_config = uvicorn.Config(
            self.peer_app,
            host=cfg.peer.host,
            port=cfg.peer.port,
            ssl_certfile=str(self.ctx.cfg.cert_path()),
            ssl_keyfile=str(self.ctx.cfg.key_path()),
            log_level=cfg.log_level.lower(),
            access_log=False,
            lifespan="off",
        )
        self._servers = [uvicorn.Server(dash_config), uvicorn.Server(peer_config)]
        await self.start()
        try:
            await asyncio.gather(*(server.serve() for server in self._servers))
        finally:
            await self.stop()
