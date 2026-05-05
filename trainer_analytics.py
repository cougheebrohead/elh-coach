"""ELH Coach trainer-console analytics. Tenant-scoped."""
from __future__ import annotations

from typing import Any, Optional
from db import db


def trainer_kpis(tenant_id: str, coach_id: Optional[str] = None) -> dict[str, Any]:
    args: list[Any] = [tenant_id]
    coach_filter = ""
    if coach_id:
        args.append(coach_id); coach_filter = " and cc.coach_id = $2"
    rows = db.fetch_all(
        f"""select
             count(distinct cc.client_id) filter (where cc.status = 'active') as active_clients,
             count(distinct u.id) filter (where u.created_at > now() - interval '7 days') as new_7d,
             count(distinct e.client_id) filter (where e.risk_tier in ('slipping','ghosting')) as at_risk,
             round(coalesce(avg(e.score), 0)::numeric, 1)::float as avg_engagement,
             count(distinct ss.id) filter (where ss.starts_at > now() - interval '7 days'
                                            and ss.status = 'completed') as sessions_7d,
             count(distinct m.id) filter (where m.sent_at > now() - interval '7 days') as messages_7d
           from coach_clients cc
           left join users u on u.id = cc.client_id and u.tenant_id = $1
           left join engagement_score e on e.client_id = cc.client_id and e.tenant_id = $1
           left join schedule_sessions ss on ss.client_id = cc.client_id and ss.tenant_id = $1
           left join messages m on m.client_id = cc.client_id and m.tenant_id = $1
           where cc.tenant_id = $1 {coach_filter}""",
        *args,
    )
    return rows[0] if rows else {}


def trainer_roster(tenant_id: str, *, coach_id: Optional[str] = None,
                   q: Optional[str] = None, risk_tier: Optional[str] = None,
                   limit: int = 200) -> list[dict]:
    args: list[Any] = [tenant_id]
    where = "cc.tenant_id = $1 and cc.status = 'active'"
    n = 1
    if coach_id:
        n += 1; args.append(coach_id); where += f" and cc.coach_id = ${n}"
    if risk_tier:
        n += 1; args.append(risk_tier); where += f" and e.risk_tier = ${n}"
    if q:
        n += 1; args.append(f"%{q.lower()}%")
        where += f" and (lower(u.name) like ${n} or lower(u.email) like ${n})"
    return db.fetch_all(
        f"""select u.id, u.name, u.email, u.created_at::text as joined_at,
                  e.score, e.risk_tier, e.last_login_at::text as last_login_at,
                  e.days_active_30,
                  cp.weight_kg, cp.goal,
                  (select count(*) from meals m where m.tenant_id = $1
                                                  and m.client_id = u.id
                                                  and m.log_date > current_date - interval '7 days') as meals_7d
           from coach_clients cc
           join users u on u.id = cc.client_id
           left join engagement_score e on e.client_id = u.id and e.tenant_id = $1
           left join client_profiles cp on cp.user_id = u.id
           where {where}
           order by e.score desc nulls last, u.name
           limit {int(limit)}""",
        *args,
    )


def client_overview(tenant_id: str, client_id: str) -> dict[str, Any]:
    user = db.fetch_one(
        """select u.id, u.name, u.email, u.created_at, u.last_login_at,
                  cc.coach_id, c.name as coach_name
           from users u
           left join coach_clients cc on cc.client_id = u.id and cc.status = 'active'
           left join users c on c.id = cc.coach_id
           where u.tenant_id = $1 and u.id = $2""",
        tenant_id, client_id,
    )
    if not user: return {}
    profile = db.fetch_one("select * from client_profiles where user_id = $1", client_id)
    engagement = db.fetch_one(
        "select * from engagement_score where tenant_id = $1 and client_id = $2",
        tenant_id, client_id,
    )
    weight = db.fetch_all(
        """select reading_at::date::text as date, weight_kg::float
           from biometrics
           where tenant_id = $1 and client_id = $2 and weight_kg is not null
           order by reading_at desc limit 30""",
        tenant_id, client_id,
    )
    nutrition = db.fetch_all(
        """select log_date::text as date,
                  (totals_json->>'calories')::int as calories,
                  (totals_json->>'protein')::int as protein
           from meals
           where tenant_id = $1 and client_id = $2
             and log_date > current_date - interval '30 days'
           order by log_date desc""",
        tenant_id, client_id,
    )
    enrollments = db.fetch_all(
        """select e.id, e.status, e.adherence_pct, e.started_at::text as started_at,
                  p.name as program_name
           from program_enrollments e
           join programs p on p.id = e.program_id
           where e.tenant_id = $1 and e.client_id = $2
           order by e.started_at desc""",
        tenant_id, client_id,
    )
    notes = db.fetch_all(
        """select id, body, created_at::text as created_at, coach_id
           from trainer_notes
           where tenant_id = $1 and client_id = $2
           order by created_at desc limit 30""",
        tenant_id, client_id,
    )
    return {
        "user": user, "profile": profile, "engagement": engagement,
        "weight": weight, "nutrition": nutrition,
        "enrollments": enrollments, "notes": notes,
    }


def list_programs(tenant_id: str) -> list[dict]:
    return db.fetch_all(
        """select p.id, p.name, p.slug, p.program_type, p.duration_days,
                  count(e.id) filter (where e.status = 'active') as active,
                  count(e.id) filter (where e.status = 'completed') as completed
           from programs p
           left join program_enrollments e on e.program_id = p.id
           where p.tenant_id = $1 and not p.is_archived
           group by p.id
           order by p.created_at desc""",
        tenant_id,
    )
