"""AES-256-GCM helpers.

File format: base64(12-byte IV || ciphertext || 16-byte GCM tag). No AAD.
DATA_KEY is the base64 encoding of the 32-byte key.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_WS = re.compile(r"\s+")


class CryptoError(Exception):
    """The key or payload cannot be used."""


def decode_key(key_b64: str) -> bytes:
    try:
        key = base64.b64decode(key_b64.strip(), validate=True)
    except Exception as exc:
        raise CryptoError("DATA_KEY is not valid base64") from exc
    if len(key) != 32:
        raise CryptoError("DATA_KEY must decode to 32 bytes")
    return key


def encrypt_bytes(data: bytes, key_b64: str) -> str:
    key = decode_key(key_b64)
    iv = os.urandom(12)
    token = iv + AESGCM(key).encrypt(iv, data, None)
    encoded = base64.b64encode(token).decode("ascii")
    lines = [encoded[i : i + 76] for i in range(0, len(encoded), 76)]
    return "\n".join(lines) + "\n"


def decrypt_bytes(payload: str, key_b64: str) -> bytes:
    key = decode_key(key_b64)
    compact = _WS.sub("", payload)
    try:
        raw = base64.b64decode(compact, validate=True)
    except Exception as exc:
        raise CryptoError("encrypted file is not valid base64") from exc
    if len(raw) < 12 + 16:
        raise CryptoError("encrypted file is too short")
    iv, ciphertext = raw[:12], raw[12:]
    try:
        return AESGCM(key).decrypt(iv, ciphertext, None)
    except Exception as exc:
        raise CryptoError("decryption failed") from exc


def decrypt_file(path: Path, key_b64: str) -> bytes:
    return decrypt_bytes(path.read_text(encoding="utf-8"), key_b64)
