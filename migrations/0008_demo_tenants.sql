-- ELH Coach — sales-demo tenant extensions (mirrors ELH Health 0008).
--
-- The onboarding wizard scrapes a prospect coach's website (or Linktree),
-- stands up a branded tenant, and hands the salesperson a private preview
-- URL. Demos auto-expire and live behind a password gate.

alter table tenants add column if not exists is_demo            boolean      not null default false;
alter table tenants add column if not exists demo_password_hash text;
alter table tenants add column if not exists demo_expires_at    timestamptz;
alter table tenants add column if not exists created_via        text         not null default 'signup';
alter table tenants add column if not exists source_url         text;
alter table tenants add column if not exists scraped_brand      jsonb;
alter table tenants add column if not exists prospect_contact   text;
alter table tenants add column if not exists sales_owner        text;

create index if not exists tenants_is_demo_idx on tenants (is_demo) where is_demo = true;
create index if not exists tenants_demo_expires_idx on tenants (demo_expires_at) where demo_expires_at is not null;
