"""Auth — password hashing + bearer-token sessions.

PBKDF2-SHA256, 200k iterations (OWASP 2024 minimum). Constant-time
comparisons throughout. Sessions are SHA-256 of a 32-byte URL-safe random
token; we store only the hash so a DB read can't impersonate.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from db import db

# OWASP 2024 minimum for PBKDF2-SHA256
PBKDF2_ITERATIONS = 200_000
SESSION_TTL_DAYS = 30


def hash_password(plain: str) -> str:
    """Return a self-describing hash string: pbkdf2_sha256$iter$salt$hash"""
    if not plain:
        raise ValueError("password required")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    if not stored or not plain:
        return False
    try:
        algo, iters_s, salt_hex, hash_hex = stored.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    try:
        iters = int(iters_s)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(hash_hex)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, iters)
    return hmac.compare_digest(dk, expected)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(*, user_id: str, tenant_id: str,
                  ip: str | None = None, ua: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    db.execute(
        """insert into sessions (token_hash, user_id, tenant_id, expires_at, ip_hash, user_agent)
           values ($1, $2, $3, $4, $5, $6)""",
        _hash_token(token), user_id, tenant_id, expires.isoformat(),
        hashlib.sha256((ip or "").encode()).hexdigest()[:32] if ip else None,
        (ua or "")[:300],
    )
    return token


def validate_session(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    row = db.fetch_one(
        """select s.user_id, s.tenant_id, s.expires_at, u.role, u.email, u.name
           from sessions s
           join users u on u.id = s.user_id
           where s.token_hash = $1 and s.expires_at > now()""",
        _hash_token(token),
    )
    if not row:
        return None
    # touch last_seen async (best-effort)
    try:
        db.execute("update sessions set last_seen_at = now() where token_hash = $1", _hash_token(token))
    except Exception:
        pass
    return {
        "user_id": row["user_id"],
        "tenant_id": row["tenant_id"],
        "role": row["role"],
        "email": row["email"],
        "name": row["name"],
    }


def revoke_session(token: str) -> None:
    if not token:
        return
    db.execute("delete from sessions where token_hash = $1", _hash_token(token))
