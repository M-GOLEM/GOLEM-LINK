"""Verified automatic updates and remote maintenance.

A release is described by ``manifest.json`` (version, channel, notes and one
artifact per platform with its SHA-256). Manifests and artifacts come from a
folder the operator controls, or from a paired node over the pinned-TLS peer
API. Nothing is executed before its hash matches the manifest.

The two platforms have different privilege models, and the agent respects
that difference instead of working around it:

* Windows: the agent runs as a service with the rights the installer needs,
  so applying an update is a detached, logged installer run. The installer
  keeps the previous program folder as a revert path.
* Linux: the service runs unprivileged with ``NoNewPrivileges=yes`` and a
  read-only system, so it cannot (and will not) elevate itself. It stages and
  verifies the artifact, then either hands it to the opt-in root helper
  installed with ``--allow-remote-updates`` (one-shot systemd unit triggered by
  a request file) or shows the operator the one ``sudo`` command that applies it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from . import remote_maintenance as remote
from .config import ConfigManager
from .events import EventBus
from .version import VERSION

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
PLATFORM_KEYS = {"Windows": "windows", "Linux": "linux"}
ARTIFACT_KEYS = ("windows", "linux")
_VERSION_RE = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
STATE_KEEP = 40


def current_platform() -> str | None:
    return PLATFORM_KEYS.get(platform.system())


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match(str(text or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def is_newer(candidate: str, current: str = VERSION) -> bool:
    left, right = parse_version(candidate), parse_version(current)
    if left is None or right is None:
        return False
    return left > right


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def normalize_manifest(data: Any) -> dict:
    """Validate a manifest and return a copy with only known fields."""
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    version = str(data.get("version") or "").strip()
    if parse_version(version) is None:
        raise ValueError("manifest version must look like 1.2.3")
    channel = str(data.get("channel") or "stable").strip()[:40]
    notes = str(data.get("notes") or "")[:4000]
    artifacts: dict[str, dict] = {}
    raw_artifacts = data.get("artifacts")
    if not isinstance(raw_artifacts, dict):
        raise ValueError("manifest artifacts must be an object")
    for key in ARTIFACT_KEYS:
        entry = raw_artifacts.get(key)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"artifact '{key}' must be an object")
        name = str(entry.get("file") or "").strip()
        if not name or Path(name).name != name or name.startswith(".") or "\\" in name:
            raise ValueError(f"artifact '{key}' has an unsafe file name")
        digest = str(entry.get("sha256") or "").strip().lower()
        if not _SHA_RE.match(digest):
            raise ValueError(f"artifact '{key}' has an invalid sha256")
        size = entry.get("size")
        if size is not None:
            try:
                size = int(size)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"artifact '{key}' has an invalid size") from exc
            if size <= 0:
                raise ValueError(f"artifact '{key}' has an invalid size")
        artifacts[key] = {"file": name, "sha256": digest, "size": size}
    if not artifacts:
        raise ValueError("manifest lists no artifacts")
    return {
        "version": version.lstrip("v"),
        "channel": channel,
        "notes": notes,
        "published_at": str(data.get("published_at") or ""),
        "artifacts": artifacts,
    }


def select_artifact(manifest: dict, platform_key: str | None) -> dict | None:
    if not manifest or not platform_key:
        return None
    entry = (manifest.get("artifacts") or {}).get(platform_key)
    if not entry:
        return None
    return {"platform": platform_key, **entry}


def verify_artifact(path: Path, sha256: str, size: int | None = None) -> bool:
    path = Path(path)
    if not path.is_file():
        return False
    if size is not None and path.stat().st_size != size:
        return False
    return sha256_file(path) == sha256.lower()


class UpdateManager:
    """Local update state machine plus the fleet-facing helpers."""

    def __init__(
        self,
        cfg: ConfigManager,
        bus: EventBus,
        identity: Any = None,
        *,
        platform: str | None = None,
        installer_launcher: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.bus = bus
        self.identity = identity
        self.platform = platform if platform is not None else current_platform()
        self.peers: Any = None
        self._installer_launcher = installer_launcher
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.work_dir = cfg.data_dir / "updates"
        self.state = "idle"
        self.latest: dict | None = None
        self.artifact: dict | None = None
        self.error: str | None = None
        self.progress = 0.0
        self.checked_at: float | None = None
        self.history: list[dict] = []
        self._load_state()

    # -- paths -------------------------------------------------------------
    @property
    def source_dir(self) -> Path:
        raw = self.cfg.get().update.source_dir.strip()
        return Path(raw).expanduser() if raw else self.work_dir

    @property
    def state_path(self) -> Path:
        return self.work_dir / "state.json"

    def install_dir(self) -> Path:
        env = os.environ.get("GOLEM_INSTALL_DIR")
        if env:
            return Path(env)
        if getattr(sys, "frozen", False) and os.name == "nt":
            return Path(sys.executable).resolve().parent
        return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GOLEM LINK"

    def staged_path(self) -> Path | None:
        if not self.artifact or not self.artifact.get("staged"):
            return None
        return Path(self.artifact["staged"])

    # -- status ------------------------------------------------------------
    def status(self) -> dict:
        cfg = self.cfg.get().update
        latest = self.latest or {}
        remote_install = self.platform == "linux" and remote.helper_ready()
        revert = self.revert_plan()
        rollback_supported = bool(revert.get("available")) and (
            self.platform == "windows"
            or (self.platform == "linux" and remote_install and remote.helper_supports("rollback"))
        )
        return {
            "state": self.state,
            "current_version": VERSION,
            "latest_version": latest.get("version"),
            "channel": cfg.channel,
            "platform": self.platform,
            "enabled": cfg.enabled,
            "auto_check": cfg.auto_check,
            "auto_stage": cfg.auto_stage,
            "auto_install": cfg.auto_install,
            "auto_install_supported": self.platform == "windows" or remote_install,
            "remote_install": remote_install,
            "remote_install_supported": self.platform == "linux",
            "check_interval_s": cfg.check_interval_s,
            "source_dir": str(self.source_dir),
            "checked_at": self.checked_at,
            "notes": latest.get("notes"),
            "published_at": latest.get("published_at"),
            "error": self.error,
            "progress": round(self.progress, 3),
            "manifest": self.latest,
            "artifact": self.artifact,
            "install": self.install_plan(),
            "revert": revert,
            "rollback_supported": rollback_supported,
            "remote_result": remote.read_result(self.work_dir) if self.platform == "linux" else None,
            "history": self.history[-STATE_KEEP:],
        }

    def install_plan(self) -> dict | None:
        staged = self.staged_path()
        if not staged or self.platform is None:
            return None
        if self.platform == "windows":
            return {
                "mode": "windows-service",
                "requires_admin": True,
                "requires_operator": False,
                "command": self._windows_command(staged),
                "note": (
                    "The agent runs the verified installer as a detached, logged task; "
                    "the service stops and starts by itself. The previous program folder "
                    "is kept for a rollback."
                ),
            }
        if self.platform == "linux":
            if remote.helper_ready():
                return {
                    "mode": "privileged-helper",
                    "requires_admin": False,
                    "requires_operator": False,
                    "command": "automatic (root helper, verified artifact)",
                    "note": (
                        "The node was installed with --allow-remote-updates, so a root-owned "
                        "one-shot unit re-checks the artifact and runs the installer; the "
                        "service restarts by itself. The previous program folder is kept "
                        "for a rollback. Revoke with: sudo rm "
                        "/etc/golem-link/remote-updates.enabled && sudo systemctl disable "
                        "--now golem-link-update.path"
                    ),
                }
            return {
                "mode": "operator-sudo",
                "requires_admin": True,
                "requires_operator": True,
                "command": f"sudo {shlex.quote(str(staged))} --upgrade --no-auto-pair --no-firewall",
                "note": (
                    "The Linux service runs unprivileged with NoNewPrivileges, so it will "
                    "never elevate itself. The artifact is verified and staged; run this "
                    "one command to apply it, or reinstall with --allow-remote-updates to "
                    "let paired devices apply verified updates. The service restarts by itself."
                ),
            }
        return None

    def revert_plan(self) -> dict:
        plan: dict[str, Any] = {"available": False}
        if self.platform == "windows":
            backup = self._windows_backup_dir()
            if backup.exists():
                plan = {
                    "available": True,
                    "kind": "program-folder-backup",
                    "path": str(backup),
                    "note": "Restore with the previous installer, or rename this folder back over the program folder.",
                }
        elif self.platform == "linux":
            backup = Path("/opt/golem-link.previous")
            if backup.exists():
                plan = {
                    "available": True,
                    "kind": "program-folder-backup",
                    "path": str(backup),
                    "command": (
                        "sudo systemctl stop golem-link && sudo mv /opt/golem-link "
                        "/opt/golem-link.failed && sudo mv /opt/golem-link.previous "
                        "/opt/golem-link && sudo systemctl start golem-link"
                    ),
                }
        # Any older staged release can be re-run by hand, which is the revert
        # path when the program-folder backup is not present.
        staged_root = self.work_dir / "staged"
        current = parse_version(VERSION) or (0, 0, 0)
        older: list[tuple[tuple[int, int, int], Path]] = []
        if staged_root.is_dir():
            for folder in staged_root.iterdir():
                version = parse_version(folder.name)
                if not folder.is_dir() or version is None or version >= current:
                    continue
                for file in sorted(folder.glob("*.exe")) + sorted(folder.glob("*.run")):
                    older.append((version, file))
        if older:
            older.sort(key=lambda row: row[0])
            plan["previous_installer"] = str(older[-1][1])
        return plan

    # -- state persistence -------------------------------------------------
    def _load_state(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._resolve_interrupted(None)
            return
        self.latest = data.get("latest") or None
        self.artifact = data.get("artifact") or None
        self.checked_at = data.get("checked_at")
        self.history = list(data.get("history") or [])[-STATE_KEEP:]
        self.error = data.get("error")
        self.state = str(data.get("state") or "idle")
        self._resolve_interrupted(data)
        if self.state == "downloading":
            # A process exit in the middle of a download leaves no usable file.
            self.state = "available" if self.latest else "idle"
            self.progress = 0.0

    def _resolve_interrupted(self, data: dict | None) -> None:
        if not data or data.get("state") != "installing":
            return
        target = (data.get("latest") or {}).get("version")
        if target and parse_version(target) == parse_version(VERSION):
            self.state = "updated"
            self._note("updated", f"running the updated version {VERSION}")
        else:
            self.state = "error"
            self.error = (
                f"update to {target or 'a newer release'} did not finish; this node still "
                f"runs {VERSION}. Check the install log in {self.work_dir}."
            )
            result = remote.read_result(self.work_dir)
            if result and not result.get("ok"):
                detail = result.get("error") or f"helper exit {result.get('rc')}"
                self.error += f" The helper reported: {detail}"
            self._note("install-failed", self.error)

    def _persist(self) -> None:
        payload = {
            "state": self.state,
            "latest": self.latest,
            "artifact": self.artifact,
            "checked_at": self.checked_at,
            "error": self.error,
            "history": self.history[-STATE_KEEP:],
        }
        try:
            self.work_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.work_dir), prefix=".state-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.state_path)
        except OSError:
            log.warning("could not persist update state", exc_info=True)

    def _note(self, event: str, detail: str = "") -> None:
        self.history.append({"ts": time.time(), "event": event, "detail": detail})
        self.history = self.history[-STATE_KEEP:]

    def _set(self, state: str, error: str | None = None) -> None:
        self.state = state
        self.error = error
        self._persist()
        self._publish()

    def _publish(self) -> None:
        try:
            self.bus.publish("update", self.status())
        except Exception:  # pragma: no cover - the bus must never break updates
            log.debug("update status publish failed", exc_info=True)

    # -- discovery ---------------------------------------------------------
    def read_local_manifest(self) -> dict | None:
        path = self.source_dir / MANIFEST_NAME
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            manifest = normalize_manifest(data)
        except (OSError, ValueError) as exc:
            log.warning("ignoring update manifest at %s: %s", path, exc)
            return None
        node = self.identity.node_id if self.identity else ""
        return {
            **manifest,
            "source": "local",
            "source_node": node,
            "source_name": "this node",
        }

    async def _peer_manifests(self) -> list[dict]:
        peers = self.peers
        if peers is None:
            return []
        runtimes = [
            peer
            for peer in list(getattr(peers, "peers", {}).values())
            if peer.status == "online" and getattr(peer, "remote_allows_updates", False)
        ]

        async def fetch(peer: Any) -> dict | None:
            try:
                payload = await peers.client.get_json(
                    peer.target(), "/peer/update/manifest", timeout=8
                )
                raw = payload.get("manifest") if isinstance(payload, dict) else payload
                manifest = normalize_manifest(raw)
            except Exception as exc:
                log.debug("update manifest from %s unavailable: %s", peer.node_id, exc)
                return None
            return {
                **manifest,
                "source": "peer",
                "source_node": peer.node_id,
                "source_name": peer.name,
            }

        if not runtimes:
            return []
        results = await asyncio.gather(*(fetch(peer) for peer in runtimes))
        return [row for row in results if row]

    async def _best_candidate(self) -> dict | None:
        channel = self.cfg.get().update.channel or "stable"
        candidates: list[dict] = []
        local = self.read_local_manifest()
        if local:
            candidates.append(local)
        candidates.extend(await self._peer_manifests())
        usable = [
            row
            for row in candidates
            if row.get("channel") == channel and is_newer(row["version"], VERSION)
        ]
        if not usable:
            return None
        # Highest numeric version wins; a local copy breaks a tie so the fleet
        # does not re-fetch what the operator already placed on this node.
        usable.sort(key=lambda row: (parse_version(row["version"]), row["source"] == "local"))
        return usable[-1]

    # -- check / download / apply -----------------------------------------
    async def check(self) -> dict:
        if not self.cfg.get().update.enabled:
            self._set("disabled")
            return self.status()
        async with self._lock:
            await self._check_locked()
        return self.status()

    async def _check_locked(self) -> None:
        self._set("checking")
        self.progress = 0.0
        try:
            candidate = await self._best_candidate()
        except Exception as exc:
            log.exception("update check failed")
            self._set("error", f"update check failed: {exc}")
            return
        self.checked_at = time.time()
        if candidate is None:
            self.latest = None
            self.artifact = None
            self._note("checked", "no newer release found")
            self._set("up_to_date")
            return
        self.latest = candidate
        artifact = select_artifact(candidate, self.platform)
        if artifact is None:
            self.artifact = None
            self.error = None
            self._note("checked", f"{candidate['version']} has no artifact for {self.platform}")
            self._set("no_artifact")
            return
        staged = self._staged_target(candidate["version"], artifact["file"])
        self.artifact = {
            **artifact,
            "source": candidate["source"],
            "source_node": candidate.get("source_node", ""),
            "source_name": candidate.get("source_name", ""),
            "staged": str(staged) if staged.is_file() else None,
        }
        self.error = None
        self._note("checked", f"found {candidate['version']} from {candidate['source_name']}")
        if staged.is_file() and verify_artifact(staged, artifact["sha256"], artifact["size"]):
            self._set("ready")
        else:
            self._set("available")

    def _staged_target(self, version: str, filename: str) -> Path:
        return self.work_dir / "staged" / version / filename

    async def download(self) -> dict:
        if not self.cfg.get().update.enabled:
            raise ValueError("updates are disabled on this node")
        async with self._lock:
            await self._download_locked()
        return self.status()

    async def _download_locked(self) -> None:
        if not self.latest or not self.artifact:
            await self._check_locked()
        if not self.latest or not self.artifact:
            raise ValueError("no newer release is available to download")
        version = self.latest["version"]
        artifact = self.artifact
        dest = self._staged_target(version, artifact["file"])
        if dest.is_file() and verify_artifact(dest, artifact["sha256"], artifact.get("size")):
            self.artifact["staged"] = str(dest)
            self._set("ready")
            return
        self._set("downloading")
        self.progress = 0.0
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if artifact.get("source") == "peer":
                await self._download_from_peer(artifact, dest)
            else:
                await asyncio.to_thread(self._copy_local, artifact, dest)
            if not verify_artifact(dest, artifact["sha256"], artifact.get("size")):
                dest.unlink(missing_ok=True)
                raise ValueError("the downloaded artifact failed SHA-256 verification")
            self.artifact["staged"] = str(dest)
            self.progress = 1.0
            self._note("downloaded", f"{artifact['file']} verified ({artifact['sha256'][:16]}…)")
            self._set("ready")
        except Exception as exc:
            log.warning("update download failed: %s", exc)
            self.error = str(exc)
            self._note("download-failed", str(exc))
            self._set("error", str(exc))

    def _copy_local(self, artifact: dict, dest: Path) -> None:
        source = self.source_dir / artifact["file"]
        if not source.is_file():
            raise ValueError(f"artifact '{artifact['file']}' is not in {self.source_dir}")
        if not verify_artifact(source, artifact["sha256"], artifact.get("size")):
            raise ValueError(f"artifact '{artifact['file']}' does not match the manifest hash")
        part = dest.with_name(dest.name + ".part")
        shutil.copyfile(source, part)
        os.replace(part, dest)
        self.progress = 1.0

    async def _download_from_peer(self, artifact: dict, dest: Path) -> None:
        peers = self.peers
        node_id = artifact.get("source_node")
        if peers is None or not node_id:
            raise ValueError("the peer that offered this update is no longer reachable")
        peer = getattr(peers, "peers", {}).get(node_id)
        if peer is None or peer.status != "online":
            raise ValueError("the peer that offered this update is offline")

        def progress(done: int, total: int | None) -> None:
            if total:
                value = min(done / total, 1.0)
            else:
                value = 0.0
            if value - self.progress >= 0.02 or value >= 1.0:
                self.progress = value
                self._publish()

        await peers.client.download_to(
            peer.target(),
            f"/peer/update/artifact/{artifact['file']}",
            dest,
            expected_sha256=artifact["sha256"],
            expected_size=artifact.get("size"),
            progress=progress,
        )

    async def apply(self) -> dict:
        if not self.cfg.get().update.enabled:
            raise ValueError("updates are disabled on this node")
        async with self._lock:
            if self.state == "installing":
                return self.status()
            if self.latest is None:
                await self._check_locked()
            if self.latest is None or not self.artifact:
                raise ValueError("no newer release is available to install")
            staged = self.staged_path()
            if not staged or not staged.is_file():
                await self._download_locked()
                staged = self.staged_path()
            if not staged or not verify_artifact(staged, self.artifact["sha256"], self.artifact.get("size")):
                raise ValueError("the staged artifact is missing or does not match the manifest")
            if self.platform == "windows":
                self._note("installing", f"{self.latest['version']} from {staged.name}")
                self._set("installing")
                self._launch_windows_update(staged)
                self._note("scheduled", "the installer will stop, replace and restart the service")
                self._persist()
            elif remote.helper_ready():
                nonce = self._request_helper_install(staged)
                # The helper stops the service to upgrade it, so this wait
                # usually ends when this process is replaced. It only matters
                # when the helper rejects the request quickly.
                await self._wait_helper_result(nonce)
            else:
                # Never escalate from the service. The operator gets one
                # verified, staged command; the state stays "ready".
                self._note("staged", f"{self.latest['version']} verified; waiting for the operator")
                self._set("ready")
        return self.status()

    # -- linux helper ------------------------------------------------------
    def _request_helper_install(self, staged: Path) -> str:
        work = self.work_dir
        work.mkdir(parents=True, exist_ok=True)
        remote.result_path(work).unlink(missing_ok=True)
        nonce = secrets.token_hex(16)
        payload = {
            "action": "install",
            "nonce": nonce,
            "version": self.latest["version"],
            "artifact": str(staged),
            "sha256": self.artifact["sha256"],
            "size": self.artifact.get("size"),
        }
        remote.write_request(work, payload)
        self._note("remote-install", f"{self.latest['version']} handed to the root helper")
        self._set("installing")
        return nonce

    async def _wait_helper_result(self, nonce: str, timeout: float = 60.0) -> dict | None:
        path = remote.result_path(self.work_dir)
        request = remote.request_path(self.work_dir)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("nonce") != nonce:
                continue
            if data.get("ok"):
                self.progress = 1.0
                self._note("installed", f"the helper finished with code {data.get('rc')}")
                self._persist()
                return data
            message = str(
                data.get("error")
                or f"the root helper could not apply the update (exit {data.get('rc')})"
            )
            self._note("install-failed", message)
            self._set("error", message)
            raise ValueError(message)
        # The helper deletes the request as soon as it starts, so a request
        # that is still there means the path unit never fired.
        if request.exists():
            request.unlink(missing_ok=True)
            message = (
                "the root helper did not pick up the update request; check that "
                "golem-link-update.path is enabled (systemctl status golem-link-update.path)"
            )
            self._note("install-failed", message)
            self._set("error", message)
            raise ValueError(message)
        self._note("remote-install", "the helper picked up the request; the service restarts itself")
        return None

    # -- windows helper ----------------------------------------------------
    def _windows_command(self, staged: Path) -> str:
        install_dir = self.install_dir()
        return (
            f'"{staged}" /Update /Silent /NoPair /NoBrowser '
            f'/InstallDir="{install_dir}"'
        )

    def _windows_backup_dir(self) -> Path:
        install_dir = self.install_dir()
        return install_dir.with_name(install_dir.name + ".previous")

    def _launch_windows_update(self, staged: Path) -> None:
        if os.name != "nt":
            raise ValueError("the detached installer is only available on Windows")
        script = self._write_update_script(staged)
        if self._installer_launcher is not None:
            self._installer_launcher(staged, script)
            return
        self._run_detached(script, "GolemLinkUpdate")

    def _write_update_script(self, staged: Path) -> Path:
        install_dir = self.install_dir()
        log_path = self.work_dir / "install.log"
        result = self.work_dir / "install-result.txt"
        script = self.work_dir / "install-update.cmd"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "@echo off",
            "setlocal",
            "rem GOLEM LINK verified self-update; the service stops inside the installer.",
            "timeout /t 6 /nobreak >nul",
            f'"{staged}" /Update /Silent /NoPair /NoBrowser /InstallDir="{install_dir}" '
            f'>> "{log_path}" 2>&1',
            f'echo %ERRORLEVEL%> "{result}"',
            "schtasks /Delete /TN GolemLinkUpdate /F >nul 2>&1",
            "endlocal",
            "",
        ]
        script.write_text("\r\n".join(lines), encoding="utf-8")
        return script

    def _run_detached(self, script: Path, task_name: str) -> None:
        """Run a script outside the service's process tree.

        The service wrapper may kill its children when it stops, so schedule
        the helper as a one-shot SYSTEM task and start it. If task scheduling
        is unavailable (for example a foreground development run), fall back
        to a detached process.
        """
        task_ok = False
        if os.name == "nt":
            create = subprocess.run(
                [
                    "schtasks", "/Create", "/TN", task_name, "/TR", f'"{script}"',
                    "/SC", "ONCE", "/ST", "23:59", "/RU", "SYSTEM", "/RL", "HIGHEST", "/F",
                ],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            )
            if create.returncode == 0:
                run = subprocess.run(
                    ["schtasks", "/Run", "/TN", task_name],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=30,
                )
                task_ok = run.returncode == 0
            if not task_ok:
                log.warning(
                    "scheduled update task unavailable (%s); using a detached process",
                    (create.stderr or create.stdout or "").strip()[:300],
                )
        if task_ok:
            return
        flags = 0
        if os.name == "nt":
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            flags |= 0x01000000  # CREATE_BREAKAWAY_FROM_JOB, best effort
        try:
            subprocess.Popen(  # noqa: S603 - fixed helper script we just wrote
                ["cmd.exe", "/c", str(script)] if os.name == "nt" else ["/bin/sh", str(script)],
                cwd=str(self.work_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
            )
        except OSError:
            if os.name == "nt":
                subprocess.Popen(
                    ["cmd.exe", "/c", str(script)],
                    cwd=str(self.work_dir),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                raise

    # -- maintenance -------------------------------------------------------
    def restart_plan(self) -> dict:
        if self.platform == "windows":
            service = self.install_dir() / "golem-link-service.exe"
            return {
                "mode": "windows-service",
                "requires_operator": False,
                "command": f'"{service}" restart',
            }
        return {
            "mode": "operator-sudo",
            "requires_operator": True,
            "command": "sudo systemctl restart golem-link",
        }

    def restart_agent(self) -> dict:
        if self.platform == "windows" and os.name == "nt":
            service = self.install_dir() / "golem-link-service.exe"
            if not service.is_file():
                raise ValueError(f"service wrapper not found at {service}")
            script = self.work_dir / "restart-agent.cmd"
            self.work_dir.mkdir(parents=True, exist_ok=True)
            script.write_text(
                "\r\n".join(
                    [
                        "@echo off",
                        "timeout /t 4 /nobreak >nul",
                        f'"{service}" stop',
                        f'"{service}" start',
                        "schtasks /Delete /TN GolemLinkRestart /F >nul 2>&1",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            self._run_detached(script, "GolemLinkRestart")
            return {"ok": True, "scheduled": True, **self.restart_plan()}
        if self.platform == "linux":
            # The unit runs with Restart=always, so exiting cleanly is the
            # whole restart: systemd brings the agent back in RestartSec and no
            # privilege is borrowed to do it.
            self._exit_soon()
            return {
                "ok": True,
                "scheduled": True,
                "mode": "systemd-restart",
                "requires_operator": False,
                "note": (
                    "The agent exits and systemd (Restart=always) starts it again "
                    "within a few seconds; the pair link reconnects by itself."
                ),
            }
        return {"ok": False, "scheduled": False, **self.restart_plan()}

    def _exit_soon(self, delay: float = 1.2) -> None:
        """Leave the event loop alive long enough to flush the HTTP response."""
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(delay, os._exit, 0)
        except RuntimeError:  # pragma: no cover - only outside a loop
            import threading

            threading.Timer(delay, os._exit, args=(0,)).start()

    # -- rollback ----------------------------------------------------------
    def rollback(self) -> dict:
        """Put the previous program folder back and restart on it.

        Rollback is an update action, so it uses the same trust boundaries:
        Windows runs a detached SYSTEM script; Linux hands the request to the
        opt-in root helper. The restored agent starts with updates disabled so
        it cannot silently reinstall the release that was just rolled back.
        """
        plan = self.revert_plan()
        if not plan.get("available"):
            raise ValueError("this node has no rollback backup")
        if self.platform == "windows" and os.name == "nt":
            return self._start_windows_rollback(plan)
        if self.platform == "linux":
            if not remote.helper_ready():
                raise ValueError(
                    "remote rollback needs the opt-in root helper; reinstall with "
                    "--allow-remote-updates, or run by hand: "
                    + str(plan.get("command") or "")
                )
            if not remote.helper_supports("rollback"):
                raise ValueError(
                    "the installed root helper predates rollback; upgrade this node "
                    "once with the 1.4.0 installer, then retry"
                )
            nonce = secrets.token_hex(16)
            remote.result_path(self.work_dir).unlink(missing_ok=True)
            remote.write_request(
                self.work_dir,
                {"action": "rollback", "nonce": nonce},
            )
            self._note("rollback", f"restore {plan['path']} handed to the root helper")
            self._persist()
            return {
                "ok": True,
                "scheduled": True,
                "mode": "privileged-helper",
                "nonce": nonce,
                "restore_from": plan["path"],
                "note": (
                    "The root helper stops the service, swaps the program folders and "
                    "starts the restored build with updates disabled. If it fails it "
                    "puts the newer build back."
                ),
            }
        raise ValueError("rollback is not supported on this platform")

    def _start_windows_rollback(self, plan: dict) -> dict:
        if os.name != "nt":
            raise ValueError("the detached rollback is only available on Windows")
        install_dir = self.install_dir()
        backup = self._windows_backup_dir()
        service = install_dir / "golem-link-service.exe"
        if not backup.is_dir():
            raise ValueError(f"no program backup at {backup}")
        if not service.is_file():
            raise ValueError(f"service wrapper not found at {service}")
        failed = install_dir.with_name(f"{install_dir.name}.failed-{int(time.time())}")
        log_path = self.work_dir / "rollback.log"
        script = self.work_dir / "rollback.cmd"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        config_path = self.cfg.data_dir / "config.json"
        powershell = (
            f"powershell -NoProfile -Command \"$p='{config_path}'; $c=Get-Content -LiteralPath $p -Raw "
            "| ConvertFrom-Json; $c.update.enabled=$false; $c.update.auto_install=$false; "
            "$c.update.auto_stage=$false; [IO.File]::WriteAllText($p, ($c | ConvertTo-Json -Depth 20))\""
        )
        lines = [
            "@echo off",
            "setlocal",
            "rem GOLEM LINK rollback: restore the program folder the installer kept.",
            "timeout /t 6 /nobreak >nul",
            f'"{service}" stop >> "{log_path}" 2>&1',
            "timeout /t 3 /nobreak >nul",
            f'move /y "{install_dir}" "{failed}" >> "{log_path}" 2>&1',
            "if errorlevel 1 goto failed_move",
            f'move /y "{backup}" "{install_dir}" >> "{log_path}" 2>&1',
            "if errorlevel 1 goto failed_restore",
            powershell + f' >> "{log_path}" 2>&1',
            f'"{service}" start >> "{log_path}" 2>&1',
            "if errorlevel 1 goto failed_start",
            "goto done",
            ":failed_move",
            f'"{service}" start >> "{log_path}" 2>&1',
            "goto done",
            ":failed_restore",
            f'move /y "{failed}" "{install_dir}" >> "{log_path}" 2>&1',
            f'"{service}" start >> "{log_path}" 2>&1',
            "goto done",
            ":failed_start",
            f'"{service}" stop >> "{log_path}" 2>&1',
            f'move /y "{install_dir}" "{backup}" >> "{log_path}" 2>&1',
            f'move /y "{failed}" "{install_dir}" >> "{log_path}" 2>&1',
            f'"{service}" start >> "{log_path}" 2>&1',
            ":done",
            "schtasks /Delete /TN GolemLinkRollback /F >nul 2>&1",
            "endlocal",
            "",
        ]
        script.write_text("\r\n".join(lines), encoding="utf-8")
        self._note("rollback", f"scheduled a restore of {backup}")
        self._persist()
        self._run_detached(script, "GolemLinkRollback")
        return {
            "ok": True,
            "scheduled": True,
            "mode": "windows-service",
            "restore_from": str(backup),
            "failed_folder": str(failed),
            "note": (
                "The detached SYSTEM task stops the service, restores the backup "
                "folder and starts it with updates disabled. See rollback.log."
            ),
        }

    # -- background loop ---------------------------------------------------
    async def run(self) -> None:
        self._stop.clear()
        # Give the rest of the service a moment to start before the first check.
        await self._sleep(min(20.0, max(5.0, self.cfg.get().update.check_interval_s)))
        while not self._stop.is_set():
            cfg = self.cfg.get().update
            try:
                if cfg.enabled and cfg.auto_check:
                    status = await self.check()
                    # Staging is safe everywhere: the download is hash- and
                    # size-checked before it counts as ready.
                    if cfg.auto_stage and status["state"] == "available":
                        status = await self.download()
                    auto_install_ready = self.platform == "windows" or (
                        self.platform == "linux" and remote.helper_ready()
                    )
                    if (
                        cfg.auto_install
                        and auto_install_ready
                        and status["state"] in ("available", "ready")
                    ):
                        if status["state"] == "available":
                            await self.download()
                        await self.apply()
                        if self.state == "installing":
                            return
            except Exception:
                log.exception("automatic update loop failed")
            interval = max(60.0, min(float(cfg.check_interval_s), 30 * 86400.0))
            await self._sleep(interval)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def stop(self) -> None:
        self._stop.set()

    def start_task(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="updates")
        return self._task
