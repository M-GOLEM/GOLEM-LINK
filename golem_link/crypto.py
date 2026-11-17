"""Cryptographic primitives used across GOLEM LINK.

Design notes
------------
* Nodes have a long-lived Ed25519 identity key. The public key is the node's
  cryptographic identity; the TLS certificate is a separate, self-signed
  certificate that peers pin after pairing.
* Pairing performs an ephemeral X25519 exchange. Both sides derive the same
  6-digit code from the shared secret and display it, so the humans involved
  can compare it out-of-band. The same secret becomes the pairing key that
  authenticates later API calls over TLS.
* All peer traffic runs over TLS with pinned certificates; the HMAC token is a
  second, application-level check that a request comes from the paired peer.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hkdf(secret: bytes, info: bytes, length: int = 32, salt: bytes | None = None) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(secret)


# -- Ed25519 ----------------------------------------------------------------

def generate_ed25519() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def sign(private_key: Ed25519PrivateKey, data: bytes) -> bytes:
    return private_key.sign(data)


def verify(public_key: Ed25519PublicKey, signature: bytes, data: bytes) -> bool:
    try:
        public_key.verify(signature, data)
        return True
    except Exception:
        return False


def pub_raw(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def pub_pem(private_key: Ed25519PrivateKey) -> str:
    return private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


def load_pub_pem(text: str) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(text.encode("ascii"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("not an Ed25519 public key")
    return key


def fingerprint(public_key_pem: str) -> str:
    """Short human-verifiable fingerprint of a public key."""
    raw = load_pub_pem(public_key_pem).public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    digest = hashlib.sha256(raw).digest()[:8]
    return ":".join(f"{b:02x}" for b in digest)


# -- X25519 pairing ---------------------------------------------------------

def generate_x25519() -> tuple[X25519PrivateKey, bytes]:
    key = X25519PrivateKey.generate()
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return key, raw


def ecdh(private_key: X25519PrivateKey, peer_public_raw: bytes) -> bytes:
    peer = X25519PublicKey.from_public_bytes(peer_public_raw)
    return private_key.exchange(peer)


def pairing_key(shared: bytes, initiator_id: str, responder_id: str) -> bytes:
    return hkdf(
        shared,
        info=f"golem-link-pairing-v1|{initiator_id}|{responder_id}".encode(),
        length=32,
    )


def pairing_code(key: bytes) -> str:
    """Six-digit code derived from the pairing key (displayed on both sides)."""
    digest = hkdf(key, info=b"golem-link-pairing-code", length=8)
    return f"{int.from_bytes(digest, 'big') % 1_000_000:06d}"


# -- Request tokens ---------------------------------------------------------

@dataclass(frozen=True)
class TokenParts:
    expires: int
    signature: str


def make_token(secret: bytes, subject: str, audience: str, ttl_s: int = 600) -> str:
    expires = int(time.time()) + ttl_s
    msg = f"{subject}|{audience}|{expires}".encode()
    sig = hmac.new(secret, msg, hashlib.sha256).digest()
    return f"{expires}.{b64e(sig)}"


def verify_token(secret: bytes, token: str, subject: str, audience: str) -> bool:
    try:
        exp_s, sig_s = token.split(".", 1)
        expires = int(exp_s)
        if expires < int(time.time()) - 5:
            return False
        msg = f"{subject}|{audience}|{expires}".encode()
        expected = hmac.new(secret, msg, hashlib.sha256).digest()
        return hmac.compare_digest(expected, b64d(sig_s))
    except Exception:
        return False


# -- Passwords --------------------------------------------------------------

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
        maxmem=SCRYPT_N * SCRYPT_R * 128 * 2,
    )
    return digest.hex(), salt.hex()


def verify_password(password: str, password_hash: str, salt_hex: str) -> bool:
    try:
        candidate, _ = hash_password(password, salt_hex)
        return hmac.compare_digest(candidate, password_hash)
    except Exception:
        return False


def new_secret(length: int = 32) -> bytes:
    return secrets.token_bytes(length)


def new_share_token() -> str:
    return secrets.token_urlsafe(24)
