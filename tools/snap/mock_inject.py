"""Inject mock fetch + BRAND + auth token into app.html and client.html
so the SPA renders fully without a real backend. Lab demo extension:
seeds /api/me/labs, /api/clients/c1/labs, and /api/me/lab/photo so the
screenshot harness can drive the lab modal + coach drilldown to a
fully-realised state."""
import re

# ── Mock data shared by both apps ───────────────────────────────
# `direction` mirrors fitapp_core.BIOMARKER_DIRECTION so the demo
# matches what the live server enriches on read.
LAB_LATEST = {
    "panel_name": "Comprehensive Metabolic + Lipid + A1C",
    "drawn_at": "2026-04-22",
    "provider": "Quest Diagnostics",
    "biomarkers": {
        "hba1c":              {"value": 5.4, "unit": "%",     "flag": "in_range", "ref_low": 4.0, "ref_high": 5.6,  "direction": "up_bad"},
        "fasting_glucose":    {"value": 92,  "unit": "mg/dL", "flag": "in_range", "ref_low": 70,  "ref_high": 99,   "direction": "up_bad"},
        "ldl_cholesterol":    {"value": 118, "unit": "mg/dL", "flag": "high",     "ref_low": 0,   "ref_high": 100,  "direction": "up_bad"},
        "hdl_cholesterol":    {"value": 58,  "unit": "mg/dL", "flag": "in_range", "ref_low": 40,  "ref_high": 100,  "direction": "up_good"},
        "triglycerides":      {"value": 84,  "unit": "mg/dL", "flag": "in_range", "ref_low": 0,   "ref_high": 150,  "direction": "up_bad"},
        "total_cholesterol":  {"value": 186, "unit": "mg/dL", "flag": "in_range", "ref_low": 0,   "ref_high": 200,  "direction": "up_bad"},
        "tsh":                {"value": 1.8, "unit": "mIU/L", "flag": "in_range", "ref_low": 0.4, "ref_high": 4.5,  "direction": "neutral"},
        "vitamin_d":          {"value": 28,  "unit": "ng/mL", "flag": "low",      "ref_low": 30,  "ref_high": 80,   "direction": "up_good"},
        "ferritin":           {"value": 86,  "unit": "ng/mL", "flag": "in_range", "ref_low": 30,  "ref_high": 400,  "direction": "up_good"},
        "crp":                {"value": 0.6, "unit": "mg/L",  "flag": "in_range", "ref_low": 0,   "ref_high": 3.0,  "direction": "up_bad"},
    },
}

LAB_HISTORY = [
    {
        "id": "lab1",
        "panel_name": "Comprehensive Metabolic + Lipid + A1C",
        "drawn_at": "2026-04-22",
        "provider": "Quest Diagnostics",
        "results_json": LAB_LATEST["biomarkers"],
        "created_at": "2026-04-22T15:00:00Z",
    },
    {
        "id": "lab2",
        "panel_name": "Lipid Panel + A1C",
        "drawn_at": "2026-01-14",
        "provider": "LabCorp",
        "results_json": {
            "hba1c":             {"value": 5.7, "unit": "%",     "flag": "high",     "ref_low": 4.0, "ref_high": 5.6,  "direction": "up_bad"},
            "ldl_cholesterol":   {"value": 134, "unit": "mg/dL", "flag": "high",     "ref_low": 0,   "ref_high": 100,  "direction": "up_bad"},
            "hdl_cholesterol":   {"value": 49,  "unit": "mg/dL", "flag": "in_range", "ref_low": 40,  "ref_high": 100,  "direction": "up_good"},
            "triglycerides":     {"value": 112, "unit": "mg/dL", "flag": "in_range", "ref_low": 0,   "ref_high": 150,  "direction": "up_bad"},
            "total_cholesterol": {"value": 205, "unit": "mg/dL", "flag": "high",     "ref_low": 0,   "ref_high": 200,  "direction": "up_bad"},
            "vitamin_d":         {"value": 22,  "unit": "ng/mL", "flag": "low",      "ref_low": 30,  "ref_high": 80,   "direction": "up_good"},
            "ferritin":          {"value": 71,  "unit": "ng/mL", "flag": "in_range", "ref_low": 30,  "ref_high": 400,  "direction": "up_good"},
        },
        "created_at": "2026-01-14T15:00:00Z",
    },
    {
        "id": "lab3",
        "panel_name": "Annual Wellness",
        "drawn_at": "2025-09-02",
        "provider": "Quest Diagnostics",
        "results_json": {
            "hba1c":             {"value": 5.9, "unit": "%",     "flag": "high",     "ref_low": 4.0, "ref_high": 5.6,  "direction": "up_bad"},
            "ldl_cholesterol":   {"value": 142, "unit": "mg/dL", "flag": "high",     "ref_low": 0,   "ref_high": 100,  "direction": "up_bad"},
            "hdl_cholesterol":   {"value": 44,  "unit": "mg/dL", "flag": "in_range", "ref_low": 40,  "ref_high": 100,  "direction": "up_good"},
            "triglycerides":     {"value": 128, "unit": "mg/dL", "flag": "in_range", "ref_low": 0,   "ref_high": 150,  "direction": "up_bad"},
            "vitamin_d":         {"value": 19,  "unit": "ng/mL", "flag": "low",      "ref_low": 30,  "ref_high": 80,   "direction": "up_good"},
        },
        "created_at": "2025-09-02T15:00:00Z",
    },
]

# JSON serializable string for embedding in JS template literals
import json as _json
LAB_LATEST_JS  = _json.dumps(LAB_LATEST)
LAB_HISTORY_JS = _json.dumps(LAB_HISTORY)

# ── Coach app mocks ─────────────────────────────────────────────
COACH_MOCK = r"""
<script>
localStorage.setItem('chq_token', 'demo-token');

window.__BRAND__ = {
  name: 'ELH Coach', app_name: 'ELH Coach',
  primary: '#0A1628', accent: '#6B1620', logo_url: '',
};

const __LAB_HISTORY__ = """ + LAB_HISTORY_JS + r""";

const _origFetch = window.fetch;
window.fetch = async (url, opts = {}) => {
  url = (typeof url === 'string') ? url : url.url;
  if (!url.startsWith('/api/')) return _origFetch(url, opts);
  const j = (data, status=200) => Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));

  if (url === '/api/me' || url.startsWith('/api/me?')) {
    return j({
      user: { id: 'u1', email: 'demo@elhcoach.app', name: 'Coach Demo', role: 'owner', photo_url: '' },
      tenant: { id: 't1', slug: 'demo', name: 'ELH Coach', plan: 'coach', billing_status: 'active' },
    });
  }
  if (url.startsWith('/api/trainer/kpis')) {
    return j({ active_clients: 28, new_7d: 4, at_risk: 3, avg_engagement: '94%', sessions_7d: 142, messages_7d: 217 });
  }
  if (url.startsWith('/api/trainer/roster')) {
    return j({ clients: [
      { id:'c1', name:'Sarah Kim', email:'sarah@example.com', goal:'Fat loss', score:92, risk_tier:'crushing', meals_7d:7, last_login_at:'2026-05-05T14:00:00Z', joined_at:'2025-11-01' },
      { id:'c2', name:'Marcus Nash', email:'marcus@example.com', goal:'Hypertrophy', score:88, risk_tier:'on_track', meals_7d:6, last_login_at:'2026-05-05T08:00:00Z', joined_at:'2025-09-12' },
      { id:'c3', name:'Aisha Tariq', email:'aisha@example.com', goal:'Postnatal', score:74, risk_tier:'on_track', meals_7d:5, last_login_at:'2026-05-04T22:00:00Z', joined_at:'2026-01-08' },
    ]});
  }
  if (url.match(/\/api\/clients\/[^/]+\/overview/)) {
    return j({
      user: { id:'c1', name:'Sarah Kim', email:'sarah@elhcoach.app', joined_at:'2025-11-01', last_login_at:'2026-05-05T14:00:00Z', coach_name:'Coach Demo' },
      profile: { goal:'Fat loss · GLP-1 protein floor' },
      engagement: { score:92, risk_tier:'crushing', days_active_30:24 },
      nutrition: [
        { date:'2026-05-05', calories:1240, protein:88 },
        { date:'2026-05-04', calories:1880, protein:132 },
        { date:'2026-05-03', calories:1740, protein:124 },
      ],
      enrollments: [{ id:'p1', program_name:'PUSH/PULL/LEGS · 12wk', status:'active', adherence_pct:91 }],
      notes: [{ body:'Crushed last 4 weeks — bump protein floor to 1.7g/kg.', created_at:'2026-04-28T10:00:00Z' }],
      weight: [
        {weight_kg:70.4},{weight_kg:70.1},{weight_kg:69.9},{weight_kg:69.7},
        {weight_kg:69.6},{weight_kg:69.4},{weight_kg:69.3},{weight_kg:69.0},
      ],
    });
  }
  if (url.match(/\/api\/clients\/[^/]+\/labs/)) {
    return j({ labs: __LAB_HISTORY__ });
  }
  if (url.startsWith('/api/programs')) {
    return j({ programs: [
      { id:'p1', name:'PUSH/PULL/LEGS · 12wk', slug:'ppl12', program_type:'workout', duration_days:84, active:18, completed:34 },
    ]});
  }
  return j({});
};
</script>
"""

# ── Client app mocks ────────────────────────────────────────────
CLIENT_MOCK = r"""
<script>
localStorage.setItem('chq_token', 'demo-token');

window.__BRAND__ = {
  name: 'ELH Coach', app_name: 'ELH Coach',
  primary: '#FFFFFF', accent: '#6B1620', logo_url: '',
};

const __LAB_LATEST__  = """ + LAB_LATEST_JS  + r""";
const __LAB_HISTORY__ = """ + LAB_HISTORY_JS + r""";

const _origFetch = window.fetch;
window.fetch = async (url, opts = {}) => {
  url = (typeof url === 'string') ? url : url.url;
  if (!url.startsWith('/api/')) return _origFetch(url, opts);
  const j = (data, status=200) => Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));

  if (url === '/api/me' || url.startsWith('/api/me?')) {
    return j({
      user: { id: 'u1', email: 'sarah@elhcoach.app', name: 'Sarah Kim', role: 'client', photo_url: '' },
      tenant: { id: 't1', slug: 'demo', name: 'ELH Coach', plan: 'coach', billing_status: 'active' },
    });
  }
  if (url.startsWith('/api/me/cycle'))    return j({ set_up:true, phase:'Follicular', day_in_cycle:8, cycle_length:28, tip:'Energy is climbing — push the heavy compound day.' });
  if (url.startsWith('/api/me/recovery')) return j({ score:84, tier:'Strong', advice:'Sleep + HRV trending up — good day to push.' });
  if (url.startsWith('/api/me/today/workout')) return j({
    program_name:'PUSH/PULL/LEGS', day_of_program:1,
    workout: { name:'PUSH · Day 1', exercises:[
      {name:'Bench press', scheme:'4 × 6 @ RPE 8'},
      {name:'DB shoulder press', scheme:'3 × 10 @ RPE 7'},
      {name:'Cable fly', scheme:'3 × 12'},
    ]},
  });
  if (url.startsWith('/api/me/glucose/tir')) return j({
    tir: { in_range_pct:78, n_readings:142, mean_glucose:106, gmi:5.4 },
    readings: [],
  });
  if (url.startsWith('/api/me/labs')) return j({ labs: __LAB_HISTORY__ });
  if (url.startsWith('/api/me/lab/photo')) return j({ ok:true, parsed: __LAB_LATEST__ });
  if (url.startsWith('/api/me/lab/save'))  return j({ ok:true, id:'newlab' }, 201);
  if (url.startsWith('/api/me/today')) return j({
    coach: { coach_name:'Coach Demo' },
    today: { calories:1240, protein:88, carbs:118, fat:46, calorie_target:1800, protein_target:130 },
    engagement: { score:92, risk_tier:'crushing', days_active_30:24 },
  });

  return j({});
};
</script>
"""

def inject(path, mock):
    with open(path) as f:
        src = f.read()
    src = src.replace('<!--BRAND_INJECT-->', mock, 1)
    with open(path, 'w') as f:
        f.write(src)

if __name__ == '__main__':
    import os
    # When run from a build dir set by snap_all.sh, fall back to CWD.
    build = os.environ.get('SNAP_BUILD_DIR') or os.getcwd()
    inject(os.path.join(build, 'coach.html'),  COACH_MOCK)
    inject(os.path.join(build, 'client.html'), CLIENT_MOCK)
    print(f'  injected mocks into {build}/{{coach,client}}.html (with lab seed data)')
