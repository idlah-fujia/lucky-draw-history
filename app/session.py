"""HMAC-signed cookies for the admin session and the staff phone lookup."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

logger = logging.getLogger(__name__)

SESSION_COOKIE = "ldh_session"
PHONE_COOKIE = "ldh_phone"
MAX_AGE = 12 * 60 * 60

_ephemeral = ""


def signing_secret() -> str:
    global _ephemeral
    configured = os.environ.get("SESSION_SECRET", "").strip()
    if configured:
        return configured
    if not _ephemeral:
        _ephemeral = secrets.token_hex(32)
        logger.warning("SESSION_SECRET is not set; using an ephemeral signing key")
    return _ephemeral


def passwords_match(given: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(
        hashlib.sha256(given.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def _sign(body: bytes) -> str:
    payload = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    digest = hmac.new(signing_secret().encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{digest}"


def _unsign(token: str) -> bytes | None:
    if not token or "." not in token:
        return None
    payload, digest = token.rsplit(".", 1)
    expected = hmac.new(signing_secret().encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, expected):
        return None
    padded = payload + "=" * (-len(payload) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception:
        return None


def issue_admin_token() -> str:
    body = f"admin:{int(time.time()) + MAX_AGE}".encode("ascii")
    return _sign(body)


def admin_token_ok(token: str | None) -> bool:
    raw = _unsign(token or "")
    if raw is None:
        return False
    try:
        role, expiry = raw.decode("ascii").split(":", 1)
    except ValueError:
        return False
    if role != "admin":
        return False
    try:
        return int(expiry) >= int(time.time())
    except ValueError:
        return False


def issue_phone_token(number: str) -> str:
    body = f"phone:{number}:{int(time.time()) + MAX_AGE}".encode("ascii")
    return _sign(body)


def read_phone_token(token: str | None) -> str | None:
    raw = _unsign(token or "")
    if raw is None:
        return None
    try:
        kind, number, expiry = raw.decode("ascii").split(":", 2)
    except ValueError:
        return None
    if kind != "phone" or not number.isdigit() or len(number) != 8:
        return None
    try:
        if int(expiry) < int(time.time()):
            return None
    except ValueError:
        return None
    return number
