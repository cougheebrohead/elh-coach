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


def test_password_hash_uses_pbkdf2():
    # Verifies we're not silently using something weaker
    from auth import hash_password
    h = hash_password("hunter2-secure-passphrase-9!")
    assert h.startswith("pbkdf2_sha256$")
    parts = h.split("$")
    assert len(parts) == 4
    assert int(parts[1]) >= 200_000   # 200k iterations per OWASP


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
