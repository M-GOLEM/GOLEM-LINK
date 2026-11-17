"""Real hardware monitoring (psutil + optional nvidia-smi). No synthetic data."""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import shutil
import subprocess
import time
from collections import deque
from typing import Any

import psutil

from .config import ConfigManager
from .events import EventBus
from .identity import NodeIdentity

log = logging.getLogger(__name__)

HISTORY_LEN = 300  # 10 minutes at 2 s
TEMP_OK_TTL_S = 60.0
TEMP_FAIL_TTL_S = 900.0
CREATE_NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0

_LHM_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor"
    " | Where-Object { $_.SensorType -eq 'Temperature' -and $_.Value -gt 0 }"
    " | Select-Object Name,Value | ConvertTo-Json -Compress"
)
_ACPI_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature"
    " | Select-Object InstanceName,CurrentTemperature | ConvertTo-Json -Compress"
)


def _run_powershell(script: str, timeout: float = 10.0) -> str:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return ""
    try:
        result = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _json_rows(text: str) -> list[dict]:
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _lhm_temps(text: str) -> list[dict]:
    """LibreHardwareMonitor/OpenHardwareMonitor WMI temperatures (°C)."""
    entries: list[dict] = []
    for row in _json_rows(text):
        try:
            value = float(row.get("Value"))
        except (TypeError, ValueError):
            continue
        if not 0.0 < value < 125.0:
            continue
        entries.append(
            {
                "chip": "librehardwaremonitor",
                "label": str(row.get("Name") or "sensor"),
                "current": round(value, 1),
                "high": None,
                "critical": None,
            }
        )
    return entries


def _acpi_temps(text: str) -> list[dict]:
    """ACPI thermal zones, reported in tenths of a kelvin."""
    entries: list[dict] = []
    for row in _json_rows(text):
        try:
            raw = float(row.get("CurrentTemperature"))
        except (TypeError, ValueError):
            continue
        celsius = raw / 10.0 - 273.15
        if not 10.0 <= celsius <= 125.0:
            continue
        entries.append(
            {
                "chip": "acpi",
                "label": str(row.get("InstanceName") or "ACPI thermal zone"),
                "current": round(celsius, 1),
                "high": None,
                "critical": None,
            }
        )
    return entries


def _is_windows_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _gpu_number(value: str) -> float | None:
    # nvidia-smi emits "[N/A]" (not "N/A") for unsupported fields such as
    # utilisation on a GT 730; a plain float() aborted the whole GPU list.
    text = value.strip().strip("[]").strip()
    if not text or text.lower() in ("n/a", "not supported"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _gpu_bytes(value: str) -> int | None:
    number = _gpu_number(value)
    return int(number * 1024 * 1024) if number is not None else None


def _hardware_monitor_running() -> bool:
    """LibreHardwareMonitor only publishes sensors while its process runs."""
    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if "librehardwaremonitor" in name or "openhardwaremonitor" in name:
                return True
    except Exception:
        pass
    return False


def _probe_windows_temps() -> tuple[list[dict], str | None]:
    # A failing WMI query costs several seconds, so only ask when the source
    # can plausibly answer: LHM running, or elevated rights for ACPI zones.
    if _hardware_monitor_running():
        entries = _lhm_temps(_run_powershell(_LHM_SCRIPT))
        if entries:
            return entries, "librehardwaremonitor"
    if not _is_windows_admin():
        return [], None
    entries = _acpi_temps(_run_powershell(_ACPI_SCRIPT))
    if entries:
        return entries, "acpi"
    return [], None



class ResourceMonitor:
    def __init__(
        self,
        identity: NodeIdentity,
        cfg: ConfigManager,
        bus: EventBus,
        *,
        history_len: int = HISTORY_LEN,
    ) -> None:
        self.identity = identity
        self.cfg = cfg
        self.bus = bus
        self.history: deque[dict] = deque(maxlen=history_len)
        self._prev_disk: dict[str, int] | None = None
        self._prev_net: dict[str, tuple[int, int]] = {}
        self._prev_ts: float | None = None
        self._last_snapshot: dict | None = None
        self._gpu_cache: tuple[float, list[dict]] = (0.0, [])
        self._disk_cache: tuple[float, list[dict]] = (0.0, [])
        self._win_temps: tuple[float, list[dict], str | None] = (0.0, [], None)
        self._top_cache: tuple[float, dict[str, list[dict]]] = (0.0, {"top_cpu": [], "top_mem": []})
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._sampling_slow = False
        self.current_interval = 0.0
        # Prime psutil's CPU counters so the first sample is meaningful.
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

    # -- collection --------------------------------------------------------
    def _disk_usage(self) -> list[dict]:
        now = time.time()
        if now - self._disk_cache[0] < 10 and self._disk_cache[1]:
            return self._disk_cache[1]
        entries: list[dict] = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or part.fstype in ("squashfs", "snap"):
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
                }
            )
        self._disk_cache = (now, entries)
        return entries

    def _gpu_stats(self) -> list[dict]:
        now = time.time()
        if now - self._gpu_cache[0] < (10 if self._gpu_cache[1] else 30):
            return self._gpu_cache[1]
        stats: list[dict] = []
        exe = shutil.which("nvidia-smi")
        if exe:
            try:
                out = subprocess.run(
                    [
                        exe,
                        "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                    creationflags=CREATE_NO_WINDOW,
                )
                for line in out.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 4:
                        continue
                    stats.append(
                        {
                            "name": parts[0],
                            "utilization": _gpu_number(parts[1]),
                            "memory_used": _gpu_bytes(parts[2]),
                            "memory_total": _gpu_bytes(parts[3]),
                            "temperature": _gpu_number(parts[4]) if len(parts) > 4 else None,
                            "power": _gpu_number(parts[5]) if len(parts) > 5 else None,
                        }
                    )
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                log.debug("nvidia-smi query failed: %s", exc)
        self._gpu_cache = (now, stats)
        return stats

    def _top_processes(self, limit: int = 4) -> dict[str, list[dict]]:
        """Biggest CPU/RAM consumers so the operator can see what weighs."""
        now = time.time()
        if now - self._top_cache[0] < 5.0 and self._top_cache[1]["top_cpu"]:
            return self._top_cache[1]
        rows: list[dict] = []
        try:
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    info = proc.info
                    name = info.get("name") or "?"
                    if name in ("System Idle Process", "Idle"):
                        continue
                    cpu = proc.cpu_percent(interval=None)
                    mem = proc.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                    continue
                rows.append(
                    {"pid": int(info["pid"]), "name": name, "cpu": round(cpu, 1), "memory": int(mem)}
                )
        except Exception:
            log.debug("process scan failed", exc_info=True)
            return {"top_cpu": [], "top_mem": []}
        by_cpu = sorted(rows, key=lambda r: r["cpu"], reverse=True)[:limit]
        by_mem = sorted(rows, key=lambda r: r["memory"], reverse=True)[:limit]
        self._top_cache = (now, {"top_cpu": by_cpu, "top_mem": by_mem})
        return self._top_cache[1]

    def _platform_temps(self) -> tuple[list[dict], str | None]:
        sensors: dict[str, Any] = {}
        getter = getattr(psutil, "sensors_temperatures", None)
        if getter is not None:
            try:
                sensors = getter(fahrenheit=False)
            except (OSError, NotImplementedError):
                sensors = {}
            except Exception:  # psutil raises odd platform errors on some hosts
                log.debug("psutil temperature read failed", exc_info=True)
                sensors = {}
        temps: list[dict] = []
        for chip, entries in sensors.items():
            for entry in entries:
                if entry.current is None or entry.current <= 0:
                    continue
                temps.append(
                    {
                        "chip": chip,
                        "label": entry.label or chip,
                        "current": round(float(entry.current), 1),
                        "high": entry.high,
                        "critical": entry.critical,
                    }
                )
        if temps:
            return temps, "psutil"
        if platform.system() != "Windows":
            return [], None

        now = time.time()
        cached_at, cached, source = self._win_temps
        ttl = TEMP_OK_TTL_S if cached else TEMP_FAIL_TTL_S
        if now - cached_at < ttl:
            return cached, source
        # PowerShell is spawned off the event loop (collect runs in a thread)
        # and cached, so a slow or denied WMI query costs at most one call per
        # TTL instead of one per sample.
        entries, source = _probe_windows_temps()
        self._win_temps = (now, entries, source)
        return entries, source

    def collect(self) -> dict:
        now = time.time()
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        cpu_total = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()

        try:
            load = [round(x, 2) for x in psutil.getloadavg()]
        except (AttributeError, OSError):
            load = []

        io = psutil.disk_io_counters()
        disk = {
            "read_bytes": io.read_bytes if io else 0,
            "write_bytes": io.write_bytes if io else 0,
            "read_rate": 0.0,
            "write_rate": 0.0,
        }
        net_io = psutil.net_io_counters(pernic=True)
        net_stats = psutil.net_if_stats()
        interfaces = []
        total_sent = total_recv = 0
        for name, counters in net_io.items():
            if name.lower().startswith(("loopback", "lo")):
                continue
            prev = self._prev_net.get(name)
            send_rate = recv_rate = 0.0
            if prev and self._prev_ts is not None:
                dt = max(now - self._prev_ts, 1e-3)
                send_rate = max(counters.bytes_sent - prev[0], 0) / dt
                recv_rate = max(counters.bytes_recv - prev[1], 0) / dt
            self._prev_net[name] = (counters.bytes_sent, counters.bytes_recv)
            total_sent += counters.bytes_sent
            total_recv += counters.bytes_recv
            interfaces.append(
                {
                    "name": name,
                    "bytes_sent": counters.bytes_sent,
                    "bytes_recv": counters.bytes_recv,
                    "send_rate": round(send_rate, 1),
                    "recv_rate": round(recv_rate, 1),
                    "is_up": net_stats[name].isup if name in net_stats else None,
                }
            )
        if self._prev_disk is not None and self._prev_ts is not None:
            dt = max(now - self._prev_ts, 1e-3)
            disk["read_rate"] = round(max(io.read_bytes - self._prev_disk["read"], 0) / dt, 1) if io else 0.0
            disk["write_rate"] = round(max(io.write_bytes - self._prev_disk["write"], 0) / dt, 1) if io else 0.0
        if io:
            self._prev_disk = {"read": io.read_bytes, "write": io.write_bytes}
        self._prev_ts = now

        temps, temp_source = self._platform_temps()
        temp_note = None
        if not temps:
            temp_note = (
                "Windows does not expose CPU temperature sensors to this process; "
                "GPU temperature is still read from nvidia-smi."
                if platform.system() == "Windows"
                else "This platform reports no temperature sensors."
            )
        freq = psutil.cpu_freq()

        snapshot = {
            "ts": now,
            "node_id": self.identity.node_id,
            "name": self.cfg.get().node_name,
            "os": platform.system(),
            "cpu": {
                "percent": round(cpu_total, 1),
                "per_core": [round(x, 1) for x in per_core],
                "load": load,
                "cores_physical": psutil.cpu_count(logical=False) or 0,
                "cores_logical": psutil.cpu_count(logical=True) or 0,
                "frequency_mhz": freq.current if freq else None,
            },
            "ram": {
                "total": vm.total,
                "used": vm.total - vm.available,
                "available": vm.available,
                "percent": round(vm.percent, 1),
                "swap_total": swap.total,
                "swap_used": swap.used,
                "swap_percent": round(swap.percent, 1),
            },
            "disk": self._disk_usage(),
            "disk_io": disk,
            "net": {
                "bytes_sent": total_sent,
                "bytes_recv": total_recv,
                "send_rate": round(sum(i["send_rate"] for i in interfaces), 1),
                "recv_rate": round(sum(i["recv_rate"] for i in interfaces), 1),
                "interfaces": interfaces,
            },
            "temps": temps,
            "temp_source": temp_source,
            "temp_note": temp_note,
            "gpu": self._gpu_stats(),
            "uptime_s": int(now - psutil.boot_time()),
            "boot_time": psutil.boot_time(),
            "processes": len(psutil.pids()),
            **self._top_processes(),
        }
        self._last_snapshot = snapshot
        return snapshot

    # -- loop --------------------------------------------------------------
    def last(self) -> dict | None:
        return self._last_snapshot

    async def sample_once(self) -> dict:
        snapshot = await asyncio.to_thread(self.collect)
        self.history.append(snapshot)
        self.bus.publish("metrics", snapshot)
        return snapshot

    def _next_interval(self, snapshot: dict | None) -> float:
        """Base cadence, doubled while CPU stays high, with hysteresis.

        Telemetry must not compete with the work that is already loading the
        host; it also must not stay slow after the host cools down.
        """
        t = self.cfg.get().thresholds
        base = max(float(t.sample_interval_s), 0.5)
        if not t.adaptive_sampling or snapshot is None:
            self._sampling_slow = False
            self.current_interval = base
            return base
        cpu = float(snapshot.get("cpu", {}).get("percent", 0.0))
        warn = float(t.cpu_warn_percent)
        if cpu >= warn:
            self._sampling_slow = True
        elif self._sampling_slow and cpu < warn - 10.0:
            self._sampling_slow = False
        if self._sampling_slow:
            cap = max(float(t.max_sample_interval_s), base)
            base = min(cap, max(base * 2.0, base + 1.0))
        self.current_interval = base
        return base

    async def run(self) -> None:
        self._stop.clear()
        log.info("resource monitor started")
        while not self._stop.is_set():
            snapshot = None
            try:
                snapshot = await self.sample_once()
            except Exception:
                log.exception("resource sampling failed")
            interval = self._next_interval(snapshot)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
        log.info("resource monitor stopped")

    def stop(self) -> None:
        self._stop.set()

    def history_payload(self, limit: int = HISTORY_LEN) -> list[dict]:
        return list(self.history)[-limit:]
