"""Tenant resolver + plan limits.

Resolves the requesting Host header into a tenant row. Caches in-process
for 60s — tenant config changes are rare (hours, not seconds), and the
cache invalidates on next request after TTL expires.
"""

from __future__ import annotations

import os
import time
from typing import Any

from db import db

APEX_HOST = os.environ.get("APEX_HOST", "elhcoachhq.app")

_CACHE: dict[str, tuple[float, dict | None]] = {}
_TTL_SEC = 60


def tenant_resolver(host: str) -> dict[str, Any] | None:
    """Look up tenant by Host header.

    Resolution order:
      1. <slug>.<APEX_HOST>      → tenant by slug
      2. <custom_domain>         → tenant by exact custom_domain
      3. localhost / local IPs   → demo-coach tenant if it exists
      4. APEX_HOST itself        → None (marketing site)
    """
    host = (host or "").lower().split(":")[0]
    if not host:
        return None

    now = time.time()
    cached = _CACHE.get(host)
    if cached and cached[0] > now:
        return cached[1]

    tenant: dict | None = None

    if host == APEX_HOST or host == f"www.{APEX_HOST}":
        # Marketing site — no tenant
        tenant = None
    elif host.endswith(f".{APEX_HOST}"):
        slug = host[: -(len(APEX_HOST) + 1)]
        tenant = db.fetch_one(
            "select * from tenants where slug = $1 and billing_status != 'canceled'",
            slug,
        )
    else:
        # Custom domain
        tenant = db.fetch_one(
            "select * from tenants where custom_domain = $1 and billing_status != 'canceled'",
            host,
        )

    # Localhost dev fallback
    if not tenant and host in ("localhost", "127.0.0.1", "0.0.0.0"):
        tenant = db.fetch_one(
            "select * from tenants where slug = 'demo-coach' limit 1"
        )

    _CACHE[host] = (now + _TTL_SEC, tenant)
    return tenant


def invalidate_tenant_cache(host: str | None = None) -> None:
    """Drop cached tenant for a host (or all)."""
    if host is None:
        _CACHE.clear()
    else:
        _CACHE.pop(host.lower(), None)


def plan_limits(plan: str) -> dict[str, int]:
    """Seat caps per plan. Truth lives here, mirrored to tenants table on
    upgrade so the UI can show the cap without a code lookup."""
    return {
        "coach":  {"max_coaches": 1, "max_clients": 25},
        "studio": {"max_coaches": 5, "max_clients": 200},
        "brand":  {"max_coaches": 100_000, "max_clients": 100_000},  # de-facto unlimited
    }.get(plan, {"max_coaches": 1, "max_clients": 25})


def brand_default() -> dict[str, str]:
    """Default brand colors for new tenants — neutral SaaS palette they
    can override in settings. Linear/Stripe-grade restraint."""
    return {
        "primary": "#0F172A",   # slate-900
        "accent":  "#22C55E",   # green-500
    }
