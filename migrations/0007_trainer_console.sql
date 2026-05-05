-- CoachHQ v2 — full trainer + client experience.
--
-- Adds programs/templates, schedule, content, wearables, lab uploads,
-- progress photos, trainer notes, weight/biomarker logs, engagement
-- scoring, saved views, API keys (Brand tier), webhooks.
--
-- Mirror of Vitalstack additions but tenant-keyed instead of org-keyed.

-- ─── programs (templates + assignments) ──────────────────────────
create table if not exists programs (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    coach_id        uuid references users(id) on delete set null,
    name            text not null,
    slug            text not null,
    program_type    text not null check (program_type in ('campaign','workout_template','nutrition_template','combined')),
    duration_days   int default 28,
    description     text,
    nutrition_json  jsonb default '{}'::jsonb,
    workouts_json   jsonb default '[]'::jsonb,
    content_ids     uuid[] default '{}',
    is_archived     boolean default false,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (tenant_id, slug)
);
create index if not exists programs_tenant_idx on programs (tenant_id) where not is_archived;

create table if not exists program_enrollments (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    program_id      uuid not null references programs(id) on delete cascade,
    client_id       uuid not null references users(id) on delete cascade,
    assigned_by     uuid references users(id) on delete set null,
    started_at      date not null default current_date,
    ends_at         date,
    status          text not null default 'active'
        check (status in ('active','completed','dropped','paused')),
    adherence_pct   int,
    created_at      timestamptz not null default now()
);
create index if not exists program_enrollments_client_idx on program_enrollments (client_id, status);

-- ─── content_items (educational drops) ───────────────────────────
create table if not exists content_items (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    title           text not null,
    body_md         text,
    media_url       text,
    media_type      text check (media_type in ('article','video','pdf','image','podcast')),
    duration_min    int,
    tags            text[] default '{}',
    is_published    boolean default true,
    created_at      timestamptz not null default now()
);

-- ─── schedule_sessions ───────────────────────────────────────────
create table if not exists schedule_sessions (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    coach_id        uuid not null references users(id) on delete cascade,
    client_id       uuid references users(id) on delete cascade,
    title           text,
    location        text,
    starts_at       timestamptz not null,
    ends_at         timestamptz not null,
    status          text not null default 'scheduled'
        check (status in ('scheduled','completed','cancelled','no_show')),
    notes           text,
    created_at      timestamptz not null default now()
);
create index if not exists schedule_sessions_coach_starts_idx on schedule_sessions (coach_id, starts_at);
create index if not exists schedule_sessions_client_starts_idx on schedule_sessions (client_id, starts_at) where client_id is not null;

-- ─── biometrics + wearables ──────────────────────────────────────
create table if not exists biometrics (
    id              bigserial primary key,
    tenant_id       uuid not null references tenants(id) on delete cascade,
    client_id       uuid not null references users(id) on delete cascade,
    reading_at      timestamptz not null,
    weight_kg       numeric(6,2),
    body_fat_pct    numeric(4,1),
    glucose_mgdl    int,
    bp_systolic     int,
    bp_diastolic    int,
    heart_rate_bpm  int,
    source          text default 'manual'
);
create index if not exists biometrics_client_time_idx on biometrics (client_id, reading_at desc);

create table if not exists wearable_connections (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    client_id       uuid not null references users(id) on delete cascade,
    provider        text not null,
    external_id     text,
    access_token_enc text,
    refresh_token_enc text,
    expires_at      timestamptz,
    last_sync_at    timestamptz,
    last_sync_status text,
    scopes          text[],
    is_active       boolean default true,
    created_at      timestamptz default now(),
    unique (client_id, provider)
);

create table if not exists wearable_samples (
    id              bigserial primary key,
    tenant_id       uuid not null references tenants(id) on delete cascade,
    client_id       uuid not null references users(id) on delete cascade,
    provider        text not null,
    sample_type     text not null,
    started_at      timestamptz not null,
    ended_at        timestamptz,
    value_json      jsonb not null
);
create index if not exists wearable_samples_client_time_idx on wearable_samples (client_id, started_at desc);

-- ─── lab results ─────────────────────────────────────────────────
create table if not exists lab_results (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    client_id       uuid not null references users(id) on delete cascade,
    panel_name      text not null,
    drawn_at        date not null,
    provider        text default 'manual',
    results_json    jsonb not null,
    raw_pdf_url     text,
    created_at      timestamptz default now()
);
create index if not exists lab_results_client_drawn_idx on lab_results (client_id, drawn_at desc);

-- ─── progress photos ─────────────────────────────────────────────
create table if not exists progress_photos (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    client_id       uuid not null references users(id) on delete cascade,
    photo_url       text not null,
    angle           text check (angle in ('front','side','back','other')),
    taken_at        timestamptz not null default now(),
    weight_kg       numeric(6,2)
);
create index if not exists progress_photos_client_taken_idx on progress_photos (client_id, taken_at desc);

-- ─── trainer notes (private) ─────────────────────────────────────
create table if not exists trainer_notes (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    coach_id        uuid not null references users(id) on delete cascade,
    client_id       uuid not null references users(id) on delete cascade,
    body            text not null,
    created_at      timestamptz default now()
);
create index if not exists trainer_notes_client_idx on trainer_notes (client_id, created_at desc);

-- ─── engagement_score (refreshed nightly) ────────────────────────
create table if not exists engagement_score (
    client_id       uuid primary key references users(id) on delete cascade,
    tenant_id       uuid not null references tenants(id) on delete cascade,
    score           int not null check (score between 0 and 100),
    components_json jsonb not null,
    risk_tier       text not null check (risk_tier in ('crushing','on_track','slipping','ghosting')),
    last_login_at   timestamptz,
    days_active_30  int default 0,
    computed_at     timestamptz default now()
);
create index if not exists engagement_score_tenant_tier_idx on engagement_score (tenant_id, risk_tier);

-- ─── saved views ─────────────────────────────────────────────────
create table if not exists saved_views (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    user_id         uuid not null references users(id) on delete cascade,
    surface         text not null,
    name            text not null,
    config_json     jsonb not null,
    created_at      timestamptz default now()
);

-- ─── api keys + webhooks (Brand tier) ────────────────────────────
create table if not exists api_keys (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    name            text not null,
    key_prefix      text not null,
    key_hash        text not null,
    scopes          text[] default '{clients:read}',
    last_used_at    timestamptz,
    revoked_at      timestamptz,
    created_at      timestamptz default now()
);

create table if not exists webhook_subscriptions (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null references tenants(id) on delete cascade,
    url             text not null,
    secret_hash     text not null,
    events          text[] not null,
    is_active       boolean default true,
    failure_count   int default 0,
    created_at      timestamptz default now()
);

-- ─── team_members table for Studio tier (assistant trainers) ─────
-- (users table already supports multiple coaches per tenant; nothing
-- structural to add — UI gates this by plan check.)

-- ─── extend tenants for billing/branding fields ──────────────────
alter table tenants add column if not exists logo_url text;
alter table tenants add column if not exists email_from text;
alter table tenants add column if not exists trial_ends_at timestamptz;
alter table tenants add column if not exists current_period_end timestamptz;

-- RLS
alter table programs            enable row level security;
alter table program_enrollments enable row level security;
alter table content_items       enable row level security;
alter table schedule_sessions   enable row level security;
alter table biometrics          enable row level security;
alter table wearable_connections enable row level security;
alter table wearable_samples    enable row level security;
alter table lab_results         enable row level security;
alter table progress_photos     enable row level security;
alter table trainer_notes       enable row level security;
alter table engagement_score    enable row level security;
alter table saved_views         enable row level security;
alter table api_keys            enable row level security;
alter table webhook_subscriptions enable row level security;

do $policy$
declare t text;
begin
  for t in select unnest(array[
      'programs','program_enrollments','content_items','schedule_sessions',
      'biometrics','wearable_connections','wearable_samples','lab_results',
      'progress_photos','trainer_notes','engagement_score','saved_views',
      'api_keys','webhook_subscriptions'])
  loop
      execute format('drop policy if exists %I_tenant on %I', t, t);
      execute format(
          'create policy %I_tenant on %I for all using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant())',
          t, t
      );
  end loop;
end $policy$;
