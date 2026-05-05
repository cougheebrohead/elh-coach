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
    "coach":  {
        "monthly": os.environ.get("STRIPE_PRICE_COACH_MONTHLY", ""),
        "annual":  os.environ.get("STRIPE_PRICE_COACH_ANNUAL", ""),
    },
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
            "metadata[coachhq_tenant_id]": tenant_id,
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
        "metadata[coachhq_tenant_id]": tenant_id,
        "metadata[plan]": plan,
        "metadata[billing_cycle]": billing_cycle,
        "subscription_data[trial_period_days]": "14",  # 14-day trial on every new sub
    })
    return {"url": session["url"], "session_id": session["id"]}


def create_billing_portal(tenant: dict[str, Any]) -> str:
    if not tenant.get("stripe_customer_id"):
        raise RuntimeError("No Stripe customer on this tenant")
    portal = _stripe("POST", "billing_portal/sessions", {
        "customer": tenant["stripe_customer_id"],
        "return_url": f"https://{tenant['slug']}.{os.environ.get('APEX_HOST','elhcoachhq.app')}/account",
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
        tenant_id = (obj.get("metadata") or {}).get("coachhq_tenant_id")
        sub_id = obj.get("subscription")
        plan = (obj.get("metadata") or {}).get("plan", "coach")
        if tenant_id and sub_id:
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
        cancel_at_period_end = obj.get("cancel_at_period_end", False)
        new_status = (
            "canceled" if status in ("canceled", "incomplete_expired") else
            "past_due" if status == "past_due" else
            "active"  if status in ("active", "trialing") else
            status
        )
        db.execute(
            """update tenants
               set billing_status = $1, updated_at = now()
               where stripe_subscription_id = $2""",
            new_status, sub_id,
        )

    elif etype == "customer.subscription.deleted":
        sub_id = obj.get("id")
        db.execute(
            "update tenants set billing_status = 'canceled', updated_at = now() where stripe_subscription_id = $1",
            sub_id,
        )

    # Other events (invoice.paid, customer.updated, etc.) are no-ops for now.
