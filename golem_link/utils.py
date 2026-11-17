"""Small shared helpers."""

from __future__ import annotations

import ipaddress
import socket
import time
from pathlib import Path


def utcnow_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def epoch() -> int:
    return int(time.time())


def get_lan_ips() -> list[str]:
    """Best-effort list of usable IPv4 LAN addresses of this host."""
    ips: set[str] = set()
    try:
        import psutil

        for _name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and a.address:
                    try:
                        ip = ipaddress.IPv4Address(a.address)
                    except ipaddress.AddressValueError:
                        continue
                    if ip.is_loopback or ip.is_link_local:
                        continue
                    ips.add(str(ip))
    except Exception:
        # psutil is a hard dependency, but never let address enumeration
        # break the agent.
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                ips.add(s.getsockname()[0])
            finally:
                s.close()
        except OSError:
            ips.add("127.0.0.1")
    return sorted(ips)


def get_broadcast_addresses() -> list[str]:
    """Broadcast addresses for each IPv4 interface subnet."""
    out: set[str] = set()
    try:
        import psutil

        for _name, addrs in psutil.net_if_addrs().items():
            v4 = [a for a in addrs if a.family == socket.AF_INET]
            for a in v4:
                if not a.address or not a.netmask:
                    continue
                try:
                    iface = ipaddress.IPv4Interface(f"{a.address}/{a.netmask}")
                except ValueError:
                    continue
                if iface.ip.is_loopback or iface.ip.is_link_local:
                    continue
                out.add(str(iface.network.broadcast_address))
    except Exception:
        pass
    return sorted(out)


def human_bytes(n: float | int | None) -> str:
    if n is None:
        return "-"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PiB"


def is_lan_address(host: str) -> bool:
    """True when ``host`` is a private/loopback address or an mDNS name."""
    host = host.strip().lower().rstrip(".")
    if host.endswith(".local") or host in {"localhost", "golem-link.local"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip in ipaddress.ip_network("100.64.0.0/10")
    )


def safe_resolve(path: Path, root: Path) -> Path:
    """Resolve ``path`` and require it to stay inside ``root`` (symlink safe)."""
    real_root = root.resolve()
    candidate = (real_root / path).resolve() if not path.is_absolute() else path.resolve()
    if candidate != real_root and real_root not in candidate.parents:
        raise ValueError("path escapes the shared root")
    return candidate
