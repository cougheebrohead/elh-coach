-- CoachHQ schema. Multi-tenant from day one — every business table
-- carries tenant_id and is gated by RLS so a coach at one org physically
-- cannot read another org's data through the API.

-- ─── tenants ──────────────────────────────────────────────────────
create table tenants (
    id              uuid primary key default gen_random_uuid(),
    slug            text not null unique check (slug ~ '^[a-z0-9-]{2,40}$'),
    name            text not null,
    plan            text not null default 'coach' check (plan in ('coach','studio','brand')),
    -- Brand identity (drives runtime theming)
    brand_primary   text not null default '#0F172A',  -- deep navy default
    brand_accent    text not null default '#22C55E',  -- one tenant accent
    logo_url        text,
    favicon_url     text,
    custom_domain   text unique,         -- tenant.elhcoachhq.app | their-own.com
    app_name        text not null default 'CoachHQ',
    -- Billing
    stripe_customer_id      text unique,
    stripe_subscription_id  text unique,
    billing_status  text not null default 'active' check (billing_status in ('trial','active','past_due','canceled')),
    trial_ends_at   timestamptz,
    -- Limits per plan (denormalized for fast read; truth lives in code)
    max_coaches     int not null default 1,
    max_clients     int not null default 25,
    -- Audit
    owner_user_id   uuid,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index tenants_custom_domain_idx on tenants (custom_domain) where custom_domain is not null;

-- ─── users (tenant-scoped) ────────────────────────────────────────
-- A user belongs to exactly one tenant. Cross-tenant trainers are not
-- supported in v1. Email is unique-per-tenant so the same person can be
-- a client at gym A and a coach at gym B with different accounts.
create table users (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    email           text not null,
    password_hash   text not null,
    role            text not null check (role in ('owner','admin','coach','client')),
    name            text not null,
    photo_url       text,
    email_verified_at timestamptz,
    last_login_at   timestamptz,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (tenant_id, email)
);
create index users_tenant_role_idx on users (tenant_id, role);

-- ─── coach roster (which clients each coach owns) ─────────────────
create table coach_clients (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    coach_id        uuid not null references users(id) on delete cascade,
    client_id       uuid not null references users(id) on delete cascade,
    started_at      timestamptz not null default now(),
    ended_at        timestamptz,
    status          text not null default 'active' check (status in ('active','paused','ended')),
    unique (tenant_id, coach_id, client_id)
);
create index coach_clients_lookup_idx on coach_clients (tenant_id, coach_id, status);

-- ─── coach profile (extends users for role='coach'|'admin'|'owner') ─
create table coach_profiles (
    user_id         uuid primary key references users(id) on delete cascade,
    tenant_id       uuid not null references tenants(id) on delete cascade,
    bio             text,
    specialties     text[],
    certifications  text[],
    timezone        text default 'America/New_York'
);

-- ─── client profile (extends users for role='client') ─────────────
create table client_profiles (
    user_id         uuid primary key references users(id) on delete cascade,
    tenant_id       uuid not null references tenants(id) on delete cascade,
    age             int check (age between 1 and 120),
    sex             text check (sex in ('male','female')),
    weight_kg       numeric(6,2),
    height_cm       numeric(6,2),
    activity        text default 'moderate',
    goal            text default 'maintain',
    conditions      text,
    allergies_json  jsonb default '[]'::jsonb,
    goals_json      jsonb default '{}'::jsonb,
    last_period_iso date,
    cycle_length    int default 28,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- ─── meals (per client, per day) ──────────────────────────────────
create table meals (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    client_id       uuid not null references users(id) on delete cascade,
    eaten_at        timestamptz not null default now(),
    log_date        date not null,                    -- user's local date, set by client
    items_json      jsonb not null,                   -- [{name,calories,protein,carbs,fat,portion,...}]
    totals_json     jsonb not null,                   -- {calories,protein,carbs,fat,sugar,...}
    source          text default 'manual' check (source in ('manual','photo','barcode','search')),
    created_at      timestamptz not null default now()
);
create index meals_client_date_idx on meals (tenant_id, client_id, log_date desc);

-- ─── messages (coach ↔ client thread) ─────────────────────────────
create table messages (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    coach_id        uuid not null references users(id) on delete cascade,
    client_id       uuid not null references users(id) on delete cascade,
    sender_id       uuid not null references users(id) on delete cascade,
    body            text not null,
    sent_at         timestamptz not null default now(),
    read_at         timestamptz,
    is_nudge        boolean default false      -- automated reminder flag
);
create index messages_thread_idx on messages (tenant_id, coach_id, client_id, sent_at desc);

-- ─── audit log (every tenant-scoped action) ──────────────────────
create table audit_log (
    id              bigserial primary key,
    tenant_id       uuid references tenants(id) on delete cascade,
    actor_id        uuid,
    actor_type      text default 'user',
    action          text not null,
    resource_type   text not null,
    resource_id     text,
    ip_hash         text,
    user_agent      text,
    details_json    jsonb,
    digest          text not null,
    prev_digest     text,
    created_at      timestamptz not null default now()
);
create index audit_log_tenant_time_idx on audit_log (tenant_id, created_at desc);

-- ─── sessions ─────────────────────────────────────────────────────
create table sessions (
    token_hash      text primary key,            -- SHA-256 of bearer token
    user_id         uuid not null references users(id) on delete cascade,
    tenant_id       uuid not null references tenants(id) on delete cascade,
    issued_at       timestamptz not null default now(),
    expires_at      timestamptz not null,
    last_seen_at    timestamptz not null default now(),
    ip_hash         text,
    user_agent      text
);
create index sessions_user_idx on sessions (user_id);

-- ─── RLS — every business table is tenant-scoped ──────────────────
alter table tenants          enable row level security;
alter table users            enable row level security;
alter table coach_clients    enable row level security;
alter table coach_profiles   enable row level security;
alter table client_profiles  enable row level security;
alter table meals            enable row level security;
alter table messages         enable row level security;
alter table audit_log        enable row level security;
alter table sessions         enable row level security;

-- Service role (the app server) bypasses RLS via 'service_role' JWT.
-- All API access is gated by Python middleware that resolves tenant_id
-- from Host header before any DB query. RLS is the second line of defense.
-- Per-tenant policies require app_metadata.tenant_id on the JWT — added
-- in 0002_rls_policies.sql once Auth0/Supabase Auth is wired.
