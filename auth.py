"""Auth — Argon2id passwords + lockout-aware verify + bearer-token sessions.

Iron Dome I-2 parity: pulls password hashing + lockout from
`fitapp_core.security` (the estate-shared module). Legacy PBKDF2
hashes ("pbkdf2_sha256$..." format) keep working through the lazy
migration window: on a successful PBKDF2 verify we re-hash with
Argon2id and write back so the next login uses the modern algorithm.

Sessions are SHA-256 of a 32-byte URL-safe random token; we store
only the hash so a DB read can't impersonate. 30-day TTL.

Login lockout: 10 consecutive fails -> 15 min lock, doubling per
repeat burst (capped at 2 hr). See `record_login_failure` +
`clear_login_failures`.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fitapp_core.security import (
    hash_password as _argon2_hash,
    verify_password as _verify_any,
    needs_rehash as _needs_rehash,
)
from fitapp_core.security.ratelimit import lockout_status

from db import db


SESSION_TTL_DAYS = 30

# Lockout policy — same defaults as fitapp_core.security.ratelimit.lockout_status
LOCKOUT_THRESHOLD = 10           # fails before lock
LOCKOUT_BASE_S = 15 * 60         # 15 min initial lock
LOCKOUT_CAP_S = 120 * 60         # 2 hr max


def hash_password(plain: str) -> str:
    """Hash a new password with Argon2id (PHC string)."""
    if not plain:
        raise ValueError("password required")
    return _argon2_hash(plain)


def verify_password(plain: str, stored: str) -> bool:
    """Verify against Argon2id or legacy PBKDF2. Returns False on
    malformed input, never raises on user content."""
    return _verify_any(stored, plain)


def hash_needs_upgrade(stored: str) -> bool:
    """Return True if `stored` is a legacy hash that should be upgraded
    to Argon2id after a successful verify."""
    return _needs_rehash(stored)


# ── per-account login lockout ───────────────────────────────────────

def get_lockout_state(user_id: str) -> tuple[bool, int]:
    """Returns (locked, seconds_remaining) for the given user.

    A non-existent row means no failures yet -> not locked.
    """
    row = db.fetch_one(
        "select fail_count, last_fail_at from user_login_failures where user_id = $1",
        user_id,
    )
    if not row or not row.get("last_fail_at"):
        return False, 0
    last = row["last_fail_at"]
    if isinstance(last, str):
        last_ts = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
    else:
        last_ts = last.timestamp()
    return lockout_status(
        now=datetime.now(timezone.utc).timestamp(),
        failure_count=int(row.get("fail_count") or 0),
        last_failure=last_ts,
        threshold=LOCKOUT_THRESHOLD,
        base_lock_s=LOCKOUT_BASE_S,
        cap_lock_s=LOCKOUT_CAP_S,
    )


def record_login_failure(user_id: str) -> None:
    """Increment the per-account failure counter. UPSERT on user_id pk."""
    db.execute(
        """insert into user_login_failures (user_id, fail_count, last_fail_at)
           values ($1, 1, now())
           on conflict (user_id) do update
             set fail_count = user_login_failures.fail_count + 1,
                 last_fail_at = now()""",
        user_id,
    )


def clear_login_failures(user_id: str) -> None:
    """Zero out the per-account failure counter on a successful login."""
    db.execute(
        """insert into user_login_failures (user_id, fail_count, last_fail_at, locked_until)
           values ($1, 0, null, null)
           on conflict (user_id) do update
             set fail_count = 0, last_fail_at = null, locked_until = null""",
        user_id,
    )


# ── sessions ────────────────────────────────────────────────────────

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
    try:
        db.execute("update sessions set last_seen_at = now() where token_hash = $1",
                   _hash_token(token))
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


def revoke_all_user_sessions(user_id: str) -> None:
    """Used on password change to invalidate every active session for a user."""
    db.execute("delete from sessions where user_id = $1", user_id)
