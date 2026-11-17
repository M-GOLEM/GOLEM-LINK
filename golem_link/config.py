"""Configuration model, paths and atomic persistence for GOLEM LINK."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .version import DEFAULT_BEACON_PORT, DEFAULT_DASHBOARD_PORT, DEFAULT_PEER_PORT

log = logging.getLogger(__name__)


def _default_data_dir() -> Path:
    env = os.environ.get("GOLEM_LINK_DATA")
    if env:
        return Path(env).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return (Path(base) / "GolemLink").resolve()
    if os.geteuid() == 0:
        return Path("/var/lib/golem-link")
    return (Path.home() / ".local" / "share" / "golem-link").resolve()


class ThresholdConfig(BaseModel):
    cpu_warn_percent: float = 80.0
    cpu_crit_percent: float = 92.0
    ram_warn_percent: float = 80.0
    ram_crit_percent: float = 90.0
    # No universal safe temperature exists; when unset we only alert on values
    # reported by the platform's thermal sensors if the user sets a limit.
    temp_warn_c: float | None = None
    temp_crit_c: float | None = None
    disk_free_warn_percent: float = 10.0
    disk_free_crit_percent: float = 4.0
    net_latency_warn_ms: float = 150.0
    net_loss_warn_percent: float = 5.0
    sample_interval_s: float = 2.0
    # Under sustained high CPU the monitor samples less often so telemetry
    # itself stops competing with the work that is already loading the host.
    adaptive_sampling: bool = True
    max_sample_interval_s: float = 6.0
    # Emergency thermal policy: when a critical temperature is sustained the
    # node refuses to accept new tasks until it cools down.
    thermal_sustained_samples: int = 3

    @field_validator("cpu_crit_percent")
    @classmethod
    def _cpu_order(cls, v: float, info: Any) -> float:
        if v <= info.data.get("cpu_warn_percent", 0.0):
            raise ValueError("cpu_crit_percent must be greater than cpu_warn_percent")
        return v

    @field_validator("ram_crit_percent")
    @classmethod
    def _ram_order(cls, v: float, info: Any) -> float:
        if v <= info.data.get("ram_warn_percent", 0.0):
            raise ValueError("ram_crit_percent must be greater than ram_warn_percent")
        return v


class SchedulingConfig(BaseModel):
    enabled: bool = True
    policy: Literal["balanced", "local_first", "remote_first"] = "balanced"
    allow_remote_execution: bool = True
    max_concurrent_tasks: int = 2
    default_timeout_s: int = 3600
    max_retries: int = 2
    retry_backoff_s: float = 5.0
    reserve_cpu_percent: float = 15.0
    reserve_ram_percent: float = 10.0
    rebalance_on_overload: bool = True
    # Keep the host responsive: when this node is already this busy and a
    # healthy peer is available, new work is placed on the peer before it can
    # start locally. The host's own running OS processes are never touched.
    keep_host_light: bool = True
    keep_host_cpu_percent: float = 55.0
    keep_host_ram_percent: float = 70.0
    # Ranking: a queued task that keeps waiting gains this many priority points
    # per minute, up to the cap, so low-priority work cannot starve forever.
    rank_aging_per_min: float = 2.0
    rank_aging_max: float = 25.0
    # Automatic relief: when this node stays above the host-light limits while
    # running its own work, that work is stopped and requeued for a healthy
    # peer instead of freezing the desktop. Bounded per task; pinned targets
    # and operating-system processes are never touched.
    preempt_on_overload: bool = True
    preempt_after_s: float = 15.0
    preempt_max_per_task: int = 2
    # The agent releases its own cached memory back to the OS under pressure.
    relieve_memory: bool = True
    relieve_ram_percent: float = 80.0
    relieve_cooldown_s: float = 120.0
    # Run task workers below normal priority so the desktop and dashboard stay
    # responsive while a heavy task is running.
    nice_workers: bool = True
    enabled_plugins: list[str] = Field(
        default_factory=lambda: ["file_hash", "compress", "image_resize", "text_stats", "disk_benchmark"]
    )
    # Running user-supplied Python is powerful and risky; it stays off until
    # the operator explicitly enables it on the executing node.
    allow_custom_python: bool = False


class DashboardConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = DEFAULT_DASHBOARD_PORT
    admin_password_hash: str | None = None
    admin_password_salt: str | None = None
    viewer_password_hash: str | None = None
    viewer_password_salt: str | None = None
    session_ttl_s: int = 12 * 3600
    login_max_attempts: int = 5
    login_window_s: int = 300


class PeerServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = DEFAULT_PEER_PORT


class DiscoveryConfig(BaseModel):
    mdns_enabled: bool = True
    beacon_enabled: bool = True
    beacon_port: int = DEFAULT_BEACON_PORT
    beacon_interval_s: float = 5.0
    manual_peers: list[str] = Field(default_factory=list)
    reconnect_interval_s: float = 5.0
    offline_after_s: float = 20.0


class ScreenConfig(BaseModel):
    node_id: str
    name: str
    side: Literal["left", "right"] = "left"
    width: int = 1920
    height: int = 1080


class InputSharingConfig(BaseModel):
    enabled: bool = False
    tool: Literal["deskflow", "input-leap", "none"] = "deskflow"
    server_node_id: str | None = None
    screens: list[ScreenConfig] = Field(default_factory=list)
    clipboard_sharing: bool = True
    auto_reconnect: bool = True


class ClipboardConfig(BaseModel):
    # Text relay between paired nodes. Only short text is relayed, never files
    # or images, and only from nodes this machine already grants control to.
    enabled: bool = True
    # Copy received text into the logged-in desktop clipboard automatically.
    # When the service cannot reach a desktop session the item waits in the
    # inbox with a manual Copy button instead.
    auto_copy: bool = True
    max_chars: int = 256 * 1024
    keep_items: int = 50


class UpdateConfig(BaseModel):
    # The agent never installs anything it has not verified, and it never
    # escalates its own privileges. On Windows the service already runs with
    # the rights the installer needs; on Linux an operator runs one sudo
    # command against the staged artifact.
    enabled: bool = True
    auto_check: bool = True
    # Autopilot: a newer release is downloaded and SHA-256 verified without a
    # click; on Windows the verified installer then replaces the service on its
    # own. Linux cannot escalate (NoNewPrivileges), so it stops at "ready" and
    # prints the one sudo command.
    auto_stage: bool = True
    auto_install: bool = True
    channel: str = "stable"
    check_interval_s: float = 6 * 3600
    source_dir: str = ""


class ShareConfig(BaseModel):
    id: str
    name: str
    path: str
    read_only: bool = False
    allow_delete: bool = False
    approved_at: str | None = None


class AppConfig(BaseModel):
    node_name: str = ""
    data_dir: str = ""
    first_run_complete: bool = False
    auto_start: bool = True
    log_level: str = "INFO"
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    peer: PeerServerConfig = Field(default_factory=PeerServerConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    input_sharing: InputSharingConfig = Field(default_factory=InputSharingConfig)
    clipboard: ClipboardConfig = Field(default_factory=ClipboardConfig)
    shares: list[ShareConfig] = Field(default_factory=list)
    alert_history_limit: int = 1000
    audit_history_limit: int = 5000


class ConfigManager:
    """Thread-safe loader/saver for ``<data_dir>/config.json``."""

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir).resolve() if data_dir else _default_data_dir()
        self.secrets_dir = self.data_dir / "secrets"
        self.peers_dir = self.secrets_dir / "peers"
        self.config_path = self.data_dir / "config.json"
        self._lock = threading.RLock()
        self._config = self._load()

    # -- paths -------------------------------------------------------------
    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.peers_dir.mkdir(parents=True, exist_ok=True)
        for p in (self.data_dir, self.secrets_dir, self.peers_dir):
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass

    def db_path(self) -> Path:
        return self.data_dir / "golem-link.db"

    def cert_path(self) -> Path:
        return self.secrets_dir / "tls_cert.pem"

    def key_path(self) -> Path:
        return self.secrets_dir / "tls_key.pem"

    def identity_path(self) -> Path:
        return self.secrets_dir / "identity.pem"

    # -- load / save -------------------------------------------------------
    def _load(self) -> AppConfig:
        migrated = False
        if self.config_path.exists():
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            cfg = AppConfig.model_validate(raw)
            # 1.3.0 autopilot: a pre-1.3 config never chose these fields, so the
            # hands-free defaults are applied once. After that the keys exist
            # and an operator who turns them off is respected.
            update_raw = raw.get("update")
            if not isinstance(update_raw, dict) or "auto_stage" not in update_raw:
                cfg.update.auto_stage = True
                cfg.update.auto_install = True
                migrated = True
        else:
            cfg = AppConfig()
        cfg.data_dir = str(self.data_dir)
        if not cfg.node_name:
            import socket

            cfg.node_name = socket.gethostname() or "golem-node"
        if migrated:
            try:
                self._write(cfg)
                log.info("enabled automatic update staging/installing for this existing config")
            except OSError:
                log.warning("could not persist the update autopilot defaults", exc_info=True)
        return cfg

    def get(self) -> AppConfig:
        with self._lock:
            return self._config

    def replace(self, cfg: AppConfig) -> None:
        with self._lock:
            cfg.data_dir = str(self.data_dir)
            self._config = cfg
            self._write(cfg)

    def update(self, mutator) -> AppConfig:
        """Mutate the config atomically: ``mutator(cfg)`` then persist."""
        with self._lock:
            mutator(self._config)
            self._config = AppConfig.model_validate(self._config.model_dump())
            self._write(self._config)
            return self._config

    def save(self) -> None:
        with self._lock:
            self._write(self._config)

    def _write(self, cfg: AppConfig) -> None:
        self.ensure_dirs()
        payload = cfg.model_dump()
        fd, tmp = tempfile.mkstemp(dir=str(self.data_dir), prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.config_path)
            try:
                os.chmod(self.config_path, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
