-- Stdlib-only Python servers don't have a typed SQL driver, so we
-- expose two RPCs that take parameterized SQL via PostgREST. They run
-- under SECURITY DEFINER as the service role — caller is the app server,
-- which owns its own auth + tenant scoping in Python.
--
-- Hard rule: NEVER expose these RPCs to anon. Anon is blocked by RLS;
-- these RPCs only work with the service-role JWT.
--
-- The RPCs accept optional ctx={"tenant_id":..., "user_id":..., "role":...}
-- which is SET LOCAL'd as app.tenant_id / app.user_id / app.user_role
-- before the user's SQL runs, so the RLS policies in 0003_rls_policies.sql
-- engage as a second line of defense.

create or replace function app_query(
    q text,
    p jsonb default '[]'::jsonb,
    ctx jsonb default '{}'::jsonb
) returns setof jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    result jsonb;
    tid text := ctx->>'tenant_id';
    uid text := ctx->>'user_id';
    rol text := ctx->>'role';
begin
    if tid is not null then perform set_config('app.tenant_id', tid, true); end if;
    if uid is not null then perform set_config('app.user_id',   uid, true); end if;
    if rol is not null then perform set_config('app.user_role', rol, true); end if;

    if jsonb_array_length(p) = 0 then
        for result in execute q loop
            return next result;
        end loop;
    else
        execute 'select jsonb_agg(row_to_json(t)) from (' || q || ') t'
            into result
            using
                p->>0, p->>1, p->>2, p->>3, p->>4,
                p->>5, p->>6, p->>7, p->>8, p->>9;
        if result is null then return; end if;
        for result in select * from jsonb_array_elements(result) loop
            return next result;
        end loop;
    end if;
end;
$$;

create or replace function app_exec(
    q text,
    p jsonb default '[]'::jsonb,
    ctx jsonb default '{}'::jsonb
) returns int
language plpgsql
security definer
set search_path = public
as $$
declare
    n int;
    tid text := ctx->>'tenant_id';
    uid text := ctx->>'user_id';
    rol text := ctx->>'role';
begin
    if tid is not null then perform set_config('app.tenant_id', tid, true); end if;
    if uid is not null then perform set_config('app.user_id',   uid, true); end if;
    if rol is not null then perform set_config('app.user_role', rol, true); end if;

    if jsonb_array_length(p) = 0 then
        execute q;
    else
        execute q
            using
                p->>0, p->>1, p->>2, p->>3, p->>4,
                p->>5, p->>6, p->>7, p->>8, p->>9;
    end if;
    get diagnostics n = ROW_COUNT;
    return n;
end;
$$;

-- Lock down: only the service role can invoke. RLS does not apply to
-- function execution, so we revoke from anon + authenticated.
revoke all on function app_query(text, jsonb, jsonb) from anon, authenticated, public;
revoke all on function app_exec (text, jsonb, jsonb) from anon, authenticated, public;
grant execute on function app_query(text, jsonb, jsonb) to service_role;
grant execute on function app_exec (text, jsonb, jsonb) to service_role;
