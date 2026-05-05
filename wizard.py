"""ELH Coach branded-demo wizard.

Auth model: a single shared admin token from env var
ELH_COACH_WIZARD_TOKEN. ELH Coach has no sales-admin team — this is
Head's tool. The token is supplied by the wizard UI as a Bearer header.

Routes (apex-only):
    POST /api/wizard/scan      { url } -> brand kit
    POST /api/wizard/provision { brand_name, ... } -> demo URL + creds
    GET  /api/wizard/demos
    POST /api/wizard/demos/{id}/extend
    DELETE /api/wizard/demos/{id}
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from db import db
from fitapp_core import scrape_brand
import provisioner


def _expected_token() -> str:
    return os.environ.get("ELH_COACH_WIZARD_TOKEN", "").strip()


def _check_auth(handler) -> bool:
    """Constant-time compare against the env token."""
    expected = _expected_token()
    if not expected:
        # Wizard disabled if no token set (fail-closed)
        return False
    h = handler.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return False
    candidate = h[7:].strip()
    return hmac.compare_digest(candidate.encode(), expected.encode())


def handle(handler, method: str, path: str) -> bool:
    """Returns True if the path was handled."""
    if not path.startswith("/api/wizard"):
        return False

    if not _check_auth(handler):
        handler._j({"error": "wizard auth required"}, 401)
        return True

    if method == "POST" and path == "/api/wizard/scan":
        body = handler._body()
        url = (body.get("url") or "").strip()
        if not url or len(url) > 500:
            handler._j({"error": "url required"}, 400); return True
        try:
            kit = scrape_brand(url)
        except Exception as e:
            handler._j({"error": f"scrape failed: {e}"}, 500); return True
        handler._j(kit, 200); return True

    if method == "POST" and path == "/api/wizard/customer":
        body = handler._body()
        required = ("brand_name", "owner_email", "owner_name")
        missing = [k for k in required if not (body.get(k) or "").strip()]
        if missing:
            handler._j({"error": f"missing: {', '.join(missing)}"}, 400); return True
        try:
            res = provisioner.provision_customer(
                brand_name=body["brand_name"],
                primary_color=body.get("primary_color"),
                accent_color=body.get("accent_color"),
                logo_url=body.get("logo_url"),
                source_url=body.get("source_url"),
                scraped_brand=body.get("scraped_brand"),
                owner_email=body["owner_email"],
                owner_name=body["owner_name"],
                plan=(body.get("plan") or "coach"),
                custom_slug=body.get("custom_slug"),
                sales_owner=body.get("sales_owner") or "wizard",
            )
        except Exception as e:
            import traceback; traceback.print_exc()
            handler._j({"error": f"provision failed: {e}"}, 500); return True
        if not res.get("ok"):
            handler._j({"error": res.get("error") or "provision failed"}, 400); return True
        handler._j(res, 200); return True

    if method == "POST" and path == "/api/wizard/provision":
        body = handler._body()
        if not (body.get("brand_name") or "").strip():
            handler._j({"error": "brand_name required"}, 400); return True
        try:
            res = provisioner.provision_demo(
                brand_name=body["brand_name"],
                primary_color=body.get("primary_color"),
                accent_color=body.get("accent_color"),
                logo_url=body.get("logo_url"),
                source_url=body.get("source_url"),
                scraped_brand=body.get("scraped_brand"),
                prospect_contact=body.get("prospect_contact"),
                sales_owner=body.get("sales_owner") or "wizard",
                expiry_days=int(body["expiry_days"]) if body.get("expiry_days") else 30,
            )
        except Exception as e:
            import traceback; traceback.print_exc()
            handler._j({"error": f"provision failed: {e}"}, 500); return True
        if not res.get("ok"):
            handler._j({"error": res.get("error") or "provision failed"}, 400); return True
        handler._j(res, 200); return True

    if method == "GET" and path == "/api/wizard/demos":
        rows = provisioner.list_demos(active_only=False)
        handler._j({"demos": rows}, 200); return True

    if method == "POST" and path.startswith("/api/wizard/demos/") and path.endswith("/extend"):
        demo_id = path.split("/")[-2]
        body = handler._body()
        days = int(body.get("days") or 30)
        new_exp = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        db.execute(
            "update tenants set demo_expires_at = $1 where id = $2 and is_demo = true",
            new_exp, demo_id,
        )
        handler._j({"ok": True, "expires_at": new_exp}, 200); return True

    if method == "POST" and path == "/api/wizard/expire-now":
        n = provisioner.expire_old_demos()
        handler._j({"ok": True, "deleted": n}, 200); return True

    if method == "DELETE" and path.startswith("/api/wizard/demos/"):
        demo_id = path.split("/")[-1]
        row = db.fetch_one("select id from tenants where id = $1 and is_demo = true", demo_id)
        if not row:
            handler._j({"error": "demo not found"}, 404); return True
        db.execute("delete from tenants where id = $1 and is_demo = true", demo_id)
        handler._j({"ok": True}, 200); return True

    handler._j({"error": "wizard route not found"}, 404)
    return True
