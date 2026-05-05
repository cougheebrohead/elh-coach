-- RLS policies for ELH Coach.
--
-- The app server connects via the SECURITY DEFINER RPCs in 0002 using the
-- service_role key — those RPCs bypass RLS by design. The policies below
-- exist so that ANY future direct connection (a misconfigured Postgres
-- client, a compromised PostgREST request without the service-role JWT,
-- a future BI tool with read-only credentials) is denied at the row level
-- unless it carries an explicit tenant claim.
--
-- The policies key off `current_setting('app.tenant_id', true)::uuid`,
-- which the app's RPCs are expected to SET LOCAL before any query.
-- Without that setting, every policy returns no rows — fail-closed.

-- Helper: read tenant_id GUC, fail-closed if absent
create or replace function app_current_tenant() returns uuid
language plpgsql stable as $$
declare v text;
begin
  v := current_setting('app.tenant_id', true);
  if v is null or v = '' then return null; end if;
  return v::uuid;
exception when others then return null; end;
$$;

create or replace function app_current_user() returns uuid
language plpgsql stable as $$
declare v text;
begin
  v := current_setting('app.user_id', true);
  if v is null or v = '' then return null; end if;
  return v::uuid;
exception when others then return null; end;
$$;

create or replace function app_current_role() returns text
language plpgsql stable as $$
begin
  return coalesce(nullif(current_setting('app.user_role', true), ''), '');
end;
$$;

-- ─── tenants ──────────────────────────────────────────────────────
-- A user can read their own tenant row, and only owners/admins can update it.
create policy tenants_select on tenants for select
  using (id = app_current_tenant());
create policy tenants_update on tenants for update
  using (id = app_current_tenant() and app_current_role() in ('owner','admin'))
  with check (id = app_current_tenant() and app_current_role() in ('owner','admin'));

-- ─── users ────────────────────────────────────────────────────────
-- Users see all peers in their tenant. Coaches see clients on their roster
-- and themselves. Clients see only themselves and their assigned coach.
create policy users_select on users for select
  using (
    tenant_id = app_current_tenant() and (
      app_current_role() in ('owner','admin')
      or id = app_current_user()
      or (app_current_role() = 'coach' and exists (
        select 1 from coach_clients cc
        where cc.tenant_id = users.tenant_id
          and cc.coach_id = app_current_user()
          and cc.client_id = users.id
      ))
      or (app_current_role() = 'client' and exists (
        select 1 from coach_clients cc
        where cc.tenant_id = users.tenant_id
          and cc.client_id = app_current_user()
          and cc.coach_id = users.id
      ))
    )
  );
create policy users_insert on users for insert
  with check (tenant_id = app_current_tenant() and app_current_role() in ('owner','admin','coach'));
create policy users_update on users for update
  using (
    tenant_id = app_current_tenant() and (
      app_current_role() in ('owner','admin') or id = app_current_user()
    )
  )
  with check (tenant_id = app_current_tenant());

-- ─── coach_clients ────────────────────────────────────────────────
create policy coach_clients_select on coach_clients for select
  using (
    tenant_id = app_current_tenant() and (
      app_current_role() in ('owner','admin')
      or coach_id = app_current_user()
      or client_id = app_current_user()
    )
  );
create policy coach_clients_modify on coach_clients for all
  using (tenant_id = app_current_tenant() and app_current_role() in ('owner','admin','coach'))
  with check (tenant_id = app_current_tenant() and app_current_role() in ('owner','admin','coach'));

-- ─── coach_profiles & client_profiles ─────────────────────────────
create policy coach_profiles_rw on coach_profiles for all
  using (tenant_id = app_current_tenant() and (
    app_current_role() in ('owner','admin') or user_id = app_current_user()
    or exists (select 1 from coach_clients cc
               where cc.tenant_id = coach_profiles.tenant_id
                 and cc.coach_id = coach_profiles.user_id
                 and cc.client_id = app_current_user())
  ))
  with check (tenant_id = app_current_tenant());

create policy client_profiles_rw on client_profiles for all
  using (tenant_id = app_current_tenant() and (
    app_current_role() in ('owner','admin') or user_id = app_current_user()
    or exists (select 1 from coach_clients cc
               where cc.tenant_id = client_profiles.tenant_id
                 and cc.client_id = client_profiles.user_id
                 and cc.coach_id = app_current_user())
  ))
  with check (tenant_id = app_current_tenant());

-- ─── meals ────────────────────────────────────────────────────────
-- Client owns their own meals. Their assigned coach can read them.
create policy meals_select on meals for select
  using (tenant_id = app_current_tenant() and (
    app_current_role() in ('owner','admin')
    or client_id = app_current_user()
    or exists (select 1 from coach_clients cc
               where cc.tenant_id = meals.tenant_id
                 and cc.client_id = meals.client_id
                 and cc.coach_id = app_current_user())
  ));
create policy meals_insert on meals for insert
  with check (tenant_id = app_current_tenant() and (
    client_id = app_current_user()
    or app_current_role() in ('owner','admin','coach')
  ));
create policy meals_update on meals for update
  using (tenant_id = app_current_tenant() and (
    client_id = app_current_user() or app_current_role() in ('owner','admin')
  ))
  with check (tenant_id = app_current_tenant());
create policy meals_delete on meals for delete
  using (tenant_id = app_current_tenant() and (
    client_id = app_current_user() or app_current_role() in ('owner','admin')
  ));

-- ─── messages ─────────────────────────────────────────────────────
create policy messages_select on messages for select
  using (tenant_id = app_current_tenant() and (
    app_current_role() in ('owner','admin')
    or coach_id = app_current_user()
    or client_id = app_current_user()
  ));
create policy messages_insert on messages for insert
  with check (tenant_id = app_current_tenant() and sender_id = app_current_user() and (
    coach_id = app_current_user() or client_id = app_current_user()
  ));
create policy messages_update on messages for update
  using (tenant_id = app_current_tenant() and (
    coach_id = app_current_user() or client_id = app_current_user()
  ))
  with check (tenant_id = app_current_tenant());

-- ─── audit_log ────────────────────────────────────────────────────
-- Read-only for owner/admin within their tenant. Inserts always go through
-- the audit RPC (SECURITY DEFINER) so direct INSERT is blocked.
create policy audit_log_select on audit_log for select
  using (tenant_id = app_current_tenant() and app_current_role() in ('owner','admin'));

-- ─── sessions ─────────────────────────────────────────────────────
-- Sessions are managed exclusively by the auth RPCs; deny all direct access.
create policy sessions_deny on sessions for all using (false) with check (false);
