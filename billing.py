"""Stripe billing — Checkout + Customer Portal + webhook.

Plans + prices configured by env vars (so prices can change without
code edits). Annual = 2 months free per the spec (10/12 of monthly × 12).

Env vars expected on Render:
    STRIPE_SECRET_KEY                 sk_live_...
    STRIPE_WEBHOOK_SECRET             whsec_...
    STRIPE_PRICE_COACH_MONTHLY        price_...    ($89/mo)
    STRIPE_PRICE_COACH_ANNUAL         price_...    ($890/yr — 2 mo free)
    STRIPE_PRICE_STUDIO_MONTHLY       price_...    ($399/mo)
    STRIPE_PRICE_STUDIO_ANNUAL        price_...    ($3,990/yr)
    STRIPE_PRICE_BRAND_MONTHLY        price_...    ($2,500/mo)
    STRIPE_PRICE_BRAND_ANNUAL         price_...    ($25,000/yr)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from db import db

STRIPE_SECRET = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

PLAN_PRICES: dict[str, dict[str, str]] = {
    # Coach subscription tier — the only recurring base tier (Studio + Brand
    # killed 2026-05-05). When a tenant upgrades to "domain" or buys "native"
    # add-on, the Coach subscription is cancelled server-side on
    # checkout.session.completed.
    "coach":  {
        "monthly": os.environ.get("STRIPE_PRICE_COACH_MONTHLY", ""),
        "annual":  os.environ.get("STRIPE_PRICE_COACH_ANNUAL", ""),
    },
    # Real Domain Upgrade — one-time setup fee + annual maintenance.
    # Replaces the monthly Coach subscription with ownership.
    "domain": {
        "setup":       os.environ.get("STRIPE_PRICE_DOMAIN_SETUP", ""),       # $2,500 one-time
        "maintenance": os.environ.get("STRIPE_PRICE_DOMAIN_MAINTENANCE", ""), # $200/yr
    },
    # Native iOS + Android apps — optional add-on, available at any tier.
    # One-time setup + annual maintenance for OS-update rebuild + resubmission.
    "native": {
        "setup":       os.environ.get("STRIPE_PRICE_NATIVE_SETUP", ""),       # $4,500 one-time
        "maintenance": os.environ.get("STRIPE_PRICE_NATIVE_MAINTENANCE", ""), # $300/yr
    },
}

# Deprecated tiers (kept for back-compat reads on legacy webhook events
# that arrive after killed-plan customers churn out). DO NOT route new
# checkouts through these.
DEPRECATED_PLAN_PRICES = {
    "studio": {
        "monthly": os.environ.get("STRIPE_PRICE_STUDIO_MONTHLY", ""),
        "annual":  os.environ.get("STRIPE_PRICE_STUDIO_ANNUAL", ""),
    },
    "brand":  {
        "monthly": os.environ.get("STRIPE_PRICE_BRAND_MONTHLY", ""),
        "annual":  os.environ.get("STRIPE_PRICE_BRAND_ANNUAL", ""),
    },
}


def _stripe(method: str, path: str, params: dict | None = None) -> dict[str, Any]:
    if not STRIPE_SECRET:
        raise RuntimeError("STRIPE_SECRET_KEY not configured")
    url = f"https://api.stripe.com/v1/{path}"
    data = None
    if params:
        # Stripe wants form-encoded body
        data = urllib.parse.urlencode(params, doseq=True).encode()
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {STRIPE_SECRET}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Stripe-Version": "2024-06-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"Stripe {e.code}: {body}")


def create_checkout(*, tenant_id: str, owner_email: str, plan: str,
                    billing_cycle: str, success_url: str, cancel_url: str) -> dict[str, Any]:
    """Create a subscription Checkout session for a brand-new tenant."""
    plan = plan.lower()
    billing_cycle = billing_cycle.lower() if billing_cycle in ("monthly", "annual") else "monthly"
    price_id = PLAN_PRICES.get(plan, {}).get(billing_cycle)
    if not price_id:
        raise RuntimeError(f"Stripe price not configured for {plan}/{billing_cycle}")

    # Get-or-create Stripe customer
    t = db.fetch_one("select stripe_customer_id from tenants where id = $1", tenant_id)
    customer_id = (t or {}).get("stripe_customer_id")
    if not customer_id:
        cust = _stripe("POST", "customers", {
            "email": owner_email,
            "metadata[elhcoach_tenant_id]": tenant_id,
        })
        customer_id = cust["id"]
        db.execute(
            "update tenants set stripe_customer_id = $1, updated_at = now() where id = $2",
            customer_id, tenant_id,
        )

    session = _stripe("POST", "checkout/sessions", {
        "customer": customer_id,
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata[elhcoach_tenant_id]": tenant_id,
        "metadata[plan]": plan,
        "metadata[billing_cycle]": billing_cycle,
        "subscription_data[trial_period_days]": "14",  # 14-day trial on every new sub
    })
    return {"url": session["url"], "session_id": session["id"]}


def create_upgrade_checkout(*, tenant_id: str, owner_email: str, sku: str,
                            success_url: str, cancel_url: str) -> dict[str, Any]:
    """Checkout for one-time + recurring combos:
       sku='domain' = $2,500 setup + $200/yr maintenance
       sku='native' = $4,500 setup + $300/yr maintenance
    Stripe runs these as a 'subscription' mode session that includes the
    one-time setup as add_invoice_items on the first invoice."""
    sku = sku.lower()
    if sku not in ("domain", "native"):
        raise RuntimeError(f"Unknown upgrade sku: {sku}")

    setup_price = PLAN_PRICES[sku]["setup"]
    maint_price = PLAN_PRICES[sku]["maintenance"]
    if not setup_price or not maint_price:
        raise RuntimeError(f"Stripe price not configured for {sku}")

    # Get-or-create Stripe customer
    t = db.fetch_one("select stripe_customer_id from tenants where id = $1", tenant_id)
    customer_id = (t or {}).get("stripe_customer_id")
    if not customer_id:
        cust = _stripe("POST", "customers", {
            "email": owner_email,
            "metadata[elhcoach_tenant_id]": tenant_id,
        })
        customer_id = cust["id"]
        db.execute(
            "update tenants set stripe_customer_id = $1, updated_at = now() where id = $2",
            customer_id, tenant_id,
        )

    session = _stripe("POST", "checkout/sessions", {
        "customer": customer_id,
        "mode": "subscription",
        "line_items[0][price]": maint_price,           # recurring annual maintenance
        "line_items[0][quantity]": "1",
        "subscription_data[add_invoice_items][0][price]": setup_price,  # one-time setup on first invoice
        "subscription_data[add_invoice_items][0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata[elhcoach_tenant_id]": tenant_id,
        "metadata[upgrade_sku]": sku,
    })
    return {"url": session["url"], "session_id": session["id"]}


def create_billing_portal(tenant: dict[str, Any]) -> str:
    if not tenant.get("stripe_customer_id"):
        raise RuntimeError("No Stripe customer on this tenant")
    portal = _stripe("POST", "billing_portal/sessions", {
        "customer": tenant["stripe_customer_id"],
        "return_url": f"https://{tenant['slug']}.{os.environ.get('APEX_HOST','elhcoach.app')}/account",
    })
    return portal["url"]


# ────────────────────────────────────────────────────────────────────
#  Webhook
# ────────────────────────────────────────────────────────────────────

def _verify_signature(body: bytes, sig_header: str) -> dict[str, Any]:
    """Verify Stripe webhook signature. Stripe signs t=...,v1=... over
    the timestamp + payload — we recompute and compare in constant time."""
    if not STRIPE_WEBHOOK_SECRET:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET not configured")
    if not sig_header:
        raise RuntimeError("missing Stripe-Signature header")
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    timestamp = parts.get("t", "")
    sig_v1 = parts.get("v1", "")
    if not timestamp or not sig_v1:
        raise RuntimeError("malformed Stripe-Signature")
    # Reject events older than 5 minutes — replay protection
    try:
        if abs(int(time.time()) - int(timestamp)) > 300:
            raise RuntimeError("Stripe signature too old")
    except ValueError:
        raise RuntimeError("malformed timestamp")
    payload = f"{timestamp}.{body.decode('utf-8', errors='replace')}"
    expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig_v1):
        raise RuntimeError("Stripe signature mismatch")
    return json.loads(body)


def handle_stripe_webhook(body: bytes, sig_header: str) -> None:
    event = _verify_signature(body, sig_header)
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if etype == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        tenant_id = meta.get("elhcoach_tenant_id")
        sub_id = obj.get("subscription")
        upgrade_sku = meta.get("upgrade_sku")  # 'domain' or 'native' if upgrade flow
        plan = meta.get("plan", "coach")

        if tenant_id and sub_id and upgrade_sku == "domain":
            # Real Domain Upgrade: cancel the Coach $89/mo sub; the new sub_id
            # is the $200/yr maintenance recurring (with $2,500 add_invoice setup).
            t = db.fetch_one("select stripe_subscription_id from tenants where id = $1", tenant_id)
            old_sub = (t or {}).get("stripe_subscription_id")
            if old_sub and old_sub != sub_id:
                try:
                    _stripe("DELETE", f"subscriptions/{old_sub}", None)
                except Exception:
                    pass  # already cancelled / not found — non-fatal
            db.execute(
                """update tenants
                   set stripe_subscription_id = $1, billing_status = 'active',
                       plan = 'domain', updated_at = now()
                   where id = $2""",
                sub_id, tenant_id,
            )

        elif tenant_id and sub_id and upgrade_sku == "native":
            # Native Apps Add-On: keep the existing Coach/Domain sub running;
            # native maintenance becomes a SECOND subscription on the customer.
            # Track by setting a flag — don't overwrite stripe_subscription_id.
            db.execute(
                """update tenants
                   set native_subscription_id = $1, native_active = true,
                       updated_at = now()
                   where id = $2""",
                sub_id, tenant_id,
            )

        elif tenant_id and sub_id:
            # Initial Coach subscription (or other base-plan checkout)
            db.execute(
                """update tenants
                   set stripe_subscription_id = $1, billing_status = 'active',
                       plan = $2, updated_at = now()
                   where id = $3""",
                sub_id, plan, tenant_id,
            )

    elif etype in ("customer.subscription.updated", "customer.subscription.created"):
        sub_id = obj.get("id")
        status = obj.get("status", "")
        new_status = (
            "canceled" if status in ("canceled", "incomplete_expired") else
            "past_due" if status == "past_due" else
            "active"  if status in ("active", "trialing") else
            status
        )
        # Route to whichever column matches the sub_id (base sub or native add-on).
        db.execute(
            """update tenants
               set billing_status = $1, updated_at = now()
               where stripe_subscription_id = $2""",
            new_status, sub_id,
        )
        db.execute(
            """update tenants
               set native_active = ($1 = 'active'), updated_at = now()
               where native_subscription_id = $2""",
            new_status, sub_id,
        )

    elif etype == "customer.subscription.deleted":
        sub_id = obj.get("id")
        db.execute(
            "update tenants set billing_status = 'canceled', updated_at = now() where stripe_subscription_id = $1",
            sub_id,
        )
        db.execute(
            "update tenants set native_active = false, updated_at = now() where native_subscription_id = $1",
            sub_id,
        )

    # Other events (invoice.paid, customer.updated, etc.) are no-ops for now.
