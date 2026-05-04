"""Supabase REST client. Stdlib-only; mirrors FitApp's db.py shape with
typed query helpers and parameterized SQL via PostgREST RPC.

The `db` object exposes:
    db.execute(sql, *params, tenant_id=None, user_id=None, role=None) -> int rowcount
    db.fetch_one(sql, *params, tenant_id=None, user_id=None, role=None) -> dict | None
    db.fetch_all(sql, *params, tenant_id=None, user_id=None, role=None) -> list[dict]

`tenant_id`/`user_id`/`role` are passed to the RPC as ctx and applied via
SET LOCAL inside Postgres so the RLS policies in 0003 engage. The app
server's HTTP handlers are responsible for resolving these from the Host
header + bearer token before any DB call.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[CoachHQ] WARNING: SUPABASE_URL and SUPABASE_SERVICE_KEY env vars required", flush=True)


def _request(method: str, path: str, params: dict | None = None,
             body: dict | list | None = None) -> Any:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        from urllib.parse import urlencode
        url = url + "?" + urlencode(params, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")[:500]
        print(f"[CoachHQ] PostgREST {e.code}: {msg}", flush=True)
        raise


def _build_ctx(tenant_id: str | None, user_id: str | None, role: str | None) -> dict:
    ctx: dict[str, str] = {}
    if tenant_id: ctx["tenant_id"] = str(tenant_id)
    if user_id:   ctx["user_id"]   = str(user_id)
    if role:      ctx["role"]      = str(role)
    return ctx


class _DB:
    def execute(self, sql: str, *params: Any,
                tenant_id: str | None = None,
                user_id: str | None = None,
                role: str | None = None) -> int:
        result = _request("POST", "rpc/app_exec", body={
            "q": sql,
            "p": list(params),
            "ctx": _build_ctx(tenant_id, user_id, role),
        })
        # PostgREST wraps scalar returns as the value itself
        if isinstance(result, int): return result
        if isinstance(result, list) and result: return int(result[0])
        return 0

    def fetch_one(self, sql: str, *params: Any,
                  tenant_id: str | None = None,
                  user_id: str | None = None,
                  role: str | None = None) -> dict | None:
        rows = _request("POST", "rpc/app_query", body={
            "q": sql,
            "p": list(params),
            "ctx": _build_ctx(tenant_id, user_id, role),
        })
        return rows[0] if rows else None

    def fetch_all(self, sql: str, *params: Any,
                  tenant_id: str | None = None,
                  user_id: str | None = None,
                  role: str | None = None) -> list[dict]:
        return _request("POST", "rpc/app_query", body={
            "q": sql,
            "p": list(params),
            "ctx": _build_ctx(tenant_id, user_id, role),
        }) or []


db = _DB()
