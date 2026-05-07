"""Smoke tests that run without DB — sanity-check imports + pure functions."""
from __future__ import annotations

import importlib


def test_modules_import():
    for mod in ("server", "db", "tenants", "auth", "billing", "ratelimit"):
        importlib.import_module(mod)


def test_password_roundtrip():
    from auth import hash_password, verify_password
    h = hash_password("Correct-Horse-Battery-Staple-1!")
    assert verify_password("Correct-Horse-Battery-Staple-1!", h)
    assert not verify_password("wrong-password", h)


def test_password_hash_uses_argon2id():
    """Iron Dome I-2: new hashes are Argon2id (PHC string)."""
    from auth import hash_password
    h = hash_password("hunter2-secure-passphrase-9!")
    assert h.startswith("$argon2id$"), f"expected Argon2id, got {h[:20]}"


def test_legacy_pbkdf2_hashes_still_verify():
    """Lazy-migration: existing pbkdf2_sha256$ hashes from before the
    Iron Dome upgrade must continue to verify so users keep their
    accounts. The login handler re-hashes them on next successful
    login."""
    import hashlib
    from auth import verify_password, hash_needs_upgrade
    plain = "legacy-Correct-Horse-Battery-Staple-1!"
    salt = bytes(range(16))
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, 200_000)
    legacy = f"pbkdf2_sha256$200000${salt.hex()}${dk.hex()}"
    assert verify_password(plain, legacy)
    assert not verify_password("wrong", legacy)
    assert hash_needs_upgrade(legacy)


def test_argon2_no_rehash_needed():
    from auth import hash_password, hash_needs_upgrade
    h = hash_password("x")
    assert not hash_needs_upgrade(h)


def test_lockout_state_clean_user():
    """A user with no entry in user_login_failures is not locked.
    DB-touching but tolerant of missing connection — skipped if so."""
    import os
    if not os.environ.get("SUPABASE_URL"):
        import pytest
        pytest.skip("no DB connection in this CI job")
    from auth import get_lockout_state
    locked, retry = get_lockout_state("00000000-0000-0000-0000-000000000000")
    assert locked is False
    assert retry == 0


def test_plan_limits_enforced():
    from tenants import plan_limits
    coach = plan_limits("coach")
    studio = plan_limits("studio")
    brand = plan_limits("brand")
    assert coach["max_clients"] == 25
    assert studio["max_clients"] == 200
    assert brand["max_clients"] >= 100_000  # effectively unlimited


def test_rate_limit_allows_then_blocks():
    from ratelimit import allow
    key = f"test-{__name__}-{id(object())}"
    for _ in range(3):
        assert allow(key, 3, 60)
    assert not allow(key, 3, 60)
