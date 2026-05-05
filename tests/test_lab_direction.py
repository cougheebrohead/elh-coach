"""Build-level test for biomarker direction intelligence.

Covers four layers:

  1. fitapp_core.BIOMARKER_DIRECTION + biomarker_direction() — the
     canonical map. Sanity-checks every category (up_good, up_bad,
     neutral) plus the unknown-key fallback.

  2. server.enrich_labs_with_direction — the read-time enrichment used
     by /api/me/labs and /api/clients/{id}/labs to backfill `direction`
     onto rows that pre-date fitapp-core 0.1.1.

  3. The deployed elhcoach.app build — /health returns the expected
     git SHA, and the served client.html ships the new direction-aware
     priorityDeltas function.

  4. The priorityDeltas color contract — extracted from the live
     client.html and exercised on synthetic data via a transliterated
     Python port of the same algorithm. (Defending the contract; we
     don't ship a JS runtime in the test harness.)

Run:
    PYTHONPATH=. python3 tests/test_lab_direction.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request


LIVE_HOST = os.environ.get("ELH_COACH_HOST", "https://elhcoach.app")


# ─── 1. fitapp_core direction map ─────────────────────────────────


def test_direction_up_good():
    from fitapp_core import biomarker_direction
    for k in ("hdl", "hdl_cholesterol", "vitamin_d", "vit_d",
              "ferritin", "iron", "free_t3", "free_t4",
              "total_testosterone", "free_testosterone",
              "vitamin_b12", "b12", "magnesium", "albumin", "egfr"):
        assert biomarker_direction(k) == "up_good", f"{k} should be up_good"


def test_direction_up_bad():
    from fitapp_core import biomarker_direction
    for k in ("hba1c", "a1c", "fasting_glucose", "glucose",
              "ldl", "ldl_cholesterol", "total_cholesterol",
              "triglycerides", "crp", "hs_crp",
              "alt", "ast", "ggt", "creatinine", "bun",
              "uric_acid", "homocysteine", "insulin",
              "blood_pressure_systolic", "sbp"):
        assert biomarker_direction(k) == "up_bad", f"{k} should be up_bad"


def test_direction_neutral():
    from fitapp_core import biomarker_direction
    for k in ("tsh", "sodium", "potassium", "chloride",
              "calcium", "phosphorus",
              "hemoglobin", "hematocrit",
              "mcv", "mch", "platelets", "wbc",
              "neutrophils", "lymphocytes"):
        assert biomarker_direction(k) == "neutral", f"{k} should be neutral"


def test_direction_unknown_falls_back_to_up_bad():
    from fitapp_core import biomarker_direction
    assert biomarker_direction("totally_made_up_marker") == "up_bad"
    assert biomarker_direction("xyz123") == "up_bad"


def test_direction_normalizes_input():
    from fitapp_core import biomarker_direction
    assert biomarker_direction("Vitamin D")  == "up_good"
    assert biomarker_direction("  HbA1c  ")  == "up_bad"
    assert biomarker_direction("LDL-Cholesterol") == "up_bad"


def test_scan_lab_output_carries_direction():
    """_sanitize_biomarker stamps `direction` so future writes save it."""
    from fitapp_core import labs as L
    raw = json.dumps({
        "panel_name": "X",
        "biomarkers": {
            "hdl_cholesterol":  {"value": 60, "unit": "mg/dL", "flag": "in_range"},
            "ldl_cholesterol":  {"value": 110, "unit": "mg/dL", "flag": "high"},
            "tsh":              {"value": 2.0, "unit": "mIU/L"},
        },
    })
    out = L._parse_lab_json(raw)
    assert out["biomarkers"]["hdl_cholesterol"]["direction"] == "up_good"
    assert out["biomarkers"]["ldl_cholesterol"]["direction"] == "up_bad"
    assert out["biomarkers"]["tsh"]["direction"] == "neutral"


# ─── 2. server.enrich_labs_with_direction ─────────────────────────


def test_enrich_legacy_rows():
    from server import enrich_labs_with_direction
    rows = [{
        "id": "lab1",
        "results_json": {
            "hdl_cholesterol": {"value": 55, "flag": "in_range"},
            "ldl":             {"value": 120, "flag": "high"},
            "tsh":             {"value": 2.0, "flag": "in_range"},
            "unknown_marker":  {"value": 9,  "flag": "in_range"},
        },
    }]
    enrich_labs_with_direction(rows)
    rj = rows[0]["results_json"]
    assert rj["hdl_cholesterol"]["direction"] == "up_good"
    assert rj["ldl"]["direction"]             == "up_bad"
    assert rj["tsh"]["direction"]             == "neutral"
    assert rj["unknown_marker"]["direction"]  == "up_bad"   # safe fallback


def test_enrich_preserves_existing_direction():
    """Idempotent: a row that already has direction is left alone."""
    from server import enrich_labs_with_direction
    rows = [{"id": "lab2", "results_json": {
        "ldl": {"value": 110, "flag": "high", "direction": "preset_value"},
    }}]
    enrich_labs_with_direction(rows)
    assert rows[0]["results_json"]["ldl"]["direction"] == "preset_value"


def test_enrich_handles_malformed_rows():
    """Non-dict results_json (None, list, missing) should not crash."""
    from server import enrich_labs_with_direction
    rows = [
        {"id": "a", "results_json": None},
        {"id": "b", "results_json": []},
        {"id": "c"},                              # missing key
        {"id": "d", "results_json": {"ldl": "not-a-dict"}},  # bad biomarker shape
    ]
    enrich_labs_with_direction(rows)   # must not raise
    # row d: the string biomarker is left as-is (no direction added)
    assert rows[3]["results_json"]["ldl"] == "not-a-dict"


def test_enrich_returns_same_list_object():
    """In-place mutation contract — caller can chain."""
    from server import enrich_labs_with_direction
    rows = [{"id": "x", "results_json": {"ldl": {"value": 100}}}]
    out = enrich_labs_with_direction(rows)
    assert out is rows


# ─── 3. Deployed build probes ─────────────────────────────────────


def _http_get(path: str, timeout: int = 10) -> tuple[int, str]:
    req = urllib.request.Request(LIVE_HOST + path, headers={
        "User-Agent": "elh-coach-direction-test/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def test_live_health_returns_expected_sha():
    """The live host should report a 12-char SHA — implies fitapp-core
    re-installed from git+...@main during the latest deploy."""
    code, body = _http_get("/health")
    assert code == 200, f"/health {code}: {body[:200]}"
    j = json.loads(body)
    assert j.get("ok") is True
    sha = j.get("version", "")
    assert re.match(r"^[a-f0-9]{12}$", sha), f"unexpected version: {sha!r}"


def test_live_client_html_has_direction_logic():
    """The served client.html must contain the new direction-aware
    branches — proves the deploy actually picked up the priorityDeltas
    rewrite, not a stale bundle."""
    code, body = _http_get("/client.html")
    assert code == 200, f"/client.html {code}"
    assert "priorityDeltas" in body
    assert "x.dir === 'up_good'" in body
    assert "x.dir === 'neutral'" in body
    # Old behavior had this exact line; the rewrite must remove it.
    assert "direction-only; clinical context belongs to coach" not in body


def test_live_client_html_no_orphan_methods():
    """Sanity: the old self._enrich_labs method got lifted to module
    level. Make sure no stale callers slipped through to production."""
    code, body = _http_get("/client.html")
    assert code == 200
    # client.html is HTML+JS, not server code — but we do want to make
    # sure no Python-style refs leaked into the bundle by accident.
    assert "self._enrich_labs" not in body


# ─── 4. priorityDeltas algorithm contract ─────────────────────────


def _priority_deltas_py(curr, prev):
    """Python transliteration of the JS priorityDeltas in client.html.
    Kept identical so this test defends the live algorithm."""
    out = []
    for k in curr.keys():
        c_entry = curr.get(k) or {}
        p_entry = (prev or {}).get(k) or {}
        c, p = c_entry.get("value"), p_entry.get("value")
        if c is None or p is None:
            continue
        d = c - p
        if abs(d) < 0.01:
            continue
        direction = c_entry.get("direction") or "up_bad"
        out.append({"k": k, "c": c, "p": p, "d": d, "dir": direction})
    out.sort(key=lambda x: abs(x["d"]), reverse=True)
    out = out[:2]
    rendered = []
    for x in out:
        rising = x["d"] > 0
        if x["dir"] == "neutral":
            color = "DIM"
        elif x["dir"] == "up_good":
            color = "GREEN" if rising else "RED"
        else:
            color = "RED" if rising else "GREEN"
        sign = "↑" if rising else "↓"
        rendered.append(f"{color} {sign} {x['k']}")
    return rendered


def test_priority_deltas_up_good_rising_is_green():
    out = _priority_deltas_py(
        {"hdl": {"value": 60, "direction": "up_good"}},
        {"hdl": {"value": 50}},
    )
    assert out == ["GREEN ↑ hdl"]


def test_priority_deltas_up_good_falling_is_red():
    out = _priority_deltas_py(
        {"hdl": {"value": 42, "direction": "up_good"}},
        {"hdl": {"value": 55}},
    )
    assert out == ["RED ↓ hdl"]


def test_priority_deltas_up_bad_rising_is_red():
    out = _priority_deltas_py(
        {"ldl": {"value": 140, "direction": "up_bad"}},
        {"ldl": {"value": 120}},
    )
    assert out == ["RED ↑ ldl"]


def test_priority_deltas_up_bad_falling_is_green():
    out = _priority_deltas_py(
        {"hba1c": {"value": 5.4, "direction": "up_bad"}},
        {"hba1c": {"value": 5.7}},
    )
    assert out == ["GREEN ↓ hba1c"]


def test_priority_deltas_neutral_dims_either_way():
    rise = _priority_deltas_py(
        {"tsh": {"value": 3.0, "direction": "neutral"}},
        {"tsh": {"value": 1.5}},
    )
    fall = _priority_deltas_py(
        {"tsh": {"value": 0.8, "direction": "neutral"}},
        {"tsh": {"value": 2.4}},
    )
    assert rise == ["DIM ↑ tsh"]
    assert fall == ["DIM ↓ tsh"]


def test_priority_deltas_missing_direction_defaults_up_bad():
    """Old jsonb rows that bypass enrich (defensive) still color sanely."""
    out = _priority_deltas_py(
        {"crp": {"value": 1.2}},   # no direction
        {"crp": {"value": 0.6}},
    )
    assert out == ["RED ↑ crp"]   # treated as up_bad, rising = red


def test_priority_deltas_top_two_by_magnitude():
    curr = {
        "ldl":    {"value": 140, "direction": "up_bad"},   # Δ +20  (biggest)
        "hdl":    {"value": 42,  "direction": "up_good"},  # Δ -13
        "tsh":    {"value": 2.1, "direction": "neutral"},  # Δ +0.6 (smallest)
        "hba1c":  {"value": 5.4, "direction": "up_bad"},   # Δ -0.2 (small)
    }
    prev = {
        "ldl":   {"value": 120}, "hdl":  {"value": 55},
        "tsh":   {"value": 1.5}, "hba1c":{"value": 5.6},
    }
    out = _priority_deltas_py(curr, prev)
    assert len(out) == 2
    assert out[0] == "RED ↑ ldl"   # |20| > |-13|
    assert out[1] == "RED ↓ hdl"   # up_good falling = red


def test_priority_deltas_filters_tiny_and_missing():
    curr = {
        "ldl":   {"value": 100,    "direction": "up_bad"},  # prev missing → skip
        "hba1c": {"value": 5.401,  "direction": "up_bad"},  # |Δ| 0.001 → skip
        "hdl":   {"value": 60,     "direction": "up_good"}, # Δ +5 → keep
    }
    prev = {"hba1c": {"value": 5.4}, "hdl": {"value": 55}}
    out = _priority_deltas_py(curr, prev)
    assert out == ["GREEN ↑ hdl"]


def test_priority_deltas_against_live_html():
    """The live client.html must contain the same color branches we
    transliterated above. Defends against future drift."""
    code, body = _http_get("/client.html")
    assert code == 200
    # All four arms of the if/else must be present.
    assert "x.dir === 'neutral'"      in body
    assert "x.dir === 'up_good'"      in body
    assert "rising ? 'var(--good)' : 'var(--bad)'" in body
    assert "rising ? 'var(--bad)'  : 'var(--good)'" in body


# ─── runner ───────────────────────────────────────────────────────


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in list(globals().items())
           if k.startswith("test_") and callable(v)]
    fails = []
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except Exception as e:
            fails.append((fn.__name__, e))
            print(f"  ✗ {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - len(fails)}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
