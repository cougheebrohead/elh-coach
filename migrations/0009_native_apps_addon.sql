-- 0009_native_apps_addon.sql — Native Apps add-on tracking.
-- The Native Apps SKU ($4,500 setup + $300/yr maintenance) creates a SECOND
-- subscription on the same Stripe customer alongside the existing Coach/Domain
-- subscription. We track it separately so cancelling Coach doesn't stomp it,
-- and the webhook can route renewal events correctly.

alter table tenants
    add column if not exists native_subscription_id text unique,
    add column if not exists native_active boolean not null default false;

create index if not exists tenants_native_sub_idx
    on tenants (native_subscription_id)
    where native_subscription_id is not null;
