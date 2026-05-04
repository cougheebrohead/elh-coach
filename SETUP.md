# CoachHQ — production setup

The repo is ready. The two real-money / external steps below need credentials.

## 1. Apply migrations to Supabase

Supabase project: `ytdheyjfqcqrvswullyb` (CoachHQ — separate from FitApp).

```bash
export DATABASE_URL='postgres://postgres:<DB_PASSWORD>@db.ytdheyjfqcqrvswullyb.supabase.co:5432/postgres'
psql "$DATABASE_URL" -f migrations/0001_init.sql
psql "$DATABASE_URL" -f migrations/0002_app_query_rpc.sql
psql "$DATABASE_URL" -f migrations/0003_rls_policies.sql
```

## 2. Deploy to Render

The repo includes `render.yaml`, `Procfile`, and `requirements.txt`. To deploy:

**Option A — via dashboard (easiest):**
1. https://dashboard.render.com/blueprint/new
2. Connect `cougheebrohead/coachhq`
3. Render reads `render.yaml`, creates the web service automatically
4. Add the env vars in `.env.example` (keys not committed)

**Option B — via CLI:**
```bash
render login            # one-time browser auth
render services create blueprint render.yaml
```

## 3. Stripe — already provisioned (test mode)

Test products + prices created on 2026-05-03. See `.env.test-stripe` for IDs.

For production, switch to live keys:

```bash
SK="sk_live_..."  # the live secret
# Re-run the create_product_with_prices commands with this key, or
# duplicate the test products in the Stripe dashboard (test → live).
```

Then update Render env vars:
- `STRIPE_SECRET_KEY` → live secret
- `STRIPE_WEBHOOK_SECRET` → live webhook signing secret (after pointing
  the live webhook at `https://coachhq.app/api/stripe/webhook`)
- `STRIPE_PRICE_*` → the six live price IDs

## 4. DNS

Apex `coachhq.app` and wildcard `*.coachhq.app` both point to the Render
service. Custom domains for Studio/Brand tenants are added via the
billing/settings flow (and need a corresponding `render domain add`).

## 5. Sentry

Create a CoachHQ project in Sentry, paste the server DSN as
`SENTRY_DSN_SERVER` and the client DSN as `SENTRY_DSN_CLIENT` on Render.
