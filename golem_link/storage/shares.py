"""Share management: volume detection, approved roots and safe path resolution."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import psutil

from ..config import ConfigManager, ShareConfig
from ..db import Database
from ..events import EventBus
from ..utils import get_lan_ips, safe_resolve

log = logging.getLogger(__name__)

FORBIDDEN_SYSTEM_DIRS = {
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "/",
    "/etc",
    "/boot",
    "/proc",
    "/sys",
    "/dev",
}


class StorageManager:
    def __init__(self, cfg: ConfigManager, db: Database, bus: EventBus) -> None:
        self.cfg = cfg
        self.db = db
        self.bus = bus

    # -- volumes -----------------------------------------------------------
    def list_volumes(self) -> list[dict]:
        entries: list[dict] = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            entries.append(
                {
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent,
                    "removable": "removable" in part.opts,
                }
            )
        return entries

    def list_share_views(self) -> list[dict]:
        views = []
        for share in self.cfg.get().shares:
            path = Path(share.path)
            exists = path.exists() and path.is_dir()
            usage = None
            if exists:
                try:
                    u = shutil.disk_usage(path)
                    usage = {"total": u.total, "used": u.used, "free": u.free,
                             "percent": round(u.used / u.total * 100, 1) if u.total else 0}
                except OSError:
                    usage = None
            views.append(
                {
                    "id": share.id,
                    "name": share.name,
                    "path": share.path,
                    "read_only": share.read_only,
                    "allow_delete": share.allow_delete,
                    "approved_at": share.approved_at,
                    "exists": exists,
                    "usage": usage,
                }
            )
        return views

    # -- share CRUD --------------------------------------------------------
    def add_share(
        self,
        *,
        name: str,
        path: str,
        read_only: bool,
        allow_delete: bool,
        confirm_root: bool = False,
    ) -> dict:
        target = Path(path).expanduser()
        if not target.is_absolute():
            raise ValueError("share path must be absolute")
        try:
            target = target.resolve()
        except OSError as exc:
            raise ValueError(f"cannot resolve path: {exc}") from exc
        if not target.exists() or not target.is_dir():
            raise ValueError("share path must be an existing directory")
        normalized = str(target)
        if normalized in FORBIDDEN_SYSTEM_DIRS and not confirm_root:
            raise ValueError(
                "refusing to share a system root; confirm explicitly if this is really intended"
            )
        if os.name == "nt" and target.parent == target and not confirm_root:
            raise ValueError("refusing to share a whole drive without explicit confirmation")
        if read_only and allow_delete:
            raise ValueError("allow_delete requires a writable share")
        for existing in self.cfg.get().shares:
            if Path(existing.path).resolve() == target:
                raise ValueError("this directory is already shared")
        share = {
            "id": f"s-{uuid.uuid4().hex[:10]}",
            "name": name.strip()[:60] or target.name,
            "path": normalized,
            "read_only": bool(read_only),
            "allow_delete": bool(allow_delete),
            "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.cfg.update(lambda c: c.shares.append(ShareConfig(**share)))
        self.db.audit("user", "share-add", share["id"], f"{normalized} ro={read_only} del={allow_delete}", None)
        self.bus.publish("shares", self.list_share_views())
        return share

    def update_share(self, share_id: str, *, read_only: bool | None = None,
                     allow_delete: bool | None = None, name: str | None = None) -> dict:
        found: dict[str, Any] = {}

        def mutate(cfg):
            for share in cfg.shares:
                if share.id == share_id:
                    if name is not None:
                        share.name = name.strip()[:60] or share.name
                    if read_only is not None:
                        share.read_only = bool(read_only)
                    if allow_delete is not None:
                        share.allow_delete = bool(allow_delete)
                    if share.read_only:
                        share.allow_delete = False
                    found.update(share.model_dump())
        self.cfg.update(mutate)
        if not found:
            raise ValueError("unknown share")
        self.db.audit("user", "share-update", share_id, str(found), None)
        self.bus.publish("shares", self.list_share_views())
        return found

    def remove_share(self, share_id: str) -> None:
        before = len(self.cfg.get().shares)
        self.cfg.update(lambda c: setattr(c, "shares", [s for s in c.shares if s.id != share_id]))
        if len(self.cfg.get().shares) == before:
            raise ValueError("unknown share")
        self.db.audit("user", "share-remove", share_id, None, None)
        self.bus.publish("shares", self.list_share_views())

    def get_share(self, share_id: str) -> ShareConfig:
        for share in self.cfg.get().shares:
            if share.id == share_id:
                return share
        raise ValueError("unknown share")

    # -- path resolution ---------------------------------------------------
    def resolve(self, share_id: str, rel_path: str) -> Path:
        share = self.get_share(share_id)
        root = Path(share.path)
        if not root.exists():
            raise ValueError("share path is no longer available")
        rel = (rel_path or "").lstrip("/\\")
        try:
            return safe_resolve(Path(rel), root) if rel else root
        except ValueError as exc:
            raise ValueError("path escapes the shared directory") from exc

    def require_writable(self, share_id: str) -> ShareConfig:
        share = self.get_share(share_id)
        if share.read_only:
            raise PermissionError("share is read-only")
        return share

    def require_delete(self, share_id: str) -> ShareConfig:
        share = self.require_writable(share_id)
        if not share.allow_delete:
            raise PermissionError("deletion is not enabled for this share")
        return share

    # -- directory listing -------------------------------------------------
    def list_dir(self, share_id: str, rel_path: str) -> list[dict]:
        path = self.resolve(share_id, rel_path)
        if not path.is_dir():
            raise ValueError("not a directory")
        entries = []
        for entry in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                stat = entry.stat()
                is_dir = entry.is_dir()
            except OSError:
                continue
            entries.append(
                {
                    "name": entry.name,
                    "is_dir": is_dir,
                    "size": 0 if is_dir else stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        return entries

    def prepare_task_spec(self, spec: dict) -> dict:
        """Resolve share-relative task inputs to absolute safe paths."""
        prepared = dict(spec)
        roots = [str(Path(s.path).resolve()) for s in self.cfg.get().shares]

        def _absolute(share_key: str, path_key: str, out_key: str, writable: bool = False) -> None:
            if not prepared.get(share_key) or prepared.get(path_key) is None:
                return
            share_id = prepared[share_key]
            if writable:
                self.require_writable(share_id)
            target = self.resolve(share_id, str(prepared[path_key]))
            prepared[out_key] = str(target)

        _absolute("share_id", "path", "resolved_path")
        _absolute("dest_share_id", "dest_path", "destination", writable=True)
        _absolute("out_share_id", "out_path", "output_dir", writable=True)
        prepared["allowed_roots"] = roots
        return prepared

    def shares_payload(self) -> list[dict]:
        return self.list_share_views()

    def network_hint(self) -> dict:
        return {
            "hostname": platform.node(),
            "addresses": get_lan_ips(),
            "os": platform.system(),
        }
