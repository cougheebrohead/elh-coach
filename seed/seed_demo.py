#!/usr/bin/env python3
"""CoachHQ demo seed.

Creates a believable trainer workspace — 'Iron Tide Coaching' — for live
demos. Idempotent. 25 active clients, varied risk tiers, plausible meals,
biometrics, messages, and a couple of program enrollments.
"""
from __future__ import annotations

import json
import os
import random
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://ytdheyjfqcqrvswullyb.supabase.co")
os.environ.setdefault(
    "SUPABASE_SERVICE_KEY",
    open("/tmp/coachhq_keys.env").read().split("SUPABASE_SERVICE_KEY=", 1)[1].strip(),
)

from auth import hash_password

random.seed(20260504)

URL = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_SERVICE_KEY"]
HDR = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def rpc(fn, q, p):
    body = json.dumps({"q": q, "p": p, "ctx": {}}).encode()
    req = urllib.request.Request(f"{URL}/rest/v1/rpc/{fn}", data=body, headers=HDR, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


def query(q, *p): return rpc("app_query", q, list(p)) or []
def execute(q, *p): return rpc("app_exec", q, list(p))


FIRST_NAMES = ["Aaliyah","Adrian","Alex","Alicia","Andre","Aria","Caleb","Camila",
    "Chloe","Devon","Diana","Diego","Elena","Eli","Elise","Emma","Erica","Ethan",
    "Felix","Gabriel","Grace","Hannah","Harper","Henry","Imani","Isabel","Jasmine",
    "Jordan","Julian","Kai","Kara","Kayla","Lara","Leo","Logan","Maya","Mateo",
    "Mira","Naomi","Natalia","Niamh","Olivia","Omar","Owen","Priya","Quinn",
    "Rachel","Rafael","Reese","Riley","Rohan","Sara","Selena","Simone","Sofia",
    "Tatiana","Theo","Tia","Trent","Valeria","Vera","Violet","Willow","Yara","Zara"]
LAST_NAMES = ["Adams","Allen","Anderson","Bailey","Baker","Bell","Bennett","Brooks",
    "Brown","Bryant","Carter","Castro","Chen","Cohen","Cooper","Cruz","Davis","Diaz",
    "Edwards","Ellis","Evans","Fischer","Foster","Garcia","Gomez","Graham","Gray",
    "Green","Hall","Harris","Hayes","Henderson","Hernandez","Hill","Hughes","Hunter",
    "Jackson","Jenkins","Johnson","Jones","Kelly","Kim","King","Lee","Lewis","Lopez",
    "Martin","Martinez","Mitchell","Moore","Morgan","Nguyen","Park","Patel","Peters",
    "Phillips","Powell","Ramirez","Reyes","Rivera","Roberts","Robinson","Rodriguez",
    "Rogers","Russell","Sanchez","Santos","Scott","Shah","Silva","Singh","Smith",
    "Stewart","Sullivan","Taylor","Thomas","Thompson","Torres","Walker","Wang",
    "Ward","Wells","White","Williams","Wilson","Wright","Yang","Young"]


def upsert_tenant():
    rows = query("select id from tenants where slug = $1", "irontide")
    if rows:
        return rows[0]["id"]
    rows = query(
        """insert into tenants
           (slug, name, plan, brand_primary, brand_accent, app_name,
            billing_status, max_coaches, max_clients, trial_ends_at)
           values ($1,$2,$3,$4,$5,$2,$6,$7,$8,now() + interval '14 days')
           returning id""",
        "irontide", "Iron Tide Coaching", "studio",
        "#0F172A", "#22C55E", "active", 5, 200,
    )
    return rows[0]["id"]


def upsert_user(tenant_id, email, role, name):
    rows = query("select id from users where tenant_id = $1 and email = $2", tenant_id, email)
    if rows:
        return rows[0]["id"]
    pw_hash = hash_password("IronTide-Demo-2026!")
    rows = query(
        """insert into users
           (tenant_id, email, password_hash, role, name, last_login_at)
           values ($1,$2,$3,$4,$5, now() - (interval '1 hour' * $6::int))
           returning id""",
        tenant_id, email, pw_hash, role, name, random.randint(0, 240),
    )
    return rows[0]["id"]


PROGRAM_DEFS = [
    ("hypertrophy-12", "12-Week Hypertrophy", "combined", 84,
     "Push/pull/legs split with progressive overload.",
     {"calories": 200, "protein_g": 1.8, "carbs_pct": 45, "fat_pct": 25}),
    ("recomp-8", "8-Week Recomp", "combined", 56,
     "Body recomposition: lift heavy, eat at maintenance, drop fat.",
     {"calories": 0, "protein_g": 2.0, "carbs_pct": 40, "fat_pct": 30}),
    ("running-5k", "Couch to 5K", "workout_template", 56,
     "Run habit builder with strength accessory work.", {}),
]


def upsert_programs(tenant_id, coach_id):
    out = {}
    for slug, name, ptype, days, desc, nutrition in PROGRAM_DEFS:
        rows = query("select id from programs where tenant_id = $1 and slug = $2", tenant_id, slug)
        if rows:
            out[slug] = rows[0]["id"]; continue
        workouts = [
            {"day": d, "name": w_name, "exercises": [
                {"name": ex, "sets": 4, "reps": "6-10", "rpe": 7} for ex in exes
            ]}
            for d, (w_name, exes) in enumerate([
                ("Push", ["Bench", "OHP", "Dips"]),
                ("Pull", ["Deadlift", "Pull-up", "Row"]),
                ("Legs", ["Squat", "RDL", "Lunge"]),
            ], 1)
        ]
        rows = query(
            """insert into programs
               (tenant_id, coach_id, name, slug, program_type, duration_days,
                description, nutrition_json, workouts_json)
               values ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb)
               returning id""",
            tenant_id, coach_id, name, slug, ptype, days, desc,
            json.dumps(nutrition), json.dumps(workouts),
        )
        out[slug] = rows[0]["id"]
    return out


def main():
    print("→ tenant")
    tenant_id = upsert_tenant()
    print(f"  Iron Tide Coaching: {tenant_id}")

    print("→ owner + coach")
    owner_id = upsert_user(tenant_id, "owen@irontide.coach", "owner", "Owen Park")
    print(f"  owner: {owner_id}")

    # Mark this owner as a coach_profile entry too
    if not query("select user_id from coach_profiles where user_id = $1", owner_id):
        execute("insert into coach_profiles (user_id, tenant_id) values ($1,$2)", owner_id, tenant_id)

    print("→ programs")
    programs = upsert_programs(tenant_id, owner_id)
    for s, i in programs.items(): print(f"  {s}: {i}")

    print("→ 25 clients with realistic data")
    existing_count = query(
        "select count(*)::int as n from users where tenant_id = $1 and role = 'client'",
        tenant_id,
    )[0]["n"]
    if existing_count >= 25:
        print(f"  ✓ {existing_count} clients already exist")
        client_rows = query(
            "select id from users where tenant_id = $1 and role = 'client'", tenant_id,
        )
        client_ids = [r["id"] for r in client_rows]
    else:
        client_ids = []
        for i in range(25):
            first = random.choice(FIRST_NAMES)
            last  = random.choice(LAST_NAMES)
            email = f"{first.lower()}.{last.lower()}{i}@member.example"
            cid = upsert_user(tenant_id, email, "client", f"{first} {last}")
            client_ids.append(cid)

            # Wire to coach
            existing_cc = query(
                "select id from coach_clients where coach_id = $1 and client_id = $2",
                owner_id, cid,
            )
            if not existing_cc:
                execute(
                    """insert into coach_clients (tenant_id, coach_id, client_id)
                       values ($1,$2,$3)""",
                    tenant_id, owner_id, cid,
                )

            # Profile
            if not query("select user_id from client_profiles where user_id = $1", cid):
                execute(
                    """insert into client_profiles
                       (user_id, tenant_id, age, sex, weight_kg, height_cm, activity, goal)
                       values ($1,$2,$3,$4,$5,$6,$7,$8)""",
                    cid, tenant_id,
                    random.randint(22, 60), random.choice(["male", "female"]),
                    round(random.uniform(54, 105), 1), random.randint(155, 192),
                    "moderate",
                    random.choice(["lose", "maintain", "gain"]),
                )

            # Meals + biometrics + messages, varied by engagement
            engagement = random.choice(["crushing", "on_track", "slipping", "ghosting"])
            n_meals = {"crushing": 28, "on_track": 18, "slipping": 6, "ghosting": 0}[engagement]
            today = date.today()
            for d_back in range(n_meals):
                day = today - timedelta(days=d_back)
                cals = random.randint(1700, 2700)
                items = [
                    {"name": "Bowl", "calories": cals*0.4, "protein": 35, "carbs": 60, "fat": 18},
                    {"name": "Snack", "calories": cals*0.15, "protein": 12, "carbs": 18, "fat": 8},
                    {"name": "Dinner", "calories": cals*0.45, "protein": 50, "carbs": 70, "fat": 22},
                ]
                totals = {
                    "calories": cals,
                    "protein": sum(x["protein"] for x in items),
                    "carbs":   sum(x["carbs"]   for x in items),
                    "fat":     sum(x["fat"]     for x in items),
                }
                execute(
                    """insert into meals
                       (tenant_id, client_id, eaten_at, log_date, items_json, totals_json, source)
                       values ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7)""",
                    tenant_id, cid,
                    (datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
                     + timedelta(hours=12 + random.randint(-3, 5))).isoformat(),
                    day.isoformat(), json.dumps(items), json.dumps(totals),
                    random.choice(["manual", "photo", "barcode"]),
                )

            # Biometrics — weekly check-ins
            for d_back in [1, 7, 14, 21, 28]:
                execute(
                    """insert into biometrics
                       (tenant_id, client_id, reading_at, weight_kg, source)
                       values ($1,$2,$3,$4,'manual')""",
                    tenant_id, cid,
                    (datetime.now(timezone.utc) - timedelta(days=d_back)).isoformat(),
                    round(random.uniform(54, 105) - d_back * 0.05, 1),
                )

            # Engagement record
            score = {"crushing": 88, "on_track": 65, "slipping": 38, "ghosting": 12}[engagement]
            execute(
                """insert into engagement_score
                   (client_id, tenant_id, score, components_json, risk_tier,
                    last_login_at, days_active_30)
                   values ($1,$2,$3,$4::jsonb,$5,$6,$7)
                   on conflict (client_id) do nothing""",
                cid, tenant_id, score,
                json.dumps({"meals": n_meals, "workouts": random.randint(0,12),
                            "logins": random.randint(0,30), "msgs": random.randint(0,8)}),
                engagement,
                (datetime.now(timezone.utc) -
                 timedelta(days={"crushing":1,"on_track":3,"slipping":7,"ghosting":21}[engagement])).isoformat(),
                {"crushing": 28, "on_track": 18, "slipping": 8, "ghosting": 1}[engagement],
            )

            # Messages
            if engagement in ("crushing", "on_track") and random.random() < 0.6:
                if not query("select id from messages where coach_id = $1 and client_id = $2 limit 1", owner_id, cid):
                    for who, body in [
                        ("coach", "Killer week. Push protein 20g and we'll add tempo Friday."),
                        ("client", "Locked in. Sleep was rough Tue, otherwise good."),
                    ]:
                        sender = owner_id if who == "coach" else cid
                        execute(
                            """insert into messages
                               (tenant_id, coach_id, client_id, sender_id, body, sent_at)
                               values ($1,$2,$3,$4,$5, now() - (random() * interval '14 days'))""",
                            tenant_id, owner_id, cid, sender, body,
                        )

            # Some clients enrolled in programs
            if random.random() < 0.5:
                slug = random.choice(list(programs.keys()))
                if not query("select id from program_enrollments where program_id=$1 and client_id=$2",
                             programs[slug], cid):
                    started = today - timedelta(days=random.randint(7, 60))
                    execute(
                        """insert into program_enrollments
                           (tenant_id, program_id, client_id, assigned_by,
                            started_at, status, adherence_pct)
                           values ($1,$2,$3,$4,$5,$6,$7)""",
                        tenant_id, programs[slug], cid, owner_id,
                        started.isoformat(),
                        random.choice(["active", "active", "active", "completed", "paused"]),
                        random.randint(35, 95),
                    )

    print()
    print("──────────────────────────────────────────────")
    print(f"DEMO TENANT: Iron Tide Coaching ({tenant_id})")
    print(f"DEMO OWNER:  owen@irontide.coach / IronTide-Demo-2026!")
    print(f"  Clients:  {len(client_ids)}")
    print(f"  Programs: {len(programs)}")
    print("──────────────────────────────────────────────")


if __name__ == "__main__":
    main()
