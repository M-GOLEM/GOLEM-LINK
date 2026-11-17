"""Headless pairing driver for installers.

Talks to the agent that is already running on this machine through its
dashboard API. The six-digit code is still compared by a human: ``init``
prints it, and ``approve`` refuses to approve a request unless the operator
typed the same code.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from typing import Any, Callable, Iterable


class LinkError(RuntimeError):
    pass


class LocalDashboard:
    def __init__(self, port: int = 8080, timeout: float = 10.0) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.timeout = timeout
        self.csrf = ""
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def _call(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        csrf: bool = False,
        timeout: float | None = None,
    ) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if csrf:
            request.add_header("X-CSRF-Token", self.csrf)
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except (ValueError, AttributeError):
                pass
            raise LinkError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise LinkError(f"cannot reach the local agent at {self.base}: {reason}") from exc
        try:
            return json.loads(body or b"{}")
        except ValueError as exc:
            raise LinkError(f"{method} {path} returned invalid JSON") from exc

    def login(self, password: str) -> None:
        data = self._call("POST", "/api/auth/login", {"password": password})
        self.csrf = str(data.get("csrf") or "")
        if not self.csrf:
            raise LinkError("login succeeded but no CSRF token was returned")

    def begin(self, peer: str) -> dict:
        return self._call("POST", "/api/pairing/begin", {"address": peer}, csrf=True)

    def poll(self, request_id: str) -> dict:
        return self._call("GET", f"/api/pairing/poll/{request_id}")

    def requests(self) -> list[dict]:
        data = self._call("GET", "/api/pairing/requests")
        return list(data.get("requests") or [])

    def approve(self, request_id: str) -> dict:
        return self._call(
            "POST", f"/api/pairing/requests/{request_id}", {"approve": True}, csrf=True
        )

    def devices(self) -> list[dict]:
        data = self._call("GET", "/api/devices")
        return list(data.get("peers") or [])

    def discovered(self) -> list[dict]:
        data = self._call("GET", "/api/pairing/discovered")
        return list(data.get("discovered") or [])

    # -- updates / maintenance --------------------------------------------
    def me(self) -> dict:
        return self._call("GET", "/api/auth/me")

    def update_status(self) -> dict:
        return self._call("GET", "/api/update")

    def update_check(self) -> dict:
        return self._call("POST", "/api/update/check", csrf=True, timeout=60)

    def update_download(self) -> dict:
        return self._call("POST", "/api/update/download", csrf=True, timeout=600)

    def update_apply(self) -> dict:
        return self._call(
            "POST", "/api/update/apply", {"confirm": True}, csrf=True, timeout=600
        )

    def update_fleet_status(self) -> dict:
        return self._call("GET", "/api/update/fleet", timeout=30)

    def update_fleet(self, action: str) -> dict:
        return self._call(
            "POST", "/api/update/fleet", {"action": action, "confirm": True},
            csrf=True, timeout=600,
        )

    def control_device(self, node_id: str, action: str, confirm: bool = True) -> dict:
        return self._call(
            "POST",
            f"/api/devices/{node_id}/control",
            {"action": action, "confirm": confirm},
            csrf=True,
            timeout=600,
        )


def find_request(requests: Iterable[dict], code: str, now: float | None = None) -> dict:
    wanted = str(code).strip()
    if not wanted:
        raise LinkError("a six-digit pairing code is required")
    moment = time.time() if now is None else now
    matches = [
        row
        for row in requests
        if str(row.get("code") or "") == wanted and float(row.get("expires_at") or 0) > moment
    ]
    if not matches:
        raise LinkError("no pending pairing request matches that code")
    if len(matches) > 1:
        raise LinkError("more than one pending request matches that code; approve from the dashboard")
    return matches[0]


def wait_online(
    client: LocalDashboard,
    node_id: str,
    timeout: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for peer in client.devices():
            if peer.get("node_id") == node_id and peer.get("status") == "online":
                return True
        sleep(1.5)
    return False


def run_init(
    client: LocalDashboard,
    peer: str,
    wait_s: float,
    *,
    online_wait_s: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> dict:
    started = client.begin(peer)
    code = str(started.get("code") or "")
    name = started.get("name") or started.get("node_id") or peer
    out(f"Pairing request sent to {name} ({started.get('address') or peer}).")
    out(f"PAIRING CODE: {code}")
    out("Confirm this code matches on the other machine, then approve the request there.")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        state = str(client.poll(started["request_id"]).get("status") or "pending")
        if state == "approved":
            node_id = str(started.get("node_id") or "")
            out(f"Pairing approved: {name} is now pinned.")
            if node_id and wait_online(client, node_id, online_wait_s, sleep=sleep):
                out(f"Link is online: {name} ({node_id}).")
            else:
                out(
                    "Pairing is saved, but the peer is not online yet. Check the LAN, "
                    "the Private-profile firewall on Windows, and port 8443."
                )
            return started
        if state in ("denied", "expired"):
            raise LinkError(f"pairing {state}")
        sleep(1.5)
    raise LinkError("timed out waiting for the other machine to approve the request")


def run_approve(
    client: LocalDashboard,
    code: str,
    wait_s: float,
    *,
    online_wait_s: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> dict:
    request = find_request(client.requests(), code)
    out(
        f"Approving {request.get('name') or request.get('node_id')} "
        f"from {request.get('address')}:{request.get('peer_port') or ''} "
        f"(fingerprint {request.get('fingerprint') or 'unknown'})."
    )
    client.approve(str(request["id"]))
    node_id = str(request.get("node_id") or "")
    out("Pairing approved. Waiting for the encrypted link to come online...")
    deadline = time.monotonic() + min(wait_s, online_wait_s)
    while time.monotonic() < deadline:
        for peer in client.devices():
            if peer.get("node_id") == node_id and peer.get("status") == "online":
                out(f"Link is online: {peer.get('name') or node_id} ({node_id}).")
                return request
        sleep(1.5)
    out(
        "Pairing is saved, but the peer is not online yet. Check the LAN, the "
        "Private-profile firewall, and port 8443."
    )
    return request


def discover(
    client: LocalDashboard,
    wait_s: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Collect unpaired peers the local agent saw over mDNS/beacon.

    Keeps listening for the whole window (or until two peers answer, which is
    already ambiguous) so a single-machine LAN settles on one candidate.
    """
    found: dict[str, dict] = {}
    deadline = time.monotonic() + max(wait_s, 0.0)
    while True:
        for row in client.discovered():
            if row.get("paired"):
                continue
            node_id = str(row.get("node_id") or "")
            address = str(row.get("address") or "")
            if node_id and address:
                found[node_id] = row
        if len(found) >= 2 or time.monotonic() >= deadline:
            break
        sleep(1.0)
    return sorted(
        found.values(),
        key=lambda row: (str(row.get("name") or ""), str(row.get("node_id") or "")),
    )


def _endpoint(peer: dict) -> str:
    address = str(peer.get("address") or "")
    port = peer.get("port")
    return f"{address}:{port}" if address and port else address


def run_auto(
    client: LocalDashboard,
    wait_s: float,
    *,
    discovery_s: float = 12.0,
    online_wait_s: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> dict | None:
    """Find the only other GOLEM LINK machine and finish the pairing.

    If that machine already sent us a request, its code is shown and the
    approval stays a human click in the dashboard; otherwise this side starts
    the request. The six-digit code is never approved without a human.
    """
    out("Looking for another GOLEM LINK machine on the local network...")
    candidates = discover(client, discovery_s, sleep=sleep)
    if not candidates:
        out(
            "No other machine answered yet. Start the installer on the other "
            "machine and it will pair with this one, or pair from the dashboard."
        )
        return None
    if len(candidates) > 1:
        names = ", ".join(
            f"{c.get('name') or c.get('node_id')} ({c.get('address')})" for c in candidates
        )
        out(
            f"More than one machine answered ({names}); pair with the one you "
            "want from the dashboard so the code is compared on both screens."
        )
        return None
    peer = candidates[0]
    node_id = str(peer.get("node_id") or "")
    name = str(peer.get("name") or node_id)
    moment = time.time()
    incoming = [
        row
        for row in client.requests()
        if str(row.get("node_id") or "") == node_id
        and float(row.get("expires_at") or 0) > moment
    ]
    if incoming:
        request = incoming[0]
        out(f"{name} is already asking to pair.")
        out(f"PAIRING CODE: {request.get('code')}")
        out(
            "Compare this code on both screens, then approve the request in the "
            f"dashboard (http://127.0.0.1:8080 > Devices)."
        )
        if wait_online(client, node_id, wait_s, sleep=sleep):
            out(f"Link is online: {name} ({node_id}).")
        else:
            out("The request is still waiting; approve it in the dashboard to finish.")
        return request
    out(f"Found {name} at {_endpoint(peer)}.")
    return run_init(
        client,
        _endpoint(peer),
        wait_s,
        online_wait_s=online_wait_s,
        sleep=sleep,
        out=out,
    )


def wait_for_service(port: int, timeout: float, *, sleep: Callable[[float], None] = time.sleep) -> None:
    client = LocalDashboard(port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            client._call("GET", "/api/setup/status")
            return
        except LinkError:
            sleep(1.0)
    raise LinkError(f"the agent did not answer on 127.0.0.1:{port} within {int(timeout)} s")
