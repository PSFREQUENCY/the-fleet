# ── cipher.py — AES-256-GCM privacy layer ────────────────────────────────────
import os, hmac, hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes


def derive_key(master_hex: str, salt: bytes = b"fleet-v1") -> bytes:
    raw = bytes.fromhex(master_hex)
    return HKDF(hashes.SHA256(), 32, salt, b"fleet-cipher").derive(raw)


def encrypt(key: bytes, data: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, data, None)


def decrypt(key: bytes, blob: bytes) -> bytes:
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


def sign(key: bytes, msg: bytes) -> str:
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify(key: bytes, msg: bytes, sig: str) -> bool:
    expected = sign(key, msg)
    return hmac.compare_digest(expected, sig)
