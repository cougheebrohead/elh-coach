"""ELH Coach sales-demo tenant provisioner.

Mirrors the ELH Health provisioner but for the trainer SaaS:
  - Creates a `tenants` row (not orgs) with is_demo=true
  - Seeds a believable solo-coach roster: 1 owner-coach + ~12 clients
  - Returns a /demo/<slug> URL gated by demo_password

The coach demo target is way smaller than the gym one — solo trainers
expect to see "their" 8-15 clients, not 240 members. We seed exactly
enough to make the roster + chat + meal log feel real.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from auth import hash_password
from db import db


DEMO_TTL_DAYS = 30
SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Slug must satisfy ^[a-z0-9-]{2,40}$ on this product."""
    s = SLUG_RE.sub("-", (name or "").lower()).strip("-")
    return s[:32] if len(s) >= 2 else "demo"


def _unique_slug(base: str) -> str:
    suffix = secrets.token_urlsafe(4).lower().replace("_", "").replace("-", "")[:6]
    candidate = f"{base[:32]}-{suffix}"
    return candidate[:40]


def _safe_color(hex_str: str | None, fallback: str) -> str:
    if not hex_str:
        return fallback
    s = (hex_str or "").strip().lower()
    if re.match(r"^#[0-9a-f]{6}$", s):
        return s
    return fallback


def _gen_demo_password() -> str:
    words = (
        "iron tide flame orca echo north quartz harbor "
        "vector summit beacon vault ridge cipher prism luna "
        "atlas pulse zenith rally drift cascade keel mosaic"
    ).split()
    pick = "-".join(secrets.choice(words) for _ in range(4))
    num = secrets.randbelow(9000) + 1000
    return f"{pick}-{num}"


_FIRST = (
    "Avery Riley Jordan Casey Morgan Quinn Reese Sage Skyler Taylor "
    "Hayden Logan Parker Rowan Drew Kai Phoenix Nico Wren Auden"
).split()
_LAST = (
    "Hale Reyes Okafor Torres Liang Patel Brennan Vasquez Singh Chen "
    "Walker Fischer Mendoza Yamada Park Quinn Caro Bishop Tanaka"
).split()


def _split_contact(contact: str | None) -> tuple[str, str] | None:
    if not contact:
        return None
    parts = re.split(r"[,;]", contact, maxsplit=1)[0].strip().split()
    if len(parts) >= 2:
        return parts[0], parts[-1]
    if len(parts) == 1:
        return parts[0], "Demo"
    return None


def _seed_lightweight(
    rng: random.Random,
    tenant_id: str,
    brand_name: str,
    brand_slug: str,
    contact_name: str | None,
) -> dict:
    """Create owner-coach + client roster. Returns summary dict."""

    first, last = _split_contact(contact_name) or (brand_name.split()[0], "Coach")
    owner_email = f"coach@{brand_slug}.example"
    owner_pwd = _gen_demo_password()
    owner = db.fetch_one(
        """insert into users (tenant_id, email, password_hash, role, name)
           values ($1,$2,$3,'owner',$4) returning id""",
        tenant_id, owner_email, hash_password(owner_pwd), f"{first} {last}",
    )
    owner_id = owner["id"] if owner else None
    if owner_id:
        # The platform expects a coach_profiles row for owners
        db.execute(
            """insert into coach_profiles (user_id, tenant_id, bio, timezone)
               values ($1,$2,$3,'America/New_York')""",
            owner_id, tenant_id,
            f"Owner-coach at {brand_name}.",
        )

    # Clients (12) — believable mix of goals
    goals = ["lose_fat", "build_muscle", "maintain", "lose_fat",
             "build_muscle", "maintain", "lose_fat", "build_muscle",
             "maintain", "lose_fat", "build_muscle", "maintain"]
    client_count = 0
    for i in range(12):
        f = rng.choice(_FIRST); l = rng.choice(_LAST)
        client_email = f"{f.lower()}{i}.{l.lower()}@{brand_slug}.example"
        c = db.fetch_one(
            """insert into users (tenant_id, email, password_hash, role, name)
               values ($1,$2,$3,'client',$4) returning id""",
            tenant_id, client_email,
            hash_password(_gen_demo_password()),
            f"{f} {l}",
        )
        if not c: continue
        cid = c["id"]
        sex = rng.choice(["male","female"])
        weight = round(rng.uniform(60, 95), 1) if sex == "male" else round(rng.uniform(52, 78), 1)
        height = round(rng.uniform(165, 188), 1) if sex == "male" else round(rng.uniform(155, 175), 1)
        db.execute(
            """insert into client_profiles
               (user_id, tenant_id, age, sex, weight_kg, height_cm,
                activity, goal)
               values ($1,$2,$3,$4,$5,$6,'moderate',$7)""",
            cid, tenant_id, rng.randint(22, 55), sex, weight, height, goals[i],
        )
        if owner_id:
            db.execute(
                """insert into coach_clients (tenant_id, coach_id, client_id, status)
                   values ($1,$2,$3,'active')""",
                tenant_id, owner_id, cid,
            )
        client_count += 1

    return {
        "owner_email": owner_email,
        "owner_password": owner_pwd,
        "client_count": client_count,
        "owner_name": f"{first} {last}",
    }


def provision_demo(
    *,
    brand_name: str,
    primary_color: str | None = None,
    accent_color: str | None = None,
    logo_url: str | None = None,
    source_url: str | None = None,
    scraped_brand: dict | None = None,
    prospect_contact: str | None = None,
    sales_owner: str | None = None,
    expiry_days: int = DEMO_TTL_DAYS,
) -> dict:
    if not brand_name or len(brand_name.strip()) < 2:
        return {"ok": False, "error": "brand_name is required"}

    brand_name = brand_name.strip()
    base_slug = slugify(brand_name) or "demo"
    slug = _unique_slug(base_slug)
    while db.fetch_one("select id from tenants where slug = $1", slug):
        slug = _unique_slug(base_slug)

    primary = _safe_color(primary_color, "#0F172A")
    accent  = _safe_color(accent_color,  "#22C55E")

    demo_password = _gen_demo_password()
    demo_password_hash = hashlib.sha256(demo_password.encode()).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=expiry_days)).isoformat()

    row = db.fetch_one(
        """insert into tenants
           (slug, name, plan, brand_primary, brand_accent, logo_url, app_name,
            billing_status, max_coaches, max_clients,
            is_demo, demo_password_hash, demo_expires_at, created_via,
            source_url, scraped_brand, prospect_contact, sales_owner)
           values ($1,$2,'coach',$3,$4,$5,$2,'trial',1,25,
                   true,$6,$7,'wizard',$8,$9,$10,$11)
           returning id""",
        slug, brand_name, primary, accent, logo_url,
        demo_password_hash, expires_at, source_url,
        json.dumps(scraped_brand) if scraped_brand else None,
        prospect_contact, sales_owner,
    )
    if not row:
        return {"ok": False, "error": "tenant insert failed"}
    tenant_id = row["id"]

    rng = random.Random(hashlib.sha256(slug.encode()).digest())
    seed_summary = _seed_lightweight(
        rng=rng,
        tenant_id=tenant_id,
        brand_name=brand_name,
        brand_slug=slugify(brand_name),
        contact_name=prospect_contact,
    )

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "slug": slug,
        "brand_name": brand_name,
        "demo_url": f"/demo/{slug}",
        "demo_password": demo_password,
        "owner_email": seed_summary["owner_email"],
        "owner_password": seed_summary["owner_password"],
        "expires_at": expires_at,
        "watermark": f"Sales preview — not affiliated with {brand_name}",
        "summary": seed_summary,
    }


def verify_demo_password(slug: str, candidate: str) -> dict | None:
    if not slug or not candidate:
        return None
    row = db.fetch_one(
        """select id, slug, name, logo_url, brand_primary, brand_accent,
                  is_demo, demo_password_hash, demo_expires_at
           from tenants where slug = $1 and is_demo = true""",
        slug,
    )
    if not row:
        return None
    if row.get("demo_expires_at"):
        try:
            exp = datetime.fromisoformat(row["demo_expires_at"].replace("Z","+00:00"))
            if exp < datetime.now(timezone.utc):
                return None
        except (ValueError, TypeError):
            pass
    candidate_hash = hashlib.sha256(candidate.encode()).hexdigest()
    if not _const_eq(candidate_hash, row.get("demo_password_hash") or ""):
        return None
    return row


def _const_eq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    out = 0
    for x, y in zip(a, b):
        out |= ord(x) ^ ord(y)
    return out == 0


def list_demos(active_only: bool = False) -> list[dict]:
    where = "where is_demo = true"
    if active_only:
        where += " and (demo_expires_at is null or demo_expires_at > now())"
    return db.fetch_all(
        f"""select id, slug, name as display_name, brand_primary, brand_accent,
                   logo_url, prospect_contact, sales_owner, source_url,
                   demo_expires_at::text as demo_expires_at,
                   created_at::text as created_at
            from tenants {where}
            order by created_at desc
            limit 200""",
    )


def expire_old_demos() -> int:
    rows = db.fetch_all(
        """select id from tenants
           where is_demo = true
             and demo_expires_at is not null
             and demo_expires_at < now() - interval '7 days'""",
    )
    n = 0
    for r in rows:
        db.execute("delete from tenants where id = $1", r["id"])
        n += 1
    return n
