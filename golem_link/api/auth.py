"""Dashboard authentication: first-run setup, scrypt passwords, signed sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from .. import crypto
from ..config import ConfigManager
from ..db import Database

COOKIE_NAME = "golem_session"


class AuthError(Exception):
    pass


class RateLimited(AuthError):
    pass


class DashboardAuth:
    def __init__(self, cfg: ConfigManager, db: Database) -> None:
        self.cfg = cfg
        self.db = db
        self._key_path = cfg.secrets_dir / "session.key"
        self._key = self._load_key()
        self._failures: dict[str, list[float]] = {}

    def _load_key(self) -> bytes:
        self.cfg.ensure_dirs()
        if self._key_path.exists():
            return self._key_path.read_bytes()
        key = crypto.new_secret(32)
        fd = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        return key

    # -- setup -------------------------------------------------------------
    def needs_setup(self) -> bool:
        return not self.cfg.get().dashboard.admin_password_hash

    def setup(self, node_name: str, password: str) -> dict:
        if not self.needs_setup():
            raise AuthError("setup already completed")
        self._validate_password(password)
        digest, salt = crypto.hash_password(password)

        def mutate(cfg):
            cfg.node_name = node_name.strip()[:64] or cfg.node_name
            cfg.dashboard.admin_password_hash = digest
            cfg.dashboard.admin_password_salt = salt
            cfg.first_run_complete = True

        self.cfg.update(mutate)
        self.db.audit("user", "setup", "initial", "admin password created", None)
        return self.create_session("admin")

    # -- login -------------------------------------------------------------
    def _check_rate(self, ip: str) -> None:
        window = self.cfg.get().dashboard.login_window_s
        max_attempts = self.cfg.get().dashboard.login_max_attempts
        now = time.time()
        history = [t for t in self._failures.get(ip, []) if now - t < window]
        if len(history) >= max_attempts:
            raise RateLimited("too many failed attempts; try again later")
        self._failures[ip] = history

    def _record_failure(self, ip: str) -> None:
        self._failures.setdefault(ip, []).append(time.time())

    def login(self, password: str, ip: str) -> tuple[str, dict]:
        self._check_rate(ip)
        cfg = self.cfg.get()
        if cfg.dashboard.admin_password_hash and crypto.verify_password(
            password, cfg.dashboard.admin_password_hash, cfg.dashboard.admin_password_salt or ""
        ):
            self._failures.pop(ip, None)
            self.db.audit("user", "login", "dashboard", "role=admin", ip)
            return "admin", self.create_session("admin")
        if cfg.dashboard.viewer_password_hash and crypto.verify_password(
            password, cfg.dashboard.viewer_password_hash, cfg.dashboard.viewer_password_salt or ""
        ):
            self._failures.pop(ip, None)
            self.db.audit("user", "login", "dashboard", "role=viewer", ip)
            return "viewer", self.create_session("viewer")
        self._record_failure(ip)
        self.db.audit("user", "login-failed", "dashboard", None, ip)
        raise AuthError("invalid password")

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 8:
            raise AuthError("password must be at least 8 characters")

    def set_viewer_password(self, password: str | None) -> None:
        if password:
            self._validate_password(password)
            digest, salt = crypto.hash_password(password)

            def mutate(cfg):
                cfg.dashboard.viewer_password_hash = digest
                cfg.dashboard.viewer_password_salt = salt
        else:
            def mutate(cfg):
                cfg.dashboard.viewer_password_hash = None
                cfg.dashboard.viewer_password_salt = None
        self.cfg.update(mutate)
        self.db.audit("user", "viewer-password", "dashboard", "enabled" if password else "removed", None)

    def change_admin_password(self, old: str, new: str) -> None:
        cfg = self.cfg.get()
        if not crypto.verify_password(old, cfg.dashboard.admin_password_hash or "", cfg.dashboard.admin_password_salt or ""):
            raise AuthError("current password is incorrect")
        self._validate_password(new)
        digest, salt = crypto.hash_password(new)

        def mutate(c):
            c.dashboard.admin_password_hash = digest
            c.dashboard.admin_password_salt = salt

        self.cfg.update(mutate)
        self.db.audit("user", "password-change", "dashboard", "admin", None)

    # -- sessions ----------------------------------------------------------
    def create_session(self, role: str) -> dict:
        cfg = self.cfg.get()
        payload = {
            "role": role,
            "exp": int(time.time()) + cfg.dashboard.session_ttl_s,
            "csrf": secrets.token_hex(16),
            "nonce": secrets.token_hex(8),
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        sig = hmac.new(self._key, raw, hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + crypto.b64e(sig)
        return {"token": token, **payload}

    def verify_session(self, token: str | None) -> dict | None:
        if not token:
            return None
        try:
            body, sig = token.split(".", 1)
            padded = body + "=" * (-len(body) % 4)
            raw = base64.urlsafe_b64decode(padded)
            expected = hmac.new(self._key, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, crypto.b64d(sig)):
                return None
            payload = json.loads(raw)
            if int(payload.get("exp", 0)) < int(time.time()):
                return None
            return payload
        except Exception:
            return None
