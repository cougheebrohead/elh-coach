-- Bug from 0005: substituted values can contain `$N` patterns (e.g. PBKDF2
-- hash `pbkdf2_sha256$200000$<salt>$<hash>`) that the next pass treats as
-- another placeholder, mangling the SQL.
--
-- Two-pass fix:
--   Pass 1 — replace every `$N` placeholder with a unique sentinel
--            `:__a_N_b__` (won't appear in any user value).
--   Pass 2 — replace each sentinel with the literal value (plain `replace`,
--            no regex, so $-chars in values can't recurse).

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
    n int := jsonb_array_length(p);
    tid text := ctx->>'tenant_id';
    uid text := ctx->>'user_id';
    rol text := ctx->>'role';
begin
    if tid is not null then perform set_config('app.tenant_id', tid, true); end if;
    if uid is not null then perform set_config('app.user_id',   uid, true); end if;
    if rol is not null then perform set_config('app.user_role', rol, true); end if;

    -- Pass 1: $N → sentinel. Reverse so $10 isn't mangled by $1.
    for i in reverse n - 1 .. 0 loop
        final := regexp_replace(final, '\$' || (i + 1)::text || '(?!\d)',
                                ':__a_' || (i + 1)::text || '_b__', 'g');
    end loop;

    -- Pass 2: sentinel → literal value (no regex, so $ in v can't recurse)
    for i in 0 .. n - 1 loop
        v := case jsonb_typeof(p->i)
                when 'null'    then 'NULL'
                when 'number'  then (p->>i)
                when 'boolean' then (p->>i)
                else                quote_literal(p->>i)
             end;
        final := replace(final, ':__a_' || (i + 1)::text || '_b__', v);
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
    rows_n int;
    final text := q;
    v text;
    i int;
    n int := jsonb_array_length(p);
    tid text := ctx->>'tenant_id';
    uid text := ctx->>'user_id';
    rol text := ctx->>'role';
begin
    if tid is not null then perform set_config('app.tenant_id', tid, true); end if;
    if uid is not null then perform set_config('app.user_id',   uid, true); end if;
    if rol is not null then perform set_config('app.user_role', rol, true); end if;

    for i in reverse n - 1 .. 0 loop
        final := regexp_replace(final, '\$' || (i + 1)::text || '(?!\d)',
                                ':__a_' || (i + 1)::text || '_b__', 'g');
    end loop;
    for i in 0 .. n - 1 loop
        v := case jsonb_typeof(p->i)
                when 'null'    then 'NULL'
                when 'number'  then (p->>i)
                when 'boolean' then (p->>i)
                else                quote_literal(p->>i)
             end;
        final := replace(final, ':__a_' || (i + 1)::text || '_b__', v);
    end loop;

    execute final;
    get diagnostics rows_n = ROW_COUNT;
    return rows_n;
end;
$$;

revoke all on function app_query(text, jsonb, jsonb) from anon, authenticated, public;
revoke all on function app_exec (text, jsonb, jsonb) from anon, authenticated, public;
grant execute on function app_query(text, jsonb, jsonb) to service_role;
grant execute on function app_exec (text, jsonb, jsonb) to service_role;
