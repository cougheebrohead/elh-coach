-- Final fix: switch from USING (which binds typed params and breaks
-- text→uuid/int implicit casts) to safe literal substitution. Postgres
-- happily casts string LITERALS to uuid/int/timestamp — it just refuses
-- to cast typed parameters across these boundaries. quote_literal()
-- escapes the value so substitution is injection-safe.

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
    rec record;
    final text := q;
    v text;
    i int;
    tid text := ctx->>'tenant_id';
    uid text := ctx->>'user_id';
    rol text := ctx->>'role';
begin
    if tid is not null then perform set_config('app.tenant_id', tid, true); end if;
    if uid is not null then perform set_config('app.user_id',   uid, true); end if;
    if rol is not null then perform set_config('app.user_role', rol, true); end if;

    -- Reverse-iterate so $10 isn't mangled by $1 substitution
    for i in reverse jsonb_array_length(p) - 1 .. 0 loop
        v := case jsonb_typeof(p->i)
                when 'null'    then 'NULL'
                when 'number'  then (p->>i)
                when 'boolean' then (p->>i)
                else                quote_literal(p->>i)
             end;
        final := regexp_replace(final, '\$' || (i + 1)::text || '(?!\d)', v, 'g');
    end loop;

    for rec in execute 'with q as (' || final || ') select to_jsonb(q.*) as j from q' loop
        return next rec.j;
    end loop;
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
    final text := q;
    v text;
    i int;
    tid text := ctx->>'tenant_id';
    uid text := ctx->>'user_id';
    rol text := ctx->>'role';
begin
    if tid is not null then perform set_config('app.tenant_id', tid, true); end if;
    if uid is not null then perform set_config('app.user_id',   uid, true); end if;
    if rol is not null then perform set_config('app.user_role', rol, true); end if;

    for i in reverse jsonb_array_length(p) - 1 .. 0 loop
        v := case jsonb_typeof(p->i)
                when 'null'    then 'NULL'
                when 'number'  then (p->>i)
                when 'boolean' then (p->>i)
                else                quote_literal(p->>i)
             end;
        final := regexp_replace(final, '\$' || (i + 1)::text || '(?!\d)', v, 'g');
    end loop;

    execute final;
    get diagnostics n = ROW_COUNT;
    return n;
end;
$$;

revoke all on function app_query(text, jsonb, jsonb) from anon, authenticated, public;
revoke all on function app_exec (text, jsonb, jsonb) from anon, authenticated, public;
grant execute on function app_query(text, jsonb, jsonb) to service_role;
grant execute on function app_exec (text, jsonb, jsonb) to service_role;
