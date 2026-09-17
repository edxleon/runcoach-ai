"""The web app's snapshot: builder functions with plain dicts (covers the
empty/null cases that never occur by chance in a real data set) plus full
`assemble()` runs against a real tmp SQLite store."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import DETAIL, TODAY, make_activity, make_day, make_split
from runcoach import paths, snapshot
from runcoach.models import Activity, ScheduledWorkout
from runcoach.store import Store

TOP_LEVEL_KEYS = {
    "schema", "generated_at", "data_through", "stale_days", "stale_after_days",
    "today", "decision_today",
    "sleep", "load", "series", "weeks", "runs", "intensity", "vo2max", "zones", "plan",
    "aerobic", "predictions", "targets", "profile", "degraded", "counts"}

Z_EASY = {"z1": 100, "z2": 2000, "z3": 200, "z4": 0, "z5": 0}
Z_HARD = {"z1": 30, "z2": 497, "z3": 802, "z4": 2015, "z5": 65}


def _day_row(d: date, **kw) -> dict:
    row = {"day": d.isoformat(), "sleep_seconds": None, "sleep_score": None, "hrv_avg_ms": None,
           "hrv_status": None, "stress_avg": None, "body_battery_high": None,
           "resting_hr": None, "steps": None, "vo2max": None, "training_status": None,
           "acute_load": None, "chronic_load": None, "acwr_ratio": None, "acwr_status": None,
           "intensity_moderate_min": None, "intensity_vigorous_min": None,
           "deep_sleep_seconds": None, "light_sleep_seconds": None,
           "rem_sleep_seconds": None, "awake_seconds": None}
    row.update(kw)
    return row


def _week(week_start: str, *, easy_s=0, moderate_s=0, hard_s=0, partial=False) -> dict:
    return {"week_start": week_start, "distance_m": 0.0, "duration_s": 99999, "load": 0.0,
            "runs": 0, "workouts": 0, "easy_s": easy_s, "moderate_s": moderate_s,
            "hard_s": hard_s, "z5_s": 0, "partial": partial}


# ── assemble: empty database and schema ──────────────────────────────────────

def test_assemble_survives_empty_database(store, today):
    """A fresh install must not produce a traceback but a complete skeleton with
    nulls (the UI then honestly shows "no data" instead of a blank page)."""
    snap = snapshot.assemble(store, today=today)
    assert snap["schema"] == snapshot.SCHEMA
    assert snap["data_through"] is None and snap["stale_days"] is None
    assert snap["today"]["verdict"] is None and snap["today"]["day"] is None
    assert snap["decision_today"]["decision"] == "unknown"
    assert snap["decision_today"]["week"]["target_min"] == 0
    assert snap["sleep"]["seconds"] is None and snap["sleep"]["nights_14d"] == 0
    assert snap["vo2max"]["current"] is None and snap["vo2max"]["changed_days"] == []
    assert snap["zones"]["z4_low"] is None and snap["zones"]["source"] is None
    assert snap["runs"] == [] and snap["series"]["days"] == []
    assert snap["aerobic"]["points"] == [] and snap["predictions"]["k5_s"] is None
    assert snap["degraded"] == []
    json.dumps(snap)


def test_schema_keys_complete(store, today):
    """The frontend reads fixed paths — if a top-level block is missing, the page
    is broken rather than empty. Hence nailed down here."""
    snap = snapshot.assemble(store, today=today)
    assert set(snap) == TOP_LEVEL_KEYS
    assert set(snap["today"]) == {"day", "verdict", "reasons", "reason_flags", "signals"}
    assert set(snap["intensity"]) == {"d28", "d84"}
    assert snap["targets"] == {"hard_share": snapshot.HARD_SHARE_TARGET}
    assert set(snap["counts"]) == {"days", "runs", "weeks"}


def test_weeks_are_monday_aligned_and_include_the_running_week(store, today):
    snap = snapshot.assemble(store, today=today)       # TODAY is a Wednesday
    starts = [w["week_start"] for w in snap["weeks"]]
    assert len(starts) == snapshot.WEEKS + 1
    assert all(date.fromisoformat(s).weekday() == 0 for s in starts)
    assert starts[-1] == "2026-06-08"
    assert [w["partial"] for w in snap["weeks"]] == [False] * snapshot.WEEKS + [True]


def test_stale_days_from_latest_day(store, today):
    store.upsert_daily(make_day(today - timedelta(days=3), resting_hr=44, sleep_score=80))
    snap = snapshot.assemble(store, today=today)
    assert snap["stale_days"] == 3 and snap["data_through"] == "2026-06-07"
    # A verdict exists, but it is not from today → the decision refuses to use it.
    assert snap["today"]["verdict"] is not None
    assert snap["decision_today"]["decision"] == "unknown"


def test_assemble_defaults_today_to_paths_today(store, today):
    assert snapshot.assemble(store)["plan"]["today"] == TODAY.isoformat()


# ── sleep ────────────────────────────────────────────────────────────────────

def test_sleep_average_ignores_missing_nights():
    """Missing nights must not dilute the average as 0 — otherwise the app
    claims a sleep deficit that never happened."""
    rows = [_day_row(date(2026, 8, 1), sleep_seconds=25200, sleep_score=80),
            _day_row(date(2026, 8, 2)),                        # no measurement
            _day_row(date(2026, 8, 3), sleep_seconds=21600, sleep_score=70,
                     deep_sleep_seconds=3600, light_sleep_seconds=14400,
                     rem_sleep_seconds=3600, awake_seconds=600)]
    s = snapshot.build_sleep(rows)
    assert s["day"] == "2026-08-03" and s["seconds"] == 21600 and s["score"] == 70
    assert s["avg_14d_seconds"] == 23400          # (25200 + 21600) / 2, not / 3
    assert s["nights_14d"] == 2 and s["avg_14d_score"] == 75.0
    assert s["phases"] == {"deep": 3600, "light": 14400, "rem": 3600, "awake": 600}


def test_sleep_average_uses_the_last_14_nights_only():
    rows = [_day_row(date(2026, 8, 1) + timedelta(days=i), sleep_seconds=20000) for i in range(6)]
    rows += [_day_row(date(2026, 8, 7) + timedelta(days=i), sleep_seconds=28000) for i in range(14)]
    s = snapshot.build_sleep(rows)
    assert s["nights_14d"] == 14 and s["avg_14d_seconds"] == 28000
    assert s["avg_14d_score"] is None


def test_build_series_is_column_wise_with_gaps():
    rows = [_day_row(date(2026, 8, 1), resting_hr=44), _day_row(date(2026, 8, 3), vo2max=50.1)]
    s = snapshot.build_series(rows)
    assert s["days"] == ["2026-08-01", "2026-08-03"]
    assert s["resting_hr"] == [44, None] and s["vo2max"] == [None, 50.1]
    assert all(len(v) == 2 for v in s.values())


# ── VO2max: steps, no invented slope ─────────────────────────────────────────

def test_vo2max_steps_and_deltas():
    base = date(2026, 8, 11)
    rows = ([_day_row(base - timedelta(days=d), vo2max=47.0) for d in range(56, 30, -1)]
            + [_day_row(base - timedelta(days=d), vo2max=48.0) for d in range(30, -1, -1)])
    v = snapshot.build_vo2max(rows, [], today=base)
    assert v["current"] == 48.0 and v["carry_forward"] is True
    assert v["current_day"] == "2026-08-11"
    assert v["change_56d"] == 1.0
    assert v["change_28d"] == 0.0                 # the jump lies BEFORE the window
    assert v["changed_days"] == [{"day": "2026-07-12", "value": 48.0}]   # exactly ONE jump
    assert v["days_with_value"] == 57
    assert v["factors"] == {}


def test_vo2max_delta_is_none_without_old_enough_data():
    base = date(2026, 8, 11)
    rows = [_day_row(base - timedelta(days=d), vo2max=48.0) for d in range(10, -1, -1)]
    v = snapshot.build_vo2max(rows, [], today=base)
    assert v["change_28d"] is None and v["change_56d"] is None


def test_vo2max_factors_split_blocks():
    today = date(2026, 8, 11)

    def _run(days_ago, km, z5, z1):
        return {"day": (today - timedelta(days=days_ago)).isoformat(), "type": "running",
                "distance_m": km * 1000, "has_detail": True, "temperature_c": 20.0,
                "zones_s": {"z1": z1, "z2": 0, "z3": 0, "z4": 0, "z5": z5}}

    f = snapshot.vo2max_factors([_run(5, 10, 300, 2700), _run(40, 6, 0, 3600)], today=today)
    assert f["last_28d"]["runs"] == 1 and f["last_28d"]["distance_km"] == 10.0
    assert f["last_28d"]["z5_min"] == 5.0
    assert f["last_28d"]["easy_pct"] == 90.0
    assert f["last_28d"]["avg_temp_c"] == 20.0 and f["last_28d"]["with_detail"] == 1
    assert f["prev_28d"]["runs"] == 1 and f["prev_28d"]["z5_min"] == 0.0
    # The oldest run read is 40 days old: the older block (28–56 days) is not
    # covered in full, and the UI must not sell the comparison as a change.
    assert f["covers_full_window"] is False
    full = snapshot.vo2max_factors([_run(5, 10, 300, 2700), _run(60, 6, 0, 3600)], today=today)
    assert full["covers_full_window"] is True
    assert full["prev_28d"]["runs"] == 0 and full["prev_28d"]["easy_pct"] is None
    assert snapshot.vo2max_factors([], today=today) == {}


def test_vo2max_factors_block_edges():
    today = date(2026, 8, 11)
    def run(days_ago, m):
        return {"day": (today - timedelta(days=days_ago)).isoformat(),
                "type": "running", "distance_m": m}

    runs = [run(28, 5000),        # → prev block
            run(0, 7000),         # → last block
            run(56, 9000)]        # → neither
    f = snapshot.vo2max_factors(runs, today=today)
    assert f["last_28d"]["distance_km"] == 7.0 and f["prev_28d"]["distance_km"] == 5.0

    # Other sports are not running volume: an 80 km bike ride in the recent block
    # used to be handed to the coach as "running volume nearly doubled".
    ride = {"day": today.isoformat(), "type": "cycling", "distance_m": 80000}
    assert snapshot.vo2max_factors([*runs, ride], today=today) == f
    assert snapshot.vo2max_factors([ride], today=today) == {}


# ── zones card: ONE number with its origin ───────────────────────────────────

def test_zones_take_latest_run_with_bounds_and_never_compute():
    runs = [{"day": "2026-08-10", "hr_z4_low": None, "hr_z5_low": None},
            {"day": "2026-08-05", "hr_z4_low": 158, "hr_z5_low": 172}]
    z = snapshot.build_zones(runs, None, None, None)
    assert z["z4_low"] == 158 and z["z5_low"] == 172
    assert z["as_of_day"] == "2026-08-05" and "Garmin" in z["source"]
    assert snapshot.build_zones([{"day": "x"}], None, None, None)["source"] is None   # invent nothing


def test_zones_carry_the_lactate_threshold_with_its_own_date():
    """Two values, two dates: the zone bounds come from the last RUN, the
    threshold from Garmin's last MEASUREMENT — these can be weeks apart."""
    runs = [{"day": "2026-09-07", "hr_z4_low": 169, "hr_z5_low": 184}]
    lt = {"lthr_bpm": 176, "lt_speed_mps": 3.44, "lt_measured_on": "2026-07-13",
          "seen_on": "2026-09-07"}
    z = snapshot.build_zones(runs, lt, None, None)
    assert z["z4_low"] == 169 and z["as_of_day"] == "2026-09-07"
    assert z["lthr_bpm"] == 176 and z["lt_speed_mps"] == 3.44
    assert z["lt_measured_on"] == "2026-07-13"
    assert z["lt_pace_s_per_km"] == 291               # 3.44 m/s → 4:51 min/km
    assert "two dates" in z["note"]


def test_zones_without_a_threshold_claim_nothing():
    runs = [{"day": "2026-09-07", "hr_z4_low": 169, "hr_z5_low": 184}]
    z = snapshot.build_zones(runs, None, None, None)
    assert z["lthr_bpm"] is None and z["lt_pace_s_per_km"] is None
    assert z["lt_measured_on"] is None and "no measurement" in z["note"]
    # the keys stay even without runs (the frontend checks for null)
    empty = snapshot.build_zones([], None, None, None)
    assert "lthr_bpm" in empty and empty["lthr_bpm"] is None
    assert empty["lt_history"] == [] and empty["max_hr_observed"] is None


def test_lt_pace_computes_and_degrades_cleanly():
    assert snapshot.lt_pace_s_per_km(3.44) == 291
    assert snapshot.lt_pace_s_per_km(None) is None
    assert snapshot.lt_pace_s_per_km(0) is None


def test_zones_carry_the_threshold_history():
    runs = [{"day": "2026-09-07", "hr_z4_low": 169, "hr_z5_low": 184}]
    hist = [{"day": "2026-07-13", "lthr_bpm": 178, "lt_speed_mps": 3.53},
            {"day": "2026-07-30", "lthr_bpm": 176, "lt_speed_mps": None},
            {"day": None, "lthr_bpm": 1, "lt_speed_mps": 1.0},              # dropped
            {"day": "2026-09-07", "lthr_bpm": 176, "lt_speed_mps": 3.44}]
    z = snapshot.build_zones(runs, {"lthr_bpm": 176, "lt_speed_mps": 3.44,
                                    "lt_measured_on": "2026-09-07"}, hist, None)
    assert z["lt_history"] == [
        {"day": "2026-07-13", "lthr_bpm": 178, "lt_pace_s_per_km": 283},
        {"day": "2026-07-30", "lthr_bpm": 176, "lt_pace_s_per_km": None},
        {"day": "2026-09-07", "lthr_bpm": 176, "lt_pace_s_per_km": 291}]


def test_predictions_and_max_hr_in_zones():
    pr = snapshot.build_predictions({"day": "2026-09-07", "race_5k_s": 1450, "race_10k_s": 3065,
                                     "race_hm_s": 6915, "race_m_s": 15424})
    assert pr == {"day": "2026-09-07", "k5_s": 1450, "k10_s": 3065, "hm_s": 6915, "m_s": 15424}
    assert snapshot.build_predictions(None) == {"day": None, "k5_s": None, "k10_s": None,
                                                "hm_s": None, "m_s": None}
    runs = [{"day": "2026-09-07", "hr_z4_low": 169, "hr_z5_low": 184}]
    lt = {"lthr_bpm": 176, "lt_speed_mps": 3.44, "lt_measured_on": "2026-09-07"}
    z = snapshot.build_zones(runs, lt, [], {"max_hr": 188, "day": "2026-09-07"})
    assert z["max_hr_observed"] == 188 and z["max_hr_day"] == "2026-09-07"
    assert z["lthr_pct_max_hr"] == 94
    assert snapshot.build_zones(runs, None, None, {"max_hr": 188, "day": "x"})["lthr_pct_max_hr"] is None
    assert snapshot.build_zones(runs, lt, None, None)["lthr_pct_max_hr"] is None


# ── names, pace, band ────────────────────────────────────────────────────────

def test_name_cleaning_and_pace():
    assert snapshot._clean_name("Run\x00\nPark") == "Run  Park"
    # Garmin delivers names with interspersed U+200B — they must not split the word.
    assert snapshot._clean_name("Strength trai​ning") == "Strength training"
    assert len(snapshot._clean_name("x" * 200)) == snapshot._NAME_MAX
    assert snapshot._clean_name("") is None and snapshot._clean_name(None) is None
    assert snapshot._pace(10000, 3000) == 300.0
    assert snapshot._pace(0, 3000) is None and snapshot._pace(10000, None) is None
    assert snapshot._pace(50, 30) is None                    # under 100 m: no pace fantasy


def test_band_is_the_one_source():
    assert snapshot.band(Z_HARD) == "hard"
    assert snapshot.band(Z_EASY) == "easy"
    assert snapshot.band({"z1": 0, "z2": 1000, "z3": 800, "z4": 100, "z5": 0}) == "moderate"   # 30 % Z3
    assert snapshot.band({"z1": 0, "z2": 1400, "z3": 100, "z4": 400, "z5": 100}) == "moderate"  # 25 % hard
    assert snapshot.band({"z1": 0, "z2": 600, "z3": 0, "z4": 400, "z5": 0}) == "hard"           # exactly 40 %
    assert snapshot.band(None) is None and snapshot.band({}) is None
    assert snapshot.band({"z1": None, "z2": 0}) is None


def test_build_runs_band_only_with_detail(store):
    d = date(2026, 6, 8)
    store.upsert_activity(make_activity(1, d, name="Tempo​ run"))
    store.update_activity_detail(1, DETAIL)
    store.upsert_activity(make_activity(2, d - timedelta(days=1)))
    runs = snapshot.build_runs(store)
    assert [r["activity_id"] for r in runs] == [1, 2]
    assert runs[0]["band"] == "hard" and runs[0]["name"] == "Tempo run"
    assert runs[0]["type"] == "running" and runs[0]["pace_s_per_km"] == 400.0
    assert runs[1]["band"] is None and runs[1]["has_detail"] is False
    assert snapshot.build_runs(store, limit=1)[0]["activity_id"] == 1


# ── week plan ────────────────────────────────────────────────────────────────

def test_build_plan_maps_planned_and_done_onto_weekdays():
    today = date(2026, 9, 9)                       # Wednesday
    scheduled = [{"day": "2026-09-07", "title": "Threshold 35", "sport": "running", "workout_id": 1},
                 {"day": "2026-09-11", "title": "Easy 8k", "sport": "running", "workout_id": 2},
                 {"day": "2026-09-15", "title": "Long Run", "sport": "running", "workout_id": 3},
                 {"day": "2026-09-06", "title": "last week", "sport": "running", "workout_id": 4}]
    runs = [{"activity_id": 9, "day": "2026-09-07", "name": "Threshold run", "type": "running",
             "training_load": 265, "has_detail": True, "band": "hard", "zones_s": Z_HARD},
            {"activity_id": 8, "day": "2026-09-08", "name": "Strength", "type": "strength_training",
             "training_load": 19, "has_detail": False, "zones_s": {}},
            {"activity_id": 7, "day": "2026-09-05", "name": "old", "type": "running"}]
    p = snapshot.build_plan(scheduled, runs, today)
    assert p["week_start"] == "2026-09-07" and p["today"] == "2026-09-09"
    assert [t["day"] for t in p["days"]] == [f"2026-09-{d:02d}" for d in range(7, 14)]
    mon = p["days"][0]
    assert mon["planned"] == [{"title": "Threshold 35", "sport": "running", "workout_id": 1}]
    assert mon["done"][0]["band"] == "hard" and mon["done"][0]["training_load"] == 265
    assert p["days"][1]["done"][0]["type"] == "strength_training"
    assert p["days"][4]["planned"][0]["title"] == "Easy 8k" and p["days"][4]["done"] == []
    assert p["upcoming"] == [{"day": "2026-09-15", "title": "Long Run", "sport": "running",
                              "workout_id": 3}]
    empty = snapshot.build_plan([], [], today)
    assert len(empty["days"]) == 7 and empty["upcoming"] == []
    assert snapshot.build_plan(None, None, today)["days"][0] == {"day": "2026-09-07", "planned": [],
            "done": []}


def test_build_plan_caps_upcoming_at_six_sorted():
    today = date(2026, 9, 9)
    scheduled = [{"day": (date(2026, 9, 30) - timedelta(days=i)).isoformat(), "title": f"w{i}",
                  "sport": "running", "workout_id": i} for i in range(9)]
    up = snapshot.build_plan(scheduled, [], today)["upcoming"]
    assert len(up) == 6 and [u["day"] for u in up] == sorted(u["day"] for u in up)
    assert up[0]["day"] == "2026-09-22"


# ── decision_today ───────────────────────────────────────────────────────────

def test_one_hard_session_early_in_the_week_does_not_count_as_share_reached():
    """Tuesday tempo only: 16 of 46 measured minutes are above the easy zones.
    Measured against the running week alone that share would count as "reached"
    and Thursday's intervals would be cancelled; against a typical week (~4 h)
    the week is clearly still open."""
    today = date(2026, 9, 10)                      # Thursday → Monday 2026-09-07
    weeks = [_week(f"2026-08-{d:02d}", easy_s=11000, moderate_s=1500, hard_s=1900)
             for d in (10, 17, 24, 31)]
    weeks.append(_week("2026-09-07", easy_s=1800, moderate_s=200, hard_s=780, partial=True))
    ready = {"day": "2026-09-10", "verdict": "GO", "reasons": [], "reason_flags": [],
             "signals": {"days_since_hard_workout": 2}}
    e = snapshot.build_decision(ready, weeks, today)
    # 200 s Z3 + 780 s Z4-5 = 980 s above easy = 16 min, against 20 % of the
    # typical week (14400 s) = 48 min.
    assert e["decision"] == "hard" and e["week"] == {"hard_min": 16, "target_min": 48,
                                                     "quality_min": 13,
                                                     "week_start": "2026-09-07"}

    # Once the running week has outgrown the typical one, its own volume is the base.
    weeks[-1] = _week("2026-09-07", easy_s=13000, moderate_s=1500, hard_s=3600, partial=True)
    assert snapshot.build_decision(ready, weeks, today)["decision"] == "easy"


def test_build_decision_picks_the_running_week():
    """The referee works with the week whose Monday belongs to `today` — not the
    last row and not the whole window. Numerator/denominator: everything above the
    easy zones (Z3+Z4+Z5) against the MEASURED zone time (duration_s stays out)."""
    today = date(2026, 9, 9)                       # Wednesday → Monday 2026-09-07
    weeks = [_week("2026-08-31", easy_s=6000, moderate_s=600, hard_s=1800),            # last week: 30 min
             _week("2026-09-07", easy_s=6000, moderate_s=600, hard_s=720, partial=True)]   # 12 min
    ready = {"day": "2026-09-09", "verdict": "GO", "reasons": ["Recovery in the green"],
             "reason_flags": [], "signals": {"days_since_hard_workout": 3}}
    e = snapshot.build_decision(ready, weeks, today)
    # Above easy: 600 s Z3 + 720 s Z4-5 = 1320 s = 22 min. The running week
    # (7320 s) is still smaller than the last full week (8400 s), so the target is
    # anchored on the typical week: 8400 x 20 % / 60 = 28 min.
    assert e["week"] == {"hard_min": 22, "target_min": 28, "quality_min": 12,
                         "week_start": "2026-09-07"}
    assert e["decision"] == "hard" and e["reason"] == "GO, week below share"
    json.dumps(e)

    # Hard yesterday → the 48-hour rule beats the open week.
    ready["signals"]["days_since_hard_workout"] = 1
    e = snapshot.build_decision(ready, weeks, today)
    assert e["decision"] == "easy" and e["reason"] == "48-hour rule"

    # REST beats everything.
    ready["verdict"] = "REST"
    e = snapshot.build_decision(ready, weeks, today)
    assert e["decision"] == "rest" and e["week"]["hard_min"] == 22

    # No row for the running week (window ends earlier) → 0/0, week_start None.
    ready = {**ready, "verdict": "GO", "signals": {"days_since_hard_workout": 3}}
    e = snapshot.build_decision(ready, weeks[:1], today)
    assert e["week"] == {"hard_min": 0, "target_min": 0, "week_start": None}
    assert e["decision"] == "hard" and e["reason"] == "GO, first session of the week"


def test_build_decision_without_data_is_unknown():
    empty = {"day": None, "verdict": None, "reasons": ["No health data yet."],
             "reason_flags": [], "signals": {}}
    e = snapshot.build_decision(empty, [], date(2026, 8, 11))
    assert e["decision"] == "unknown" and e["week"]["target_min"] == 0


def test_build_decision_refuses_a_stale_verdict():
    """A decision about today backed by yesterday's verdict would be a lie."""
    today = date(2026, 9, 9)
    ready = {"day": "2026-09-08", "verdict": "GO", "signals": {"days_since_hard_workout": 3}}
    e = snapshot.build_decision(ready, [_week("2026-09-07", easy_s=6000, hard_s=720)], today)
    assert e["decision"] == "unknown"
    assert e["week"]["hard_min"] == 12                   # the weekly numbers are still reported


def test_assemble_decision_from_a_real_store(store, today):
    monday = today - timedelta(days=today.weekday())
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=85, body_battery_high=90))
    store.upsert_activity(make_activity(1, monday, aerobic_te=2.0))
    store.update_activity_detail(1, {"hr_z1_s": 600, "hr_z2_s": 5400, "hr_z3_s": 600, "hr_z4_s": 600,
                                     "hr_z5_s": 120})
    snap = snapshot.assemble(store, today=today)
    e = snap["decision_today"]
    # 7320 s zone time → target 24 min; 600 s Z3 + 720 s Z4-5 above easy → 22 min
    assert e["week"] == {"hard_min": 22, "target_min": 24, "quality_min": 12,
                         "week_start": monday.isoformat()}
    assert snap["today"]["verdict"] == "GO" and e["decision"] == "hard"


def test_a_week_of_grey_zone_running_is_not_a_week_with_a_stimulus():
    """`hard_min` counts Z3 so that a grey-zone week is not ALSO told to add
    volume. But a full load bucket is not a stimulus: with 60 min of Z3 and
    nothing above threshold, "the week's hard share is reached, no further hard
    stimulus needed" absolves the exact pattern `zones.md` calls the classic
    recreational mistake — and `tools._decision_block` hands that sentence to
    the coach as the decision it must explain rather than replace."""
    today = date(2026, 9, 10)                      # Thursday
    weeks = [_week(f"2026-08-{d:02d}", easy_s=6000, moderate_s=600, hard_s=600)
             for d in (10, 17, 24, 31)]
    # Running week: 100 min easy, 60 min Z3, ZERO above threshold.
    weeks.append(_week("2026-09-07", easy_s=6000, moderate_s=3600, hard_s=0, partial=True))
    ready = {"day": "2026-09-10", "verdict": "GO", "reasons": [], "reason_flags": [],
             "signals": {"days_since_hard_workout": 5}}
    e = snapshot.build_decision(ready, weeks, today)
    assert e["week"]["hard_min"] == 60 and e["week"]["quality_min"] == 0
    assert e["week"]["hard_min"] >= e["week"]["target_min"], "the load bucket IS full"
    assert e["decision"] == "hard", "what is missing is quality, not volume"
    assert e["reason"] == "GO, share reached but no quality"
    assert "zone 3" in e["sentence"] and "quality, not volume" in e["sentence"]

    # One genuine hard block in the same week and the rule goes quiet again.
    weeks[-1] = _week("2026-09-07", easy_s=6000, moderate_s=3600, hard_s=900, partial=True)
    e = snapshot.build_decision(ready, weeks, today)
    assert e["decision"] == "easy" and e["reason"] == "GO, share reached"

    # The recovery light still outranks it: quality missing is never a reason
    # to train hard on a red day.
    ready["verdict"] = "REST"
    weeks[-1] = _week("2026-09-07", easy_s=6000, moderate_s=3600, hard_s=0, partial=True)
    assert snapshot.build_decision(ready, weeks, today)["decision"] == "rest"


# ── aerobic efficiency ───────────────────────────────────────────────────────

def test_build_aerobic_weekly_median_of_easy_runs_only():
    today = date(2026, 9, 7)
    runs = [
        # week of Aug 31: two easy runs (median), one threshold run (out)
        {"day": "2026-09-02", "type": "running", "avg_hr": 140, "pace_s_per_km": 390,
         "distance_m": 6000, "has_detail": True, "zones_s": Z_EASY, "temperature_c": 20},
        {"day": "2026-09-05", "type": "running", "avg_hr": 142, "pace_s_per_km": 380,
         "distance_m": 9000, "has_detail": True, "zones_s": Z_EASY, "temperature_c": 22},
        {"day": "2026-09-07", "type": "running", "avg_hr": 166, "pace_s_per_km": 320,
         "distance_m": 10663, "has_detail": True, "zones_s": Z_HARD},
        # week of Jul 13: no detail — avg HR decides; 2 km is dropped; strength is dropped
        {"day": "2026-07-15", "type": "running", "avg_hr": 138, "pace_s_per_km": 400,
         "distance_m": 6000, "has_detail": False, "zones_s": {}},
        {"day": "2026-07-16", "type": "running", "avg_hr": 160, "pace_s_per_km": 300,
         "distance_m": 6000, "has_detail": False, "zones_s": {}},
        {"day": "2026-07-17", "type": "running", "avg_hr": 130, "pace_s_per_km": 500,
         "distance_m": 2000, "has_detail": False, "zones_s": {}},
        {"day": "2026-07-17", "type": "strength_training", "avg_hr": 110, "pace_s_per_km": None},
        # too old (before the 8-week window starting Monday Jul 13)
        {"day": "2026-07-10", "type": "running", "avg_hr": 130, "pace_s_per_km": 300,
         "distance_m": 6000, "has_detail": False, "zones_s": {}},
    ]
    a = snapshot.build_aerobic(runs, today=today, ref_hr=140)
    assert a["ref_hr"] == 140 and a["weeks"] == snapshot.WEEKS
    assert [p["week_start"] for p in a["points"]] == ["2026-07-13", "2026-08-31"]
    # Jul 15: 400 x 138/140 = 394.3 → 394; week of Aug 31: (390, 380 x 142/140 = 385.4) → median 388
    assert a["points"][0]["pace_s_per_km"] == 394 and a["points"][0]["n"] == 1
    assert a["points"][0]["temp_c"] is None
    assert a["points"][1]["pace_s_per_km"] == 388 and a["points"][1]["n"] == 2
    assert a["points"][1]["temp_c"] == 21.0
    assert a["current"] == 388
    # Spread instead of an endpoint difference.
    assert a["mean_s"] == 391 and a["spread_s"] == 4.2
    assert a["trend_s_per_week"] is None                  # no trend below 3 points
    empty = snapshot.build_aerobic([], today=today, ref_hr=140)
    assert empty["points"] == [] and empty["current"] is None
    assert empty["mean_s"] is None and empty["spread_s"] is None and empty["trend_s_per_week"] is None


def test_build_aerobic_trend_is_a_regression_slope():
    """The trend is a least-squares slope, not an endpoint difference — for a
    flat noisy series it must stay small against the spread."""
    paces = [416, 395, 405, 398, 406, 400, 388, 396]
    runs = []
    for i, pace in enumerate(paces):
        day = date(2026, 7, 13) + timedelta(weeks=i, days=1)
        runs.append({"day": day.isoformat(), "type": "running", "avg_hr": 140,
                     "pace_s_per_km": pace, "distance_m": 8000,
                     "has_detail": True, "zones_s": Z_EASY})
    a = snapshot.build_aerobic(runs, today=date(2026, 9, 7), ref_hr=140, weeks=10)
    assert [p["pace_s_per_km"] for p in a["points"]] == paces
    assert a["spread_s"] == 8.5
    # Slope -2.2 s/km per week → -15 s over the 8 weeks; the endpoint difference
    # would have reported -20 (the first point happens to be the maximum).
    assert a["trend_s_per_week"] == -2.2
    assert a["current"] == 396 and a["mean_s"] == 400
    assert paces[-1] - paces[0] == -20


def test_build_aerobic_scales_pace_to_the_reference_hr():
    run = {"day": "2026-09-02", "type": "running", "avg_hr": 150, "pace_s_per_km": 300,
           "distance_m": 8000, "has_detail": True, "zones_s": Z_EASY}
    assert snapshot.build_aerobic([run], today=date(2026, 9, 7), ref_hr=150)["current"] == 300
    assert snapshot.build_aerobic([run], today=date(2026, 9, 7), ref_hr=125)["current"] == 360


def test_aerobic_ref_hr_precedence():
    assert snapshot.aerobic_ref_hr({"aerobic_ref_hr": 138}, {"lthr_bpm": 175}) == 138
    assert snapshot.aerobic_ref_hr({}, {"lthr_bpm": 175}) == 140          # 80 % of the threshold
    assert snapshot.aerobic_ref_hr({}, {"lthr_bpm": 170}) == 136
    assert snapshot.aerobic_ref_hr({}, None) == 140
    assert snapshot.aerobic_ref_hr({"aerobic_ref_hr": "138"}, None) == 140  # not an int → ignored


# ── profile ──────────────────────────────────────────────────────────────────

def test_load_profile_missing_or_broken_is_empty():
    assert snapshot.load_profile() == {}
    paths.profile_path().write_text("{not json", encoding="utf-8")
    assert snapshot.load_profile() == {}


def test_profile_flows_into_the_snapshot(store, today):
    paths.profile_path().write_text(
        json.dumps({"max_hr": 190, "aerobic_ref_hr": 133, "goal": "sub-45 10k", "other": 1}),
        encoding="utf-8")
    snap = snapshot.assemble(store, today=today)
    assert snap["profile"] == {"max_hr": 190, "goal": "sub-45 10k"}
    assert snap["aerobic"]["ref_hr"] == 133


# ── degraded blocks ──────────────────────────────────────────────────────────

def test_degraded_block_is_named_instead_of_hidden(tmp_path, today, capsys):
    """If a side read fails, `soft` returns the default — and the UI would turn
    that into "nothing planned", an active claim from a broken source. The
    reader's name lands in `degraded` so the card can say "unreadable"."""
    class BrokenStore(Store):
        def get_scheduled_workouts(self, start, end):
            raise RuntimeError("no such column: day")

    snap = snapshot.assemble(BrokenStore(tmp_path / "broken.db"), today=today)
    assert snap["degraded"] == ["get_scheduled_workouts"]
    assert snap["plan"]["days"] and all(not t["planned"] for t in snap["plan"]["days"])
    assert "get_scheduled_workouts" in capsys.readouterr().err
    # a clean run afterwards: the marker must NOT stick
    assert snapshot.assemble(Store(tmp_path / "broken.db"), today=today)["degraded"] == []


def test_every_soft_read_can_degrade(tmp_path, today, capsys):
    class AllBroken(Store):
        def latest_lactate_threshold(self):
            raise RuntimeError("x")

        def lactate_threshold_history(self):
            raise RuntimeError("x")

        def max_hr_since(self, start):
            raise RuntimeError("x")

        def latest_race_predictions(self):
            raise RuntimeError("x")

    snap = snapshot.assemble(AllBroken(tmp_path / "b.db"), today=today)
    assert snap["degraded"] == ["lactate_threshold_history", "latest_lactate_threshold",
                                "latest_race_predictions", "max_hr_since"]
    assert snap["zones"]["lt_history"] == [] and snap["predictions"]["k5_s"] is None
    json.dumps(snap)


# ── one full run against the real store ──────────────────────────────────────

def test_assemble_against_real_store_is_json_serialisable(store, today):
    d = today - timedelta(days=1)
    store.upsert_daily(make_day(d, resting_hr=44, sleep_seconds=25000, sleep_score=79,
                                hrv_avg_ms=52, hrv_status="BALANCED", body_battery_high=88,
                                vo2max=48.0, acwr_ratio=1.1, acwr_status="OPTIMAL",
                                training_status="PRODUCTIVE"))
    store.upsert_lactate_history([{"day": d, "lthr_bpm": 172, "lt_speed_mps": 3.5}])
    store.update_daily_fields(d, {"race_5k_s": 1400, "race_10k_s": 2950})
    store.upsert_activity(Activity(
        activity_id=987, activity_type="running",
        start_time=datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc),
        name="Intervals in the park", distance_m=10500, duration_s=3000,
        avg_hr=158, max_hr=181, training_load=120, aerobic_te=3.8))
    store.update_activity_detail(987, {"hr_z1_s": 300, "hr_z2_s": 1200, "hr_z3_s": 600,
                                       "hr_z4_s": 600, "hr_z5_s": 300,
                                       "hr_z4_low": 158, "hr_z5_low": 172,
                                       "temperature_c": 21, "performance_condition": 2})
    store.upsert_activity_splits(987, [make_split(987, i, "INTERVAL_ACTIVE", 240, avg_hr=172)
                                       for i in range(4)])
    store.replace_scheduled_workouts(
        [ScheduledWorkout(1, today + timedelta(days=1), workout_id=5, title="Easy 8k", sport="running"),
         ScheduledWorkout(2, today + timedelta(days=9), workout_id=6, title="Long run", sport="running")],
        today - timedelta(days=7), today + timedelta(days=14))

    snap = snapshot.assemble(store, today=today)
    raw = json.dumps(snap, ensure_ascii=False)               # must not raise
    assert '"schema": 1' in raw
    run = snap["runs"][0]
    assert run["activity_id"] == 987 and run["structure"]["label"] == "4×4′"
    assert run["pace_s_per_km"] == pytest.approx(285.7, abs=0.2)
    assert run["zones_s"]["z5"] == 300 and run["has_detail"] is True
    assert run["band"] == "moderate"                          # 30 % hard, 20 % Z3
    assert snap["zones"]["z4_low"] == 158 and snap["zones"]["as_of_day"] == d.isoformat()
    assert snap["zones"]["lthr_bpm"] == 172 and snap["zones"]["lt_measured_on"] == d.isoformat()
    assert snap["zones"]["max_hr_observed"] == 181 and snap["zones"]["lthr_pct_max_hr"] == 95
    assert snap["zones"]["lt_history"] == [{"day": d.isoformat(), "lthr_bpm": 172,
                                            "lt_pace_s_per_km": 286}]
    assert snap["today"]["verdict"] in {"GO", "EASY", "REST"}
    assert snap["today"]["day"] == d.isoformat() and snap["stale_days"] == 1
    assert snap["vo2max"]["current"] == 48.0
    assert snap["load"]["acwr"] == 1.1 and snap["load"]["acwr_source"] == "garmin"
    assert snap["load"]["training_status"] == "PRODUCTIVE"
    assert snap["predictions"] == {"day": d.isoformat(), "k5_s": 1400, "k10_s": 2950,
                                   "hm_s": None, "m_s": None}
    assert snap["aerobic"]["ref_hr"] == 138                   # 80 % of the 172 threshold
    done = [x for t in snap["plan"]["days"] for x in t["done"]]
    assert [x["activity_id"] for x in done] == [987]
    planned = [x["title"] for t in snap["plan"]["days"] for x in t["planned"]]
    assert planned == ["Easy 8k"]
    assert [u["title"] for u in snap["plan"]["upcoming"]] == ["Long run"]
    assert snap["intensity"]["d28"]["total_runs"] == 1
    assert snap["counts"] == {"days": 1, "runs": 1, "weeks": snapshot.WEEKS + 1}
    assert snap["degraded"] == []


def test_runs_shown_are_capped_but_factors_read_wider(store, today):
    """With only the 30 shown runs read, the older 28-day block of the VO2max
    factor comparison was cut off — an artefact of the list, not a training change."""
    for i in range(40):
        store.upsert_activity(make_activity(1000 + i, today - timedelta(days=i + 1), distance_m=5000))
    # beyond the 56-day comparison window → the older block is covered in full
    store.upsert_activity(make_activity(2000, today - timedelta(days=60), distance_m=5000))
    store.upsert_daily(make_day(today, vo2max=50.0, steps=1))
    snap = snapshot.assemble(store, today=today)
    assert len(snap["runs"]) == snapshot.RUNS == snap["counts"]["runs"]
    f = snap["vo2max"]["factors"]
    assert f["last_28d"]["runs"] == 27 and f["prev_28d"]["runs"] == 13   # blocks are (lo, hi]
    assert f["covers_full_window"] is True
