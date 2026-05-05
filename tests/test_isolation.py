"""Cross-tenant isolation tests.

These run against a ELH Coach Postgres instance with the migrations applied.
If SUPABASE_URL + SUPABASE_SERVICE_KEY (or ELHCOACH_TEST_DATABASE_URL) is
not set, they skip — but on CI against the staging DB they MUST pass.

The contract: under no circumstances may a session for tenant A see any
row tied to tenant B. We verify by:
  1. Using the app_query RPC with tenant_id=A to fetch users where
     tenant_id=B and asserting an empty set.
  2. Attempting an UPDATE that names a tenant-B row and confirming
     zero rows affected (RLS denies the row).
"""
from __future__ import annotations

import os
import uuid
import pytest


pytestmark = pytest.mark.skipif(
    not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY")),
    reason="Supabase test env not configured",
)


def _make_tenant(db, slug: str, name: str) -> str:
    rows = db.fetch_all(
        "insert into tenants (slug, name) values ($1, $2) returning id",
        slug, name,
    )
    return rows[0]["id"]


def _make_user(db, tenant_id: str, email: str, role: str) -> str:
    rows = db.fetch_all(
        """insert into users (tenant_id, email, password_hash, role, name)
           values ($1, $2, 'x', $3, 'Test') returning id""",
        tenant_id, email, role,
    )
    return rows[0]["id"]


def test_tenant_a_cannot_see_tenant_b_users():
    from db import db
    a = _make_tenant(db, f"iso-a-{uuid.uuid4().hex[:6]}", "A")
    b = _make_tenant(db, f"iso-b-{uuid.uuid4().hex[:6]}", "B")
    _make_user(db, b, f"hidden-{uuid.uuid4().hex[:6]}@b.com", "client")

    # Issue a query as tenant A and try to list tenant B's users
    leaked = db.fetch_all(
        "select id from users where tenant_id = $1",
        b,
        tenant_id=a,            # this becomes app.tenant_id GUC
    )
    assert leaked == [], f"Tenant A leaked tenant B users: {leaked}"


def test_tenant_a_cannot_update_tenant_b_meal():
    from db import db
    a = _make_tenant(db, f"iso-c-{uuid.uuid4().hex[:6]}", "A")
    b = _make_tenant(db, f"iso-d-{uuid.uuid4().hex[:6]}", "B")
    cb = _make_user(db, b, f"client-{uuid.uuid4().hex[:6]}@b.com", "client")

    # Create a meal for tenant B
    rows = db.fetch_all(
        """insert into meals (tenant_id, client_id, log_date, items_json, totals_json)
           values ($1, $2, current_date, '[]'::jsonb, '{}'::jsonb) returning id""",
        b, cb,
    )
    meal_id = rows[0]["id"]

    # Now masquerade as tenant A and try to update tenant B's meal
    affected = db.execute(
        "update meals set source = 'manual' where id = $1",
        meal_id,
        tenant_id=a,
    )
    assert affected == 0, "Tenant A was able to update tenant B's meal"
