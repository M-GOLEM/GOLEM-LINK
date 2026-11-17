"""Persistent node identity: Ed25519 key, node id and self-signed TLS cert."""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import platform
import socket
import uuid
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from . import crypto
from .config import ConfigManager
from .utils import get_lan_ips
from .version import PACKAGE_NAME, VERSION


class NodeIdentity:
    def __init__(self, cfg: ConfigManager) -> None:
        self.cfg = cfg
        self.cfg.ensure_dirs()
        self.node_id = self._load_or_create_node_id()
        self.private_key = self._load_or_create_identity_key()
        self.public_pem = crypto.pub_pem(self.private_key)
        self.fingerprint = crypto.fingerprint(self.public_pem)
        self._ensure_tls_cert()

    # -- files -------------------------------------------------------------
    def _node_id_path(self) -> Path:
        return self.cfg.secrets_dir / "node_id"

    def _load_or_create_node_id(self) -> str:
        path = self._node_id_path()
        if path.exists():
            value = path.read_text(encoding="ascii").strip()
            if value:
                return value
        value = f"gl-{uuid.uuid4().hex[:20]}"
        self._write_private(path, value.encode("ascii"))
        return value

    def _load_or_create_identity_key(self):
        path = self.cfg.identity_path()
        if path.exists():
            data = path.read_bytes()
            key = serialization.load_pem_private_key(data, password=None)
            return key
        key = crypto.generate_ed25519()
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self._write_private(path, pem)
        return key

    @staticmethod
    def _write_private(path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    # -- TLS ---------------------------------------------------------------
    def _ensure_tls_cert(self) -> None:
        cert_path = self.cfg.cert_path()
        key_path = self.cfg.key_path()
        if cert_path.exists() and key_path.exists():
            return
        key = ec.generate_private_key(ec.SECP256R1())
        hostname = socket.gethostname() or "golem-node"
        dns_names = {hostname, f"{hostname}.local", "golem-link.local", "localhost"}
        ips = {"127.0.0.1"} | set(get_lan_ips())
        san: list[x509.GeneralName] = [x509.DNSName(n) for n in sorted(dns_names)]
        san += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in sorted(ips)]
        now = dt.datetime.now(dt.timezone.utc)
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "GOLEM LINK"),
                x509.NameAttribute(NameOID.COMMON_NAME, f"golem-link/{self.node_id}"),
            ]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        self._write_private(
            key_path,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        try:
            os.chmod(cert_path, 0o644)
        except OSError:
            pass

    @property
    def cert_pem(self) -> str:
        return self.cfg.cert_path().read_text(encoding="ascii")

    # -- info --------------------------------------------------------------
    def sign(self, data: bytes) -> str:
        return crypto.b64e(crypto.sign(self.private_key, data))

    def info(self) -> dict:
        cfg = self.cfg.get()
        import psutil

        try:
            cpu_model = platform.processor() or ""
            if not cpu_model and platform.system() == "Linux":
                with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        if line.lower().startswith("model name"):
                            cpu_model = line.split(":", 1)[1].strip()
                            break
        except Exception:
            cpu_model = ""
        return {
            "node_id": self.node_id,
            "name": cfg.node_name,
            "os": platform.system(),
            "os_version": platform.version(),
            "os_release": platform.release(),
            "arch": platform.machine(),
            "hostname": socket.gethostname(),
            "cpu_model": cpu_model,
            "cpu_cores_physical": psutil.cpu_count(logical=False) or 0,
            "cpu_cores_logical": psutil.cpu_count(logical=True) or 0,
            "python": platform.python_version(),
            "app": PACKAGE_NAME,
            "version": VERSION,
            "public_key": self.public_pem,
            "fingerprint": self.fingerprint,
            "certificate": self.cert_pem,
            "addresses": get_lan_ips(),
            "dashboard_port": cfg.dashboard.port,
            "peer_port": cfg.peer.port,
        }
