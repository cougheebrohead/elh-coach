# CoachHQ

Multi-tenant coaching SaaS for trainers, dietitians, and online coaches.

- **Coach** ($89/mo) — solo trainer, up to 25 clients
- **Studio** ($399/mo) — multi-trainer gym, up to 5 trainers + 200 clients
- **Brand** ($2,500/mo) — white-label, unlimited, BAA available

Built on `fitapp-core` for nutrition/cycle/glucose primitives. Stripe Checkout
for billing. Supabase Postgres for data, with row-level security as a second
line of defense behind app-layer tenant gating.

## Architecture

- **Apex** (`coachhq.app`) → marketing, signup, login, billing webhook.
- **Tenant subdomain** (`{slug}.coachhq.app` or custom domain) → branded SPA.
- Host header → tenant resolver → injected `__BRAND__` into HTML.
- All queries go through `app_query` / `app_exec` SECURITY DEFINER RPCs
  that `SET LOCAL app.tenant_id` before executing user SQL — even a
  bug that forgets a `WHERE tenant_id = $1` is caught by RLS.

## Local dev

```bash
cp .env.example .env   # fill in secrets
pip install -r requirements.txt
python server.py
```

Then `http://localhost:10000` for the apex (marketing), or set
`Host: yourtenant.coachhq.app` (use a `Host`-header proxy or `/etc/hosts`)
for tenant routing.

## Migrations

```bash
psql "$SUPABASE_URL" -f migrations/0001_init.sql
psql "$SUPABASE_URL" -f migrations/0002_app_query_rpc.sql
psql "$SUPABASE_URL" -f migrations/0003_rls_policies.sql
```

## Tests

```bash
pytest tests/
```

Tests must include cross-tenant isolation cases: tenant A cannot read
tenant B's clients/meals/messages even with a forged tenant_id in the
request body.

## Production

- Hosting: Render (Oregon)
- DB: Supabase (project `ytdheyjfqcqrvswullyb`)
- Email: Resend
- Errors: Sentry (server + client)

## Public API

The Brand tier exposes a per-tenant REST API. See `docs/api.md` (TBD).
