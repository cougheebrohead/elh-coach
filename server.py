"""ELH Coach HTTP server — Python stdlib, multi-tenant from request 1.

Architecture:
    1. Host header → tenant resolver (subdomain or custom domain)
    2. Tenant config (brand, plan, limits) cached in-process
    3. Bearer-token sessions, scoped to (user_id, tenant_id)
    4. All DB queries through scoped helpers that include tenant_id
    5. Stripe Checkout for plan upgrades, webhook for activation

This file deliberately mirrors FitApp's server.py shape (same Python
stdlib, same patterns) so the team can move between the two without
relearning. The multi-tenant scope is the only meaningful diff.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import socketserver
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

# ── App version (Render injects RENDER_GIT_COMMIT) ─────────────────
APP_VERSION = (os.environ.get("RENDER_GIT_COMMIT") or "dev")[:12]
APP_URL = os.environ.get("APP_URL", "https://elh-coach.onrender.com")
APEX_HOST = os.environ.get("APEX_HOST", "elhcoach.app")

# ── Sentry (no-op when DSN unset) ───────────────────────────────────
SENTRY_ENABLED = False
_SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            traces_sample_rate=0.05,
            send_default_pii=False,
            release=APP_VERSION,
            environment=os.environ.get("SENTRY_ENV", "production"),
        )
        SENTRY_ENABLED = True
        print("[ELHCoach] Sentry initialized", flush=True)
    except Exception as e:
        print(f"[ELHCoach] Sentry init failed: {e}", flush=True)


def _capture(exc: BaseException) -> None:
    if SENTRY_ENABLED:
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass


# ── Project modules ────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import db                # noqa: E402
from tenants import tenant_resolver, plan_limits, brand_default  # noqa: E402
import wizard                     # noqa: E402
import provisioner                # noqa: E402
from auth import (               # noqa: E402
    hash_password, verify_password,
    issue_session, validate_session, revoke_session,
)
from billing import (            # noqa: E402
    create_checkout, handle_stripe_webhook,
    PLAN_PRICES,
)
import trainer_analytics  # noqa: E402
from ratelimit import allow      # noqa: E402

PORT = int(os.environ.get("PORT", "8080"))
SERVED_AT = time.time()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".txt":  "text/plain; charset=utf-8",
    ".ico":  "image/x-icon",
}


# ════════════════════════════════════════════════════════════════════
#  HTTP handler
# ════════════════════════════════════════════════════════════════════

class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = f"ELH Coach/{APP_VERSION}"

    # ─── helpers ─────────────────────────────────────────────────────
    def log_message(self, fmt: str, *args: Any) -> None:
        # silence default per-request logs; Render captures stdout
        pass

    def _client_ip(self) -> str:
        return (self.headers.get("X-Forwarded-For", "") or self.client_address[0] or "").split(",")[0].strip()

    def _path(self) -> str:
        return urllib.parse.urlparse(self.path).path or "/"

    def _qparams(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 5_000_000:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if 0 < length <= 25_000_000 else b""

    def _security_headers(self) -> None:
        self.send_header("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(self), microphone=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline' https://js.stripe.com; "
            "font-src 'self' data:; "
            "connect-src 'self' https://api.stripe.com https://*.sentry.io https://*.supabase.co; "
            "frame-src https://js.stripe.com https://hooks.stripe.com;"
        )

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization,X-Lang,X-Local-Date,X-Tz-Offset")

    def _j(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _resolve_tenant(self) -> dict[str, Any] | None:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        t = tenant_resolver(host)
        if t:
            return t
        # Apex-fallback override: ?tenant=<slug> (used by /demo/<slug> path
        # routing while wildcard SSL is still pending). Lets the same
        # subdomain-bound code work on plain apex elhcoach.app.
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        slug = (qs.get("tenant") or [None])[0]
        if slug:
            return db.fetch_one(
                "select * from tenants where slug = $1 and billing_status != 'canceled'",
                slug,
            )
        return None

    def _serve_static(self, name: str, mime: str, cache: bool = False) -> None:
        fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
        if not os.path.isfile(fpath):
            self.send_response(404); self.end_headers(); return
        with open(fpath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _serve_branded_index(self, tenant: dict[str, Any], fname: str = "app.html") -> None:
        """Serve a branded SPA shell with tenant brand variables injected."""
        fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
        if not os.path.isfile(fpath):
            self.send_response(404); self.end_headers(); return
        with open(fpath, "rb") as f:
            html = f.read().decode("utf-8")
        is_demo = bool(tenant.get("is_demo"))
        brand_block = json.dumps({
            "tenant_id":   tenant["id"],
            "tenant_slug": tenant["slug"],
            "name":        tenant["name"],
            "primary":     tenant["brand_primary"],
            "accent":      tenant["brand_accent"],
            "logo_url":    tenant.get("logo_url") or "",
            "app_name":    tenant["app_name"],
            "is_demo":     is_demo,
        })
        injected = html.replace(
            "<!--BRAND_INJECT-->",
            f'<script>window.__BRAND__ = {brand_block};</script>',
        )
        if is_demo:
            wm = (tenant.get("name") or "").replace("<","&lt;")
            watermark = (
                "<style>"
                "#__demo_wm{position:fixed;left:50%;bottom:14px;"
                "transform:translateX(-50%);z-index:2147483647;"
                "background:rgba(15,23,42,.92);color:#fff;"
                "font:600 11px/1 Inter,system-ui,sans-serif;"
                "letter-spacing:.04em;text-transform:uppercase;"
                "padding:8px 16px;border-radius:999px;pointer-events:none;"
                "box-shadow:0 4px 24px rgba(0,0,0,.25);}"
                "</style>"
                f"<div id=\"__demo_wm\">Sales Preview — Not Affiliated With {wm}</div>"
            )
            injected = injected.replace("</body>", watermark + "</body>", 1)
        body = injected.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if is_demo else "no-cache")
        if is_demo:
            self.send_header("X-Robots-Tag", "noindex, nofollow, nosnippet, noarchive")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    # ─── auth ────────────────────────────────────────────────────────
    def _auth_user(self, tenant_id: str | None) -> dict[str, Any] | None:
        h = self.headers.get("Authorization", "")
        if not h.startswith("Bearer "):
            return None
        token = h[7:].strip()
        if not token:
            return None
        sess = validate_session(token)
        if not sess:
            return None
        # Scope check: session must match resolved tenant
        if tenant_id and sess.get("tenant_id") != tenant_id:
            return None
        return sess

    def _rate(self, scope: str, limit: int, window_sec: int) -> bool:
        ip = self._client_ip()
        if not allow(f"{scope}:{ip}", limit, window_sec):
            self._j({"error": "Too many requests. Try again shortly."}, 429)
            return False
        return True

    # ─── routing ─────────────────────────────────────────────────────
    def do_OPTIONS(self) -> None:
        self.send_response(204); self._cors(); self.send_header("Content-Length", "0"); self.end_headers()

    def do_DELETE(self) -> None:
        path = self._path()
        if path.startswith("/api/wizard"):
            if wizard.handle(self, "DELETE", path):
                return
        return self._j({"error": "not found"}, 404)

    def do_GET(self) -> None:
        path = self._path()
        host = (self.headers.get("Host") or "").split(":")[0].lower()

        # Wizard + demo routes are host-agnostic. Resolve them before
        # tenant lookup so they work on apex without subdomain DNS.
        if path.startswith("/api/wizard"):
            if wizard.handle(self, "GET", path):
                return
        if path in ("/admin/onboard", "/admin/onboard/"):
            return self._serve_static("onboard.html", "text/html; charset=utf-8")
        if path.startswith("/demo/") or path.startswith("/api/demo/"):
            return self._demo_route("GET", path)

        tenant = self._resolve_tenant()

        # Apex (marketing site) — no tenant resolved
        if not tenant or host == APEX_HOST or host.startswith("www."):
            return self._do_get_apex(path)

        # Tenant-scoped paths
        if path == "/api/health":
            return self._j({"ok": True, "tenant": tenant["slug"], "version": APP_VERSION})
        if path == "/api/me":
            return self._api_me(tenant)
        if path == "/api/clients":
            return self._api_list_clients(tenant)
        if path.startswith("/api/messages/"):
            client_id = path.rsplit("/", 1)[-1]
            return self._api_messages(tenant, client_id)
        if path == "/api/billing/portal":
            return self._api_billing_portal(tenant)

        # ─── Trainer console v2 ─────────────────────────────────
        if path == "/api/trainer/kpis":
            return self._api_trainer_kpis(tenant)
        if path == "/api/trainer/roster":
            return self._api_trainer_roster(tenant)
        if path.startswith("/api/clients/") and path.endswith("/overview"):
            return self._api_client_overview(tenant, path.split("/")[3])
        if path == "/api/programs":
            return self._api_list_programs(tenant)

        # ─── Member-facing endpoints ─────────────────────────────
        if path == "/api/me/today":
            return self._api_me_today(tenant)

        # Static + branded SPA. Anything under /api/ that didn't match is 404.
        if path.startswith("/api/"):
            return self._j({"error": "not found"}, 404)
        if path in ("", "/", "/coach", "/login", "/signup", "/account", "/dashboard"):
            return self._serve_branded_index(tenant, "app.html")
        if path in ("/me", "/client"):
            return self._serve_branded_index(tenant, "client.html")
        ext = os.path.splitext(path)[1]
        if ext in MIME:
            return self._serve_static(path.lstrip("/"), MIME[ext], cache=True)
        # SPA route — serve branded index so client-side routing can take over
        return self._serve_branded_index(tenant, "app.html")

    def do_POST(self) -> None:
        path = self._path()
        host = (self.headers.get("Host") or "").split(":")[0].lower()

        # Wizard + demo routes — host-agnostic
        if path.startswith("/api/wizard"):
            if wizard.handle(self, "POST", path):
                return
        if path.startswith("/api/demo/"):
            return self._demo_route("POST", path)

        tenant = self._resolve_tenant()

        # Apex routes (no tenant)
        if not tenant or host == APEX_HOST:
            if path == "/api/signup-tenant":
                return self._api_signup_tenant()
            if path == "/api/contact":
                return self._api_contact()
            if path == "/api/stripe/webhook":
                return self._api_stripe_webhook()
            return self._j({"error": "not found"}, 404)

        # Tenant-scoped POSTs
        if path == "/api/login":
            return self._api_login(tenant)
        if path == "/api/logout":
            return self._api_logout(tenant)
        if path == "/api/invite-client":
            return self._api_invite_client(tenant)
        if path == "/api/log-meal":
            return self._api_log_meal(tenant)
        if path.startswith("/api/messages/"):
            client_id = path.rsplit("/", 1)[-1]
            return self._api_send_message(tenant, client_id)
        if path == "/api/checkout":
            return self._api_checkout(tenant)
        if path == "/api/programs":
            return self._api_create_program(tenant)
        if path.startswith("/api/clients/") and path.endswith("/note"):
            return self._api_add_note(tenant, path.split("/")[3])
        if path == "/api/me/meal":
            return self._api_member_log_meal(tenant)
        if path == "/api/me/biometric":
            return self._api_member_log_biometric(tenant)
        return self._j({"error": "not found"}, 404)

    # ────────────────────────────────────────────────────────────────
    #  Apex (marketing + signup)
    # ────────────────────────────────────────────────────────────────
    def _do_get_apex(self, path: str) -> None:
        if path in ("/health", "/api/health"):
            return self._j({"ok": True, "version": APP_VERSION, "ts": int(time.time())})
        if path in ("", "/", "/index.html"):
            return self._serve_static("marketing.html", "text/html; charset=utf-8")
        if path == "/pricing":
            return self._serve_static("pricing.html", "text/html; charset=utf-8")
        if path == "/signup":
            return self._serve_static("signup.html", "text/html; charset=utf-8")
        if path == "/login":
            return self._serve_static("login.html", "text/html; charset=utf-8")
        if path in ("/terms", "/legal/terms"):
            return self._serve_static("terms.html", "text/html; charset=utf-8")
        if path in ("/privacy", "/legal/privacy"):
            return self._serve_static("privacy.html", "text/html; charset=utf-8")
        ext = os.path.splitext(path)[1]
        if ext in MIME:
            return self._serve_static(path.lstrip("/"), MIME[ext], cache=True)
        # Unknown route — JSON 404 for /api/*, HTML 404 otherwise
        if path.startswith("/api/"):
            return self._j({"error": "not found"}, 404)
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", "9")
        self._security_headers()
        self.end_headers()
        self.wfile.write(b"Not found")

    def _api_signup_tenant(self) -> None:
        """Create a new tenant + owner user, then send to Stripe Checkout.
        The webhook activates the subscription on payment success."""
        if not self._rate("signup-tenant", limit=5, window_sec=60):
            return
        d = self._body()
        slug = re.sub(r"[^a-z0-9-]", "", (d.get("slug") or "").lower())[:40]
        name = (d.get("name") or "").strip()[:120]
        email = (d.get("email") or "").strip().lower()[:200]
        password = (d.get("password") or "")
        plan = (d.get("plan") or "coach").lower()
        billing = (d.get("billing_cycle") or "monthly").lower()
        if not slug or len(slug) < 2 or not name or not email or len(password) < 8:
            return self._j({"error": "Fill in slug, name, email, and password (8+ chars)."}, 400)
        if plan not in ("coach", "studio", "brand"):
            return self._j({"error": "Invalid plan."}, 400)
        if billing not in ("monthly", "annual"):
            billing = "monthly"
        if not re.match(r"^[a-z0-9-]+$", slug):
            return self._j({"error": "Slug must be lowercase letters, numbers, dashes."}, 400)

        # Check slug availability
        existing = db.fetch_one("select id from tenants where slug = $1", slug)
        if existing:
            return self._j({"error": f"'{slug}' is taken. Try another."}, 400)

        limits = plan_limits(plan)
        defaults = brand_default()
        tenant_id = db.fetch_one(
            """insert into tenants
                (slug, name, plan, brand_primary, brand_accent, app_name,
                 billing_status, max_coaches, max_clients)
               values ($1,$2,$3,$4,$5,$2,'trial',$6::int,$7::int)
               returning id""",
            slug, name, plan, defaults["primary"], defaults["accent"],
            limits["max_coaches"], limits["max_clients"],
        )["id"]

        owner_id = db.fetch_one(
            """insert into users (tenant_id, email, password_hash, role, name)
               values ($1, $2, $3, 'owner', $2)
               returning id""",
            tenant_id, email, hash_password(password),
        )["id"]
        db.execute("update tenants set owner_user_id = $1 where id = $2", owner_id, tenant_id)
        db.execute(
            "insert into coach_profiles (user_id, tenant_id) values ($1, $2)",
            owner_id, tenant_id,
        )

        # Stripe Checkout
        try:
            checkout = create_checkout(
                tenant_id=tenant_id, owner_email=email, plan=plan, billing_cycle=billing,
                success_url=f"https://{slug}.{APEX_HOST}/?welcome=1",
                cancel_url=f"https://{APEX_HOST}/signup?cancelled=1",
            )
        except Exception as e:
            _capture(e)
            return self._j({"error": "Payments not configured yet — contact us."}, 500)
        return self._j({
            "ok": True,
            "tenant_id": tenant_id,
            "slug": slug,
            "checkout_url": checkout["url"],
        })

    def _api_contact(self) -> None:
        if not self._rate("contact", limit=10, window_sec=60):
            return
        d = self._body()
        # Persist contact form (simple model — admin reads via SQL until UI exists)
        db.execute(
            """insert into audit_log (action, resource_type, details_json, digest)
               values ('contact.submit', 'contact', $1, $2)""",
            json.dumps({
                "email": (d.get("email") or "")[:200],
                "name":  (d.get("name") or "")[:120],
                "msg":   (d.get("message") or "")[:5000],
                "ip":    self._client_ip()[:64],
            }),
            hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest(),
        )
        return self._j({"ok": True})

    def _api_stripe_webhook(self) -> None:
        body = self._raw_body()
        sig = self.headers.get("Stripe-Signature", "")
        try:
            handle_stripe_webhook(body, sig)
        except Exception as e:
            _capture(e)
            return self._j({"error": str(e)}, 400)
        return self._j({"ok": True})

    # ────────────────────────────────────────────────────────────────
    #  Tenant-scoped APIs
    # ────────────────────────────────────────────────────────────────
    # ─── demo gate ──────────────────────────────────────────────────
    def _demo_route(self, method: str, path: str) -> None:
        clean = path.rstrip("/")
        # POST /api/demo/<slug>/login
        if method == "POST" and clean.startswith("/api/demo/") and clean.endswith("/login"):
            slug = clean[len("/api/demo/"):-len("/login")]
            if not slug:
                return self._j({"error": "slug required"}, 400)
            if not self._rate(f"demo-gate:{slug}", limit=8, window_sec=300):
                return
            body = self._body()
            password = (body.get("password") or "").strip()
            tenant = provisioner.verify_demo_password(slug, password)
            if not tenant:
                return self._j({"error": "invalid password or expired"}, 401)
            token = self._issue_demo_session(tenant)
            return self._j({
                "ok": True,
                "redirect_url": f"/?tenant={tenant['slug']}&token={token}",
                "brand": {
                    "name": tenant["name"],
                    "primary": tenant["brand_primary"],
                    "accent": tenant["brand_accent"],
                },
            })

        # GET /demo/<slug>(?key=…)
        if method == "GET" and clean.startswith("/demo/"):
            slug = clean[len("/demo/"):]
            qs = self._qparams()
            key = (qs.get("key") or [None])[0]
            row = db.fetch_one(
                """select slug, name, logo_url, brand_primary, brand_accent,
                          is_demo, demo_expires_at
                   from tenants where slug = $1""",
                slug,
            )
            if not row or not row.get("is_demo"):
                self.send_response(404); self.end_headers()
                self.wfile.write(b"Demo not found.")
                return
            if key:
                tenant = provisioner.verify_demo_password(slug, key)
                if tenant:
                    token = self._issue_demo_session(tenant)
                    self.send_response(302)
                    self.send_header(
                        "Location", f"/?tenant={tenant['slug']}&token={token}",
                    )
                    self.end_headers()
                    return
            return self._serve_demo_gate(row, key_attempted=bool(key))

        return self._j({"error": "not found"}, 404)

    def _issue_demo_session(self, tenant: dict[str, Any]) -> str:
        u = db.fetch_one(
            "select id from users where tenant_id = $1 and role = 'owner' limit 1",
            tenant["id"],
        )
        if not u:
            return ""
        return issue_session(
            user_id=u["id"], tenant_id=tenant["id"],
            ip=self._client_ip(),
            ua=("demo-gate;" + self.headers.get("User-Agent", ""))[:300],
        )

    def _serve_demo_gate(self, t: dict[str, Any], key_attempted: bool = False) -> None:
        primary = t.get("brand_primary") or "#0F172A"
        accent  = t.get("brand_accent")  or "#22C55E"
        name    = t.get("name") or "Demo"
        logo    = t.get("logo_url") or ""
        slug    = t.get("slug") or ""
        err = ("<p class='err'>That password didn't work. Try again, or ask "
               "the sender for the latest link.</p>" if key_attempted else "")
        logo_html = (f"<img src='{logo}' alt='' onerror='this.remove()'>" if logo else "")
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Sales preview — {name}</title>
<style>
  html,body{{margin:0;padding:0;height:100%;background:#FAFAF8;
    font:400 15px/1.4 Inter,system-ui,sans-serif;color:#0A0A0C;}}
  .wrap{{max-width:480px;margin:8vh auto 0;padding:0 24px;text-align:center;}}
  .card{{background:#fff;border:1px solid #E8E5DF;border-radius:16px;
    padding:40px 32px;box-shadow:0 8px 32px rgba(15,23,42,.06);}}
  img{{max-height:64px;max-width:240px;margin-bottom:16px;display:block;
    margin-left:auto;margin-right:auto;}}
  h1{{font:600 22px/1.2 Newsreader,Georgia,serif;margin:0 0 6px;}}
  .sub{{color:#5C5F66;font-size:14px;margin-bottom:28px;}}
  .pill{{display:inline-block;padding:5px 12px;border-radius:999px;
    background:{primary};color:#fff;font-weight:600;font-size:11px;
    letter-spacing:.05em;text-transform:uppercase;margin-bottom:24px;}}
  input[type=password]{{width:100%;padding:14px 16px;border:1px solid #E8E5DF;
    border-radius:10px;font-size:15px;background:#fff;}}
  input:focus{{outline:none;border-color:{accent};}}
  button{{width:100%;margin-top:14px;padding:14px;border:0;border-radius:10px;
    background:{primary};color:#fff;font-weight:600;font-size:14px;
    cursor:pointer;letter-spacing:.02em;}}
  .err{{color:#A8456B;font-size:13px;margin:14px 0 0;}}
  footer{{margin-top:24px;font-size:11px;color:#8A8E94;line-height:1.5;}}
</style>
</head><body>
<div class="wrap"><div class="card">
  <span class="pill">Sales Preview</span>
  {logo_html}
  <h1>{name}</h1>
  <p class="sub">This is a private preview prepared for you.<br>
     Not affiliated with {name}.</p>
  <form id="f">
    <input id="pw" name="password" type="password" placeholder="Access password" autofocus required>
    <button type="submit">Open preview</button>
    {err}
  </form>
</div>
<footer>Powered by ELH Coach · Sales engineering preview · Expires automatically.</footer>
</div>
<script>
document.getElementById('f').addEventListener('submit', async (e)=>{{
  e.preventDefault();
  const pw = document.getElementById('pw').value;
  const r = await fetch('/api/demo/{slug}/login', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{password: pw}}),
  }});
  if (!r.ok) {{
    document.querySelectorAll('.err').forEach(n=>n.remove());
    const e2 = document.createElement('p'); e2.className='err';
    e2.textContent = r.status===429 ? 'Too many attempts. Try again in a few minutes.' : 'Invalid password.';
    document.getElementById('f').appendChild(e2);
    return;
  }}
  const j = await r.json();
  location.href = j.redirect_url;
}});
</script>
</body></html>"""
        body = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Robots-Tag", "noindex, nofollow, nosnippet, noarchive")
        self.send_header("Cache-Control", "no-store, private")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _api_login(self, tenant: dict[str, Any]) -> None:
        if not self._rate("login", limit=10, window_sec=60):
            return
        d = self._body()
        email = (d.get("email") or "").strip().lower()
        password = d.get("password") or ""
        u = db.fetch_one(
            "select id, password_hash, role, name from users where tenant_id = $1 and email = $2",
            tenant["id"], email,
        )
        if not u or not verify_password(password, u["password_hash"]):
            return self._j({"error": "Invalid email or password."}, 401)
        token = issue_session(
            user_id=u["id"], tenant_id=tenant["id"],
            ip=self._client_ip(), ua=self.headers.get("User-Agent", "")[:300],
        )
        db.execute("update users set last_login_at = now() where id = $1", u["id"])
        return self._j({
            "token": token,
            "user": {"id": u["id"], "email": email, "role": u["role"], "name": u["name"]},
            "tenant": {"id": tenant["id"], "slug": tenant["slug"], "name": tenant["name"]},
        })

    def _api_logout(self, tenant: dict[str, Any]) -> None:
        h = self.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            revoke_session(h[7:].strip())
        return self._j({"ok": True})

    def _api_me(self, tenant: dict[str, Any]) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess:
            return self._j({"error": "unauthorized"}, 401)
        u = db.fetch_one(
            "select id, email, role, name, photo_url from users where id = $1",
            sess["user_id"],
        )
        return self._j({
            "user": u,
            "tenant": {
                "id": tenant["id"], "slug": tenant["slug"], "name": tenant["name"],
                "plan": tenant["plan"], "primary": tenant["brand_primary"],
                "accent": tenant["brand_accent"], "logo_url": tenant.get("logo_url"),
                "app_name": tenant["app_name"],
            },
        })

    def _api_list_clients(self, tenant: dict[str, Any]) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess or sess["role"] not in ("coach", "admin", "owner"):
            return self._j({"error": "forbidden"}, 403)
        rows = db.fetch_all(
            """select u.id, u.email, u.name, cc.started_at, cc.status
               from coach_clients cc
               join users u on u.id = cc.client_id
               where cc.tenant_id = $1 and cc.coach_id = $2 and cc.status = 'active'
               order by cc.started_at desc""",
            tenant["id"], sess["user_id"],
        )
        return self._j({"clients": rows})

    def _api_invite_client(self, tenant: dict[str, Any]) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess or sess["role"] not in ("coach", "admin", "owner"):
            return self._j({"error": "forbidden"}, 403)
        d = self._body()
        email = (d.get("email") or "").strip().lower()
        name = (d.get("name") or "").strip()
        if not email or not name:
            return self._j({"error": "Email and name required."}, 400)
        # Seat-cap check
        used = db.fetch_one(
            "select count(*) as n from coach_clients where tenant_id = $1 and status = 'active'",
            tenant["id"],
        )["n"]
        if used >= tenant["max_clients"]:
            return self._j({"error": f"Plan limit reached ({tenant['max_clients']} clients). Upgrade to add more."}, 402)
        # Idempotent: client may already exist as a user in this tenant
        existing = db.fetch_one(
            "select id from users where tenant_id = $1 and email = $2",
            tenant["id"], email,
        )
        if existing:
            client_id = existing["id"]
        else:
            tmp_pw = secrets.token_urlsafe(16)
            client_id = db.fetch_one(
                """insert into users (tenant_id, email, password_hash, role, name)
                   values ($1, $2, $3, 'client', $4)
                   returning id""",
                tenant["id"], email, hash_password(tmp_pw), name,
            )["id"]
            db.execute(
                "insert into client_profiles (user_id, tenant_id) values ($1, $2)",
                client_id, tenant["id"],
            )
        # Roster row
        db.execute(
            """insert into coach_clients (tenant_id, coach_id, client_id)
               values ($1, $2, $3)
               on conflict (tenant_id, coach_id, client_id) do nothing""",
            tenant["id"], sess["user_id"], client_id,
        )
        return self._j({"ok": True, "client_id": client_id})

    def _api_log_meal(self, tenant: dict[str, Any]) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess:
            return self._j({"error": "unauthorized"}, 401)
        d = self._body()
        items = d.get("items") or []
        if not items:
            return self._j({"error": "items[] required"}, 400)
        totals = {
            "calories": sum(int(i.get("calories") or 0) for i in items),
            "protein":  sum(float(i.get("protein") or 0) for i in items),
            "carbs":    sum(float(i.get("carbs") or 0) for i in items),
            "fat":      sum(float(i.get("fat") or 0) for i in items),
        }
        log_date = (d.get("date") or datetime.now(timezone.utc).date().isoformat())[:10]
        db.execute(
            """insert into meals (tenant_id, client_id, log_date, items_json, totals_json, source)
               values ($1, $2, $3, $4, $5, $6)""",
            tenant["id"], sess["user_id"], log_date,
            json.dumps(items), json.dumps(totals),
            (d.get("source") or "manual"),
        )
        return self._j({"ok": True, "totals": totals})

    def _api_messages(self, tenant: dict[str, Any], client_id: str) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess:
            return self._j({"error": "unauthorized"}, 401)
        # Both coach and client can see their thread; coach sees by client_id
        if sess["role"] == "client":
            client_id = sess["user_id"]
        rows = db.fetch_all(
            """select id, sender_id, body, sent_at, read_at, is_nudge
               from messages
               where tenant_id = $1 and client_id = $2
               order by sent_at desc
               limit 100""",
            tenant["id"], client_id,
        )
        return self._j({"messages": rows})

    def _api_send_message(self, tenant: dict[str, Any], client_id: str) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess:
            return self._j({"error": "unauthorized"}, 401)
        d = self._body()
        body = (d.get("body") or "").strip()[:5000]
        if not body:
            return self._j({"error": "body required"}, 400)
        # If sender is the client, set client_id = self; coach is the assigned one
        if sess["role"] == "client":
            client_id = sess["user_id"]
            cc = db.fetch_one(
                "select coach_id from coach_clients where tenant_id = $1 and client_id = $2 and status = 'active'",
                tenant["id"], client_id,
            )
            if not cc:
                return self._j({"error": "no coach assigned"}, 400)
            coach_id = cc["coach_id"]
        else:
            coach_id = sess["user_id"]
        db.execute(
            """insert into messages (tenant_id, coach_id, client_id, sender_id, body)
               values ($1, $2, $3, $4, $5)""",
            tenant["id"], coach_id, client_id, sess["user_id"], body,
        )
        return self._j({"ok": True})

    def _api_billing_portal(self, tenant: dict[str, Any]) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess or sess["role"] not in ("owner", "admin"):
            return self._j({"error": "forbidden"}, 403)
        from billing import create_billing_portal
        try:
            url = create_billing_portal(tenant)
            return self._j({"url": url})
        except Exception as e:
            _capture(e)
            return self._j({"error": str(e)}, 400)

    def _api_checkout(self, tenant: dict[str, Any]) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess or sess["role"] not in ("owner", "admin"):
            return self._j({"error": "forbidden"}, 403)
        d = self._body()
        plan = (d.get("plan") or "coach").lower()
        billing = (d.get("billing_cycle") or "monthly").lower()
        u = db.fetch_one("select email from users where id = $1", sess["user_id"])
        try:
            checkout = create_checkout(
                tenant_id=tenant["id"], owner_email=u["email"],
                plan=plan, billing_cycle=billing,
                success_url=f"{self._tenant_url(tenant)}/?upgraded=1",
                cancel_url=f"{self._tenant_url(tenant)}/?upgrade_cancelled=1",
            )
            return self._j({"url": checkout["url"]})
        except Exception as e:
            _capture(e)
            return self._j({"error": str(e)}, 400)

    def _tenant_url(self, tenant: dict[str, Any]) -> str:
        if tenant.get("custom_domain"):
            return f"https://{tenant['custom_domain']}"
        return f"https://{tenant['slug']}.{APEX_HOST}"

    # ────────────────────────────────────────────────────────────────
    #  Trainer console v2
    # ────────────────────────────────────────────────────────────────
    _TRAINER_ROLES = ("owner", "admin", "coach")

    def _require_trainer(self, tenant: dict[str, Any]):
        sess = self._auth_user(tenant["id"])
        if not sess:
            self._j({"error": "unauthorized"}, 401); return None
        if sess["role"] not in self._TRAINER_ROLES:
            self._j({"error": "forbidden"}, 403); return None
        return sess

    def _api_trainer_kpis(self, tenant: dict[str, Any]) -> None:
        sess = self._require_trainer(tenant)
        if not sess: return
        coach_id = sess["user_id"] if sess["role"] == "coach" else None
        return self._j(trainer_analytics.trainer_kpis(tenant["id"], coach_id=coach_id))

    def _api_trainer_roster(self, tenant: dict[str, Any]) -> None:
        sess = self._require_trainer(tenant)
        if not sess: return
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        coach_id = sess["user_id"] if sess["role"] == "coach" else (qs.get("coach_id") or [None])[0]
        rows = trainer_analytics.trainer_roster(
            tenant["id"], coach_id=coach_id,
            q=(qs.get("q") or [None])[0],
            risk_tier=(qs.get("risk_tier") or [None])[0],
            limit=int((qs.get("limit") or ["200"])[0]),
        )
        return self._j({"clients": rows})

    def _api_client_overview(self, tenant: dict[str, Any], client_id: str) -> None:
        sess = self._require_trainer(tenant)
        if not sess: return
        data = trainer_analytics.client_overview(tenant["id"], client_id)
        if not data.get("user"):
            return self._j({"error": "not found"}, 404)
        # If trainer is role=coach, ensure the client is on their roster
        if sess["role"] == "coach":
            on_roster = db.fetch_one(
                """select 1 as ok from coach_clients
                   where tenant_id = $1 and coach_id = $2 and client_id = $3""",
                tenant["id"], sess["user_id"], client_id,
            )
            if not on_roster:
                return self._j({"error": "forbidden"}, 403)
        return self._j(data)

    def _api_list_programs(self, tenant: dict[str, Any]) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess: return self._j({"error": "unauthorized"}, 401)
        return self._j({"programs": trainer_analytics.list_programs(tenant["id"])})

    def _api_create_program(self, tenant: dict[str, Any]) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess: return self._j({"error": "unauthorized"}, 401)
        if sess["role"] not in ("owner", "admin", "coach"):
            return self._j({"error": "forbidden"}, 403)
        body = self._body()
        slug = (body.get("name") or "").lower().replace(" ", "-")[:40]
        if not slug:
            return self._j({"error": "name required"}, 400)
        row = db.fetch_one(
            """insert into programs
               (tenant_id, coach_id, name, slug, program_type, duration_days,
                description, nutrition_json, workouts_json)
               values ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb)
               returning id""",
            tenant["id"], sess["user_id"],
            body.get("name"), slug,
            body.get("program_type") or "combined",
            int(body.get("duration_days") or 28),
            body.get("description") or "",
            json.dumps(body.get("nutrition") or {}),
            json.dumps(body.get("workouts") or []),
        )
        return self._j({"id": row["id"]}, 201)

    def _api_add_note(self, tenant: dict[str, Any], client_id: str) -> None:
        sess = self._require_trainer(tenant)
        if not sess: return
        body = self._body()
        text = (body.get("body") or "").strip()[:5000]
        if not text:
            return self._j({"error": "body required"}, 400)
        db.execute(
            """insert into trainer_notes (tenant_id, coach_id, client_id, body)
               values ($1,$2,$3,$4)""",
            tenant["id"], sess["user_id"], client_id, text,
        )
        return self._j({"ok": True}, 201)

    # ────────────────────────────────────────────────────────────────
    #  Member-facing endpoints (the client app)
    # ────────────────────────────────────────────────────────────────
    def _api_me_today(self, tenant: dict[str, Any]) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess: return self._j({"error": "unauthorized"}, 401)
        if sess["role"] != "client":
            return self._j({"error": "clients only"}, 403)
        today_meals = db.fetch_all(
            """select totals_json from meals
               where tenant_id = $1 and client_id = $2 and log_date = current_date""",
            tenant["id"], sess["user_id"],
        )
        cals = sum(int((m.get("totals_json") or {}).get("calories", 0)) for m in today_meals)
        protein = sum(int((m.get("totals_json") or {}).get("protein", 0)) for m in today_meals)
        profile = db.fetch_one(
            "select * from client_profiles where user_id = $1", sess["user_id"],
        )
        coach = db.fetch_one(
            """select cc.coach_id, c.name as coach_name
               from coach_clients cc join users c on c.id = cc.coach_id
               where cc.tenant_id = $1 and cc.client_id = $2 and cc.status = 'active' limit 1""",
            tenant["id"], sess["user_id"],
        )
        unread = db.fetch_one(
            """select count(*)::int as n from messages
               where tenant_id = $1 and client_id = $2 and sender_id != $2 and read_at is null""",
            tenant["id"], sess["user_id"],
        )
        engagement = db.fetch_one(
            "select score, risk_tier, days_active_30 from engagement_score where tenant_id = $1 and client_id = $2",
            tenant["id"], sess["user_id"],
        )
        target_kg = (profile or {}).get("weight_kg") or 70
        return self._j({
            "today": {
                "calories": cals, "protein": protein,
                "calorie_target": int(float(target_kg) * 30),
                "protein_target": int(float(target_kg) * 1.6),
            },
            "profile": profile,
            "coach": coach,
            "unread_messages": (unread or {}).get("n", 0),
            "engagement": engagement,
        })

    def _api_member_log_meal(self, tenant: dict[str, Any]) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess: return self._j({"error": "unauthorized"}, 401)
        if sess["role"] != "client":
            return self._j({"error": "clients only"}, 403)
        body = self._body()
        items = body.get("items") or []
        totals = {
            "calories": sum(int(i.get("calories") or 0) for i in items),
            "protein":  sum(int(i.get("protein")  or 0) for i in items),
            "carbs":    sum(int(i.get("carbs")    or 0) for i in items),
            "fat":      sum(int(i.get("fat")      or 0) for i in items),
        }
        db.execute(
            """insert into meals (tenant_id, client_id, log_date, items_json, totals_json, source)
               values ($1,$2, current_date, $3::jsonb, $4::jsonb, $5)""",
            tenant["id"], sess["user_id"],
            json.dumps(items), json.dumps(totals),
            (body.get("source") or "manual"),
        )
        return self._j({"ok": True, "totals": totals}, 201)

    def _api_member_log_biometric(self, tenant: dict[str, Any]) -> None:
        sess = self._auth_user(tenant["id"])
        if not sess: return self._j({"error": "unauthorized"}, 401)
        if sess["role"] != "client":
            return self._j({"error": "clients only"}, 403)
        body = self._body()
        db.execute(
            """insert into biometrics
               (tenant_id, client_id, reading_at, weight_kg, body_fat_pct,
                heart_rate_bpm, source)
               values ($1,$2, now(),$3,$4,$5,$6)""",
            tenant["id"], sess["user_id"],
            body.get("weight_kg"), body.get("body_fat_pct"),
            body.get("heart_rate_bpm"), body.get("source") or "manual",
        )
        return self._j({"ok": True}, 201)


# ════════════════════════════════════════════════════════════════════
#  Server bootstrap
# ════════════════════════════════════════════════════════════════════

class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    print(f"[ELHCoach] starting on :{PORT}", flush=True)
    with ThreadedServer(("0.0.0.0", PORT), Handler) as s:
        try:
            s.serve_forever()
        except KeyboardInterrupt:
            print("[ELHCoach] shutting down", flush=True)


if __name__ == "__main__":
    main()
