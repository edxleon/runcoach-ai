"""Store tests against a real tmp SQLite file (no mocks)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import DETAIL, TODAY, make_activity, make_day, make_split, seed_week
from runcoach.models import Activity, DailyMetrics, ScheduledWorkout
from runcoach.store import TREND_METRICS, week_start

# ── daily metrics ────────────────────────────────────────────────────────────

def test_upsert_and_get_day(store):
    d = date(2026, 6, 1)
    store.upsert_daily(make_day(d, resting_hr=43, sleep_score=81))
    row = store.get_day(d)
    assert row["day"] == "2026-06-01"          # dates leave the store as ISO strings
    assert row["resting_hr"] == 43 and row["sleep_score"] == 81
    assert row["source"] == "garmin" and row["synced_at"]
    assert store.get_day(date(2026, 6, 2)) is None


def test_upsert_daily_is_idempotent_and_overrides(store):
    d = date(2026, 6, 1)
    store.upsert_daily(make_day(d, sleep_score=50, steps=4000))
    store.upsert_daily(make_day(d, sleep_score=88))
    assert store.counts()["days"] == 1                 # no double insert
    row = store.get_day(d)
    assert row["sleep_score"] == 88
    assert row["steps"] is None                        # a re-sync overwrites daily values


def test_latest_day(store):
    assert store.latest_day() is None
    store.upsert_daily(make_day(date(2026, 6, 1), steps=1))
    store.upsert_daily(make_day(date(2026, 6, 3), steps=1))
    assert store.latest_day() == date(2026, 6, 3)      # a `date`, not a string


def test_check_constraint_rejects_out_of_range_values(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_daily(make_day(date(2026, 6, 1), sleep_score=140))
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_daily(make_day(date(2026, 6, 1), hrv_status="EXCELLENT"))
    assert store.counts()["days"] == 0


def test_daily_sync_does_not_null_the_profile_columns(store):
    """The upsert builds its SET clause from `column_names()`, which includes the
    profile columns. `fetch_day` leaves them empty on purpose, so without
    COALESCE every daily sync would write them to NULL."""
    day = date(2026, 7, 13)
    store.upsert_daily(make_day(day, steps=100))
    store.upsert_lactate_history([{"day": day, "lthr_bpm": 178, "lt_speed_mps": 3.53}])
    store.update_daily_fields(day, {"race_5k_s": 1450})
    # Re-sync of the same day: the client does NOT deliver the profile values
    store.upsert_daily(make_day(day, steps=222, resting_hr=50))
    row = store.get_day(day)
    assert row["steps"] == 222 and row["resting_hr"] == 50      # daily values are replaced
    assert row["lthr_bpm"] == 178
    assert row["lt_speed_mps"] == 3.53
    assert row["lt_measured_on"] == day.isoformat()
    assert row["race_5k_s"] == 1450
    assert [h["day"] for h in store.lactate_threshold_history()] == [day.isoformat()]


def test_profile_fields_are_exactly_the_coalesced_columns(store):
    day = date(2026, 7, 13)
    store.upsert_daily(DailyMetrics(day=day, steps=1, lthr_bpm=170, lt_speed_mps=3.4,
                                    lt_measured_on=day, race_5k_s=1400, race_10k_s=2900,
                                    race_hm_s=6500, race_m_s=14000))
    store.upsert_daily(make_day(day, steps=2))
    row = store.get_day(day)
    assert all(row[f] is not None for f in DailyMetrics.PROFILE_FIELDS)


# ── recovery summary + trend ─────────────────────────────────────────────────

def test_recovery_summary_averages(store):
    end = date(2026, 6, 7)
    seed_week(store, end)
    s = store.get_recovery_summary(end - timedelta(days=6), end)
    assert s["days_with_data"] == 7
    assert s["averages"]["resting_hr"] == 43.0           # avg(40..46)
    assert s["averages"]["hrv_avg_ms"] == 58.0           # avg(55..61)
    assert set(s["averages"]) == set(TREND_METRICS)
    assert s["latest_hrv_status"] is None                # no status labels seeded
    assert s["latest_day"]["day"] == end.isoformat()
    assert (s["period_start"], s["period_end"]) == ("2026-06-01", "2026-06-07")


def test_recovery_summary_only_counts_the_window(store):
    end = date(2026, 6, 7)
    seed_week(store, end)
    s = store.get_recovery_summary(end - timedelta(days=2), end)
    assert s["days_with_data"] == 3
    assert s["averages"]["resting_hr"] == 41.0           # avg(40, 41, 42)


def test_recovery_summary_latest_hrv_status_skips_nulls(store):
    store.upsert_daily(make_day(date(2026, 6, 5), hrv_status="UNBALANCED"))
    store.upsert_daily(make_day(date(2026, 6, 6), hrv_status="BALANCED"))
    store.upsert_daily(make_day(date(2026, 6, 7), steps=100))          # newest day: no label
    s = store.get_recovery_summary(date(2026, 6, 1), date(2026, 6, 7))
    assert s["latest_hrv_status"] == "BALANCED"
    assert s["latest_day"]["day"] == "2026-06-07"


def test_recovery_summary_empty(store):
    s = store.get_recovery_summary(date(2020, 1, 1), date(2020, 1, 7))
    assert s["days_with_data"] == 0
    assert all(v is None for v in s["averages"].values())
    assert s["latest_day"] is None and s["latest_hrv_status"] is None


def test_trend_weekly_buckets(store):
    end = date(2026, 6, 7)                               # Sunday → one full Mon–Sun week
    seed_week(store, end)
    buckets = store.get_trend("resting_hr", end - timedelta(days=6), end)
    assert buckets == [{"week_start": "2026-06-01", "avg_value": 43.0, "days": 7}]


def test_trend_splits_on_monday_and_skips_nulls(store):
    store.upsert_daily(make_day(date(2026, 6, 6), resting_hr=40))      # Saturday
    store.upsert_daily(make_day(date(2026, 6, 7), resting_hr=42))      # Sunday
    store.upsert_daily(make_day(date(2026, 6, 8), resting_hr=50))      # Monday → next bucket
    store.upsert_daily(make_day(date(2026, 6, 9), steps=5000))         # no resting HR → not counted
    buckets = store.get_trend("resting_hr", date(2026, 6, 1), date(2026, 6, 14))
    assert buckets == [{"week_start": "2026-06-01", "avg_value": 41.0, "days": 2},
                       {"week_start": "2026-06-08", "avg_value": 50.0, "days": 1}]


def test_trend_rejects_unknown_metric(store):
    with pytest.raises(ValueError):
        store.get_trend("totally_not_a_column", date(2026, 6, 1), date(2026, 6, 7))
    with pytest.raises(ValueError):
        store.get_trend("hrv_status", date(2026, 6, 1), date(2026, 6, 7))   # a label, not a number


def test_week_start_is_monday():
    assert week_start(date(2026, 6, 10)) == date(2026, 6, 8)
    assert week_start(date(2026, 6, 8)) == date(2026, 6, 8)
    assert week_start(date(2026, 6, 14)) == date(2026, 6, 8)


# ── activities ───────────────────────────────────────────────────────────────

def test_upsert_activity_idempotent(store):
    d = date(2026, 6, 1)
    store.upsert_activity(make_activity(111, d, load=100))
    store.upsert_activity(make_activity(111, d, load=140))   # same activity_id → override
    ra = store.get_recent_activities(d, d)
    assert len(ra["recent"]) == 1
    assert ra["recent"][0]["training_load"] == 140
    assert store.counts()["activities"] == 1


def test_activity_type_lookup(store):
    store.upsert_activity(make_activity(7, date(2026, 6, 8), activity_type="strength_training"))
    assert store.activity_type(7) == "strength_training"
    assert store.activity_type(999999) is None


def test_local_day_of_a_late_evening_run(store):
    # 23:30 UTC on June 7 = 01:30 Europe/Berlin on June 8 (DST, UTC+2) → the local
    # calendar day is June 8; a naive UTC date would wrongly say June 7.
    late = datetime(2026, 6, 7, 23, 30, tzinfo=timezone.utc)
    store.upsert_activity(Activity(activity_id=700, start_time=late, activity_type="running",
                                   training_load=100, aerobic_te=3.0))
    assert len(store.get_recent_activities(date(2026, 6, 8), date(2026, 6, 8))["recent"]) == 1
    assert len(store.get_recent_activities(date(2026, 6, 7), date(2026, 6, 7))["recent"]) == 0
    assert store.get_recent_runs_detail()[0]["day"] == "2026-06-08"
    assert store.get_recent_runs_detail()[0]["start_time"] == "2026-06-07T23:30:00+00:00"


def test_local_day_follows_the_configured_timezone(store, monkeypatch):
    monkeypatch.setenv("RUNCOACH_TZ", "America/Los_Angeles")
    # 03:00 UTC on June 8 = 20:00 on June 7 in Los Angeles
    store.upsert_activity(Activity(activity_id=701, activity_type="running",
                                   start_time=datetime(2026, 6, 8, 3, 0, tzinfo=timezone.utc)))
    assert store.get_recent_runs_detail()[0]["day"] == "2026-06-07"


def test_local_day_is_rewritten_on_resync(store):
    store.upsert_activity(make_activity(1, date(2026, 6, 8)))
    store.upsert_activity(make_activity(1, date(2026, 6, 9)))       # Garmin corrected the start
    assert store.get_recent_runs_detail()[0]["day"] == "2026-06-09"


def test_recent_activities_digest(store):
    end = date(2026, 6, 7)
    store.upsert_activity(make_activity(401, end, load=120, distance_m=6000))
    store.upsert_activity(make_activity(402, end - timedelta(days=1), load=80, distance_m=5000,
                                        aerobic_te=2.0))
    store.upsert_activity(make_activity(403, end - timedelta(days=2), activity_type="strength_training",
                                        load=10, aerobic_te=0.5, distance_m=0))
    store.upsert_activity(make_activity(404, end - timedelta(days=30)))       # outside the window
    ra = store.get_recent_activities(end - timedelta(days=6), end)
    by_type = {r["activity_type"]: r for r in ra["by_type"]}
    assert [r["activity_type"] for r in ra["by_type"]] == ["running", "strength_training"]  # by load
    assert by_type["running"]["workouts"] == 2
    assert by_type["running"]["load"] == 200
    assert by_type["running"]["distance_m"] == 11000
    assert by_type["running"]["duration_s"] == 4800
    assert by_type["running"]["avg_aerobic_te"] == 2.5
    assert [r["name"] for r in ra["recent"]] == ["act-401", "act-402", "act-403"]   # newest first
    assert ra["recent"][0]["day"] == end.isoformat()
    assert (ra["window_start"], ra["window_end"]) == ("2026-06-01", "2026-06-07")


def test_recent_activities_limit(store):
    end = date(2026, 6, 7)
    for i in range(5):
        store.upsert_activity(make_activity(500 + i, end - timedelta(days=i)))
    ra = store.get_recent_activities(end - timedelta(days=6), end, limit=2)
    assert [r["name"] for r in ra["recent"]] == ["act-500", "act-501"]
    assert ra["by_type"][0]["workouts"] == 5                 # the digest is NOT limited


# ── training load ────────────────────────────────────────────────────────────

def test_training_load_acwr_computed_from_activities(store):
    # No Garmin ACWR in daily_metrics → ACWR computed from activities.
    end = date(2026, 6, 28)
    # 28 days with one workout of load 100 → chronic weekly = 2800/4 = 700; acute 7d = 700
    for i in range(28):
        store.upsert_activity(make_activity(2000 + i, end - timedelta(days=i), load=100))
    t = store.get_training_load(end, 28)
    assert t["acwr_source"] == "computed"
    assert t["acwr"] == 1.0, t
    assert t["workouts_7d"] == 7
    assert t["acute_load_7d"] == 700 and t["chronic_load_weekly"] == 700
    assert (t["window_start"], t["window_end"]) == ("2026-06-01", "2026-06-28")


def test_training_load_prefers_garmin_acwr(store):
    end = date(2026, 6, 28)
    for i in range(28):                                     # a computed value WOULD be available
        store.upsert_activity(make_activity(3000 + i, end - timedelta(days=i), load=100))
    store.upsert_daily(make_day(end, acwr_ratio=1.25, acwr_status="OPTIMAL",
                                training_status="MAINTAINING", vo2max=48))
    t = store.get_training_load(end, 28)
    assert t["acwr_source"] == "garmin"
    assert t["acwr"] == 1.25 and t["acwr_status"] == "OPTIMAL"
    assert t["training_status"] == "MAINTAINING"
    assert t["vo2max"] == 48


def test_training_load_insufficient_history_no_computed_acwr(store):
    # Only 5 days of history (<21 days, <8 workouts) → no self-computed ACWR
    # (otherwise artificially high → false REST).
    end = date(2026, 6, 28)
    for i in range(5):
        store.upsert_activity(make_activity(5000 + i, end - timedelta(days=i), load=100))
    t = store.get_training_load(end, 28)
    assert t["acwr"] is None and t["acwr_source"] is None
    assert t["acute_load_7d"] == 500                        # the raw load is still reported


def test_training_load_long_span_but_low_density(store):
    # Span >= 21 days (one old session + a fresh week) but density too low
    # (< 8 workouts) → NO computed ACWR.
    end = date(2026, 6, 28)
    store.upsert_activity(make_activity(6000, end - timedelta(days=24), load=100))
    for i in range(3):
        store.upsert_activity(make_activity(6100 + i, end - timedelta(days=i), load=150))
    assert store.get_training_load(end, 28)["acwr"] is None


def test_training_load_dense_but_short_span(store):
    # 10 workouts (>= 8) inside 10 days (< 21) → NO computed ACWR either.
    end = date(2026, 6, 28)
    for i in range(10):
        store.upsert_activity(make_activity(6200 + i, end - timedelta(days=i), load=100))
    assert store.get_training_load(end, 28)["acwr"] is None


def test_training_load_gate_opens_at_21_days_and_8_workouts(store):
    end = date(2026, 6, 28)
    store.upsert_activity(make_activity(6300, end - timedelta(days=21), load=100))   # span = 21
    for i in range(7):                                                              # 8 in total
        store.upsert_activity(make_activity(6301 + i, end - timedelta(days=i), load=100))
    t = store.get_training_load(end, 28)
    assert t["acwr_source"] == "computed"
    assert t["acwr"] == 3.0          # 700 / (800 / 4) = 3.5 → capped at 3.0


def test_training_load_vo2max_precision(store):
    """VO2max keeps its decimal — a 47.9 → 48.0 tick must not round away to 0."""
    end = date(2026, 6, 28)
    store.upsert_daily(make_day(end - timedelta(days=1), vo2max=47.9))
    store.upsert_daily(make_day(end, vo2max=48.0))
    t = store.get_training_load(end, 28)
    assert t["vo2max"] == 48.0
    assert t["vo2max_change"] == 0.1


def test_training_load_vo2max_change_none_on_single_point(store):
    """A single VO2max day in the window → change None, no invented 0.0."""
    end = date(2026, 6, 28)
    store.upsert_daily(make_day(end, vo2max=48.0))
    t = store.get_training_load(end, 28)
    assert t["vo2max"] == 48.0
    assert t["vo2max_change"] is None


def test_training_load_vo2max_change_none_on_carry_forward(store):
    """Several days, ALL with an identical VO2max (Garmin carries the value
    forward on days without a real measurement) → change None, not 0.0."""
    end = date(2026, 6, 28)
    for off in range(4):
        store.upsert_daily(make_day(end - timedelta(days=off), vo2max=48.0))
    t = store.get_training_load(end, 28)
    assert t["vo2max"] == 48.0
    assert t["vo2max_change"] is None


def test_training_load_weekly_buckets(store):
    end = date(2026, 6, 28)                                  # Sunday
    store.upsert_activity(make_activity(1, date(2026, 6, 22), load=100))          # Monday
    store.upsert_activity(make_activity(2, date(2026, 6, 28), load=50))
    store.upsert_activity(make_activity(3, date(2026, 6, 15), load=70))
    store.upsert_activity(make_activity(4, date(2026, 6, 16), load=None))         # NULL load counts as 0
    t = store.get_training_load(end, 28)
    # 28 days back from Sunday 2026-06-28 lands on Monday 2026-06-01, so no
    # bucket is cut off here. EMPTY weeks are listed as zeros, like
    # `get_weekly_volume` does: leaving them out hid a training break in the one
    # block that narrates load to the coach.
    assert t["weekly_load"] == [
        {"week_start": "2026-06-01", "load": 0, "workouts": 0, "partial": False},
        {"week_start": "2026-06-08", "load": 0, "workouts": 0, "partial": False},
        {"week_start": "2026-06-15", "load": 70, "workouts": 2, "partial": False},
        {"week_start": "2026-06-22", "load": 150, "workouts": 2, "partial": False}]


def test_a_truncated_weekly_bucket_says_so_at_both_ends(store):
    """`win_start` is almost never a Monday, so the oldest bucket usually holds
    only the tail of its week — while carrying its Monday as a label. Read as a
    full week that fabricates a ramp, in the one block that narrates training
    load to the coach.

    And the same is true of the LAST bucket, which is the running week: it is
    the newest and most influential row in that block, and four days of it
    reported as a complete week is an invented 40 % deload. An earlier version
    of this test asserted `partial is False` for exactly that bucket — it pinned
    the wrong half of the rule, while `get_weekly_volume` next door flagged both
    ends. Both now go through `_is_partial_week`."""
    end = date(2026, 6, 24)                       # Wednesday -> window opens Thu 2026-05-28
    for day, load in ((date(2026, 5, 26), 300),   # Tuesday, BEFORE the window: not counted
                      (date(2026, 5, 29), 120),   # Friday, inside the window
                      (date(2026, 6, 8), 200),    # a genuinely full week
                      (date(2026, 6, 22), 200)):  # Monday of the running week
        store.upsert_activity(make_activity(int(day.strftime("%m%d")), day, load=load))
    buckets = {w["week_start"]: w for w in store.get_training_load(end, 28)["weekly_load"]}
    assert buckets["2026-05-25"]["partial"] is True, "Mon 25 May is before the window start"
    assert buckets["2026-05-25"]["load"] == 120, "and it only holds part of that week"
    assert buckets["2026-06-08"]["partial"] is False, "a week wholly inside the window"
    assert buckets["2026-06-22"]["partial"] is True, "the running week ends on a Wednesday"

    # ...and the two surfaces agree about the same week, which is the point.
    volume = {w["week_start"]: w for w in store.get_weekly_volume(date(2026, 5, 28), end)}
    for monday in ("2026-05-25", "2026-06-08", "2026-06-22"):
        assert buckets[monday]["partial"] == volume[monday]["partial"], monday


def test_training_load_empty(store):
    t = store.get_training_load(date(2026, 6, 28), 28)
    assert t["acwr"] is None and t["weekly_load"] == [] and t["vo2max"] is None
    assert t["acute_load_7d"] == 0 and t["chronic_load_weekly"] == 0 and t["workouts_7d"] == 0
    json.dumps(t)


# ── workout detail: zones, splits, analysis ──────────────────────────────────

def test_missing_detail_and_update(store):
    d = date(2026, 6, 8)
    store.upsert_activity(make_activity(1, d))
    win = (d - timedelta(days=7), d)
    assert store.activities_missing_detail(*win) == [1]      # fresh → without detail
    store.update_activity_detail(1, DETAIL)
    assert store.activities_missing_detail(*win) == []       # detailed now
    a = store.get_workout_analysis(activity_id=1)
    assert a["zones_s"]["z4"] == 1276 and a["hr_z5_low"] == 176 and a["avg_cadence"] == 170
    assert a["temperature_c"] == 15 and a["humidity_pct"] == 72 and a["performance_condition"] == -3
    assert a["has_detail"] is True and a["day"] == "2026-06-08"


def test_activities_missing_detail_is_newest_first_capped_and_windowed(store):
    d = date(2026, 6, 8)
    for i in range(5):
        store.upsert_activity(make_activity(10 + i, d - timedelta(days=i)))
    store.upsert_activity(make_activity(99, d - timedelta(days=40)))              # outside the window
    assert store.activities_missing_detail(d - timedelta(days=7), d) == [10, 11, 12, 13, 14]
    assert store.activities_missing_detail(d - timedelta(days=7), d, limit=2) == [10, 11]


def test_summary_resync_preserves_detail(store):
    # The summary upsert must NOT reset the separately written detail columns to NULL.
    d = date(2026, 6, 8)
    store.upsert_activity(make_activity(1, d, load=100))
    store.update_activity_detail(1, DETAIL)
    store.upsert_activity(make_activity(1, d, load=999))     # re-sync with a different load
    a = store.get_workout_analysis(activity_id=1)
    assert a["training_load"] == 999
    assert a["zones_s"]["z5"] == 31                          # detail kept
    assert store.activities_missing_detail(d - timedelta(days=7), d) == []


def test_upsert_splits_full_replace_and_cascade(store):
    d = date(2026, 6, 8)
    store.upsert_activity(make_activity(1, d))
    store.upsert_activity_splits(1, [
        make_split(1, i, "INTERVAL_ACTIVE", 40, distance_m=200, avg_hr=160, max_hr=170)
        for i in range(3)])
    store.update_activity_detail(1, DETAIL)
    assert store.get_workout_analysis(activity_id=1)["split_count"] == 3
    # Full replace: 2 instead of 3 → exactly 2 remain
    store.upsert_activity_splits(1, [
        make_split(1, i, "INTERVAL_RECOVERY", 160, distance_m=400, avg_hr=150) for i in range(2)])
    assert store.get_workout_analysis(activity_id=1)["split_count"] == 2
    # An empty list clears them
    store.upsert_activity_splits(1, [])
    assert store.get_workout_analysis(activity_id=1)["split_count"] == 0
    # CASCADE: delete the workout → splits gone
    store.upsert_activity_splits(1, [make_split(1, 0, "RWD_RUN", 2400)])
    with store._conn() as conn:
        conn.execute("DELETE FROM activities WHERE activity_id = ?", (1,))
        n = conn.execute("SELECT COUNT(*) AS n FROM activity_splits").fetchone()["n"]
    assert n == 0


def test_splits_need_an_existing_activity(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_activity_splits(12345, [make_split(12345, 0, "RWD_RUN", 600)])


def test_intensity_distribution(store):
    d = date(2026, 6, 8)
    # two runs with zones: easy = 2*(100+200) = 600, hard = 2*(400+300) = 1400, Z5 = 600
    for aid in (1, 2):
        store.upsert_activity(make_activity(aid, d - timedelta(days=aid)))
        store.update_activity_detail(aid, {"hr_z1_s": 100, "hr_z2_s": 200, "hr_z3_s": 50,
                                           "hr_z4_s": 400, "hr_z5_s": 300,
                                           "hr_z4_low": 156, "hr_z5_low": 176})
    # a strength session with zone time must not count
    store.upsert_activity(make_activity(3, d, activity_type="strength_training"))
    store.update_activity_detail(3, {"hr_z1_s": 9999})
    di = store.get_intensity_distribution(d - timedelta(days=14), d)
    assert di["easy_s"] == 600 and di["moderate_s"] == 100 and di["hard_s"] == 1400
    assert di["vo2max_s"] == 600
    assert di["total_s"] == 2100 and di["with_detail"] == 2 and di["total_runs"] == 2
    assert di["vo2max_pct"] == round(100 * 600 / 2100, 1)
    assert di["easy_pct"] + di["moderate_pct"] + di["hard_pct"] == pytest.approx(100, abs=0.2)
    assert di["zones_s"] == {"z1": 200, "z2": 400, "z3": 100, "z4": 800, "z5": 600}


def test_intensity_distribution_counts_every_running_type(store):
    d = date(2026, 6, 8)
    store.upsert_activity(make_activity(1, d, activity_type="trail_running"))
    store.upsert_activity(make_activity(2, d, activity_type="treadmill_running"))
    assert store.get_intensity_distribution(d, d)["total_runs"] == 2


def test_intensity_distribution_no_detail(store):
    d = date(2026, 6, 8)
    store.upsert_activity(make_activity(1, d))               # run without detail
    di = store.get_intensity_distribution(d - timedelta(days=7), d)
    assert di["total_s"] == 0 and di["total_runs"] == 1 and di["with_detail"] == 0
    assert di["easy_pct"] is None and di["vo2max_pct"] is None


def test_workout_analysis_interval_facts(store):
    d = date(2026, 6, 8)
    store.upsert_activity(make_activity(1, d))
    store.update_activity_detail(1, DETAIL)
    store.upsert_activity_splits(1, [
        make_split(1, 0, "INTERVAL_WARMUP", 600, avg_hr=130),
        make_split(1, 1, "INTERVAL_ACTIVE", 40, avg_hr=160, max_hr=178),
        make_split(1, 2, "INTERVAL_RECOVERY", 160, avg_hr=158),
        make_split(1, 3, "INTERVAL_ACTIVE", 44, avg_hr=164, max_hr=170),
        make_split(1, 4, "INTERVAL_RECOVERY", 160, avg_hr=160),
    ])
    a = store.get_workout_analysis(activity_id=1)
    assert a["has_intervals"] and a["rep_count"] == 2
    assert a["avg_rep_duration_s"] == 42.0           # mean(40, 44)
    assert a["avg_active_hr"] == 162.0               # mean(160, 164)
    assert a["avg_recovery_hr"] == 159.0             # mean(158, 160)
    assert a["max_active_hr"] == 178
    assert a["vo2max_s"] == 31                       # Z5 seconds
    assert a["high_s"] == 1276 + 31
    assert a["structure_kind"] == "intervals" and a["structure_label"] == "2×42″"
    json.dumps(a)


def test_workout_analysis_selection_order(store):
    d = date(2026, 6, 8)
    store.upsert_activity(make_activity(1, d - timedelta(days=2)))             # older, with detail
    store.update_activity_detail(1, DETAIL)
    store.upsert_activity(make_activity(2, d - timedelta(days=1), hour=6))     # two on one day
    store.update_activity_detail(2, DETAIL)
    store.upsert_activity(make_activity(3, d - timedelta(days=1), hour=17))
    store.update_activity_detail(3, DETAIL)
    store.upsert_activity(make_activity(4, d))                                 # newest run, NO detail
    store.upsert_activity(make_activity(5, d, hour=18, activity_type="strength_training"))
    store.update_activity_detail(5, DETAIL)                                    # newest, not a run

    # 1. an explicit id wins — even without detail and even for a non-run
    assert store.get_workout_analysis(activity_id=4)["has_detail"] is False
    assert store.get_workout_analysis(activity_id=5, day=d - timedelta(days=2))["activity_id"] == 5
    assert store.get_workout_analysis(activity_id=777) is None
    # 2. else the latest RUN with detail on that day
    assert store.get_workout_analysis(day=d - timedelta(days=1))["activity_id"] == 3
    assert store.get_workout_analysis(day=d - timedelta(days=2))["activity_id"] == 1
    assert store.get_workout_analysis(day=d) is None          # only an undetailed run + strength
    # 3. else the latest run with detail at all
    assert store.get_workout_analysis()["activity_id"] == 3


def test_workout_analysis_none_when_no_detail(store):
    store.upsert_activity(make_activity(1, date(2026, 6, 8)))
    assert store.get_workout_analysis() is None


# ── readiness ────────────────────────────────────────────────────────────────

def test_readiness_empty_database(store, today):
    r = store.get_readiness()
    assert r["day"] is None and r["verdict"] is None
    assert r["reasons"] and r["reason_flags"] == [] and r["signals"] == {}


def test_readiness_verdict_from_stored_signals(store, today):
    # Seven prior nights, not five: below `RHR_BASELINE_MIN_DAYS` the baseline is
    # deliberately withheld rather than averaged out of a handful of readings.
    for i in range(1, 9):
        store.upsert_daily(make_day(today - timedelta(days=i), resting_hr=45,
                                    sleep_score=80, body_battery_high=90))
    store.upsert_daily(make_day(today, resting_hr=45, sleep_score=80, body_battery_high=90,
                                hrv_status="UNBALANCED", training_status="PRODUCTIVE"))
    r = store.get_readiness(today)
    assert r["day"] == today.isoformat()
    assert r["verdict"] == "EASY"
    assert any("HRV" in reason for reason in r["reasons"])
    assert [f["key"] for f in r["reason_flags"]] == ["hrv"]
    assert r["signals"]["resting_hr_baseline"] == 45.0
    assert r["signals"]["training_status"] == "PRODUCTIVE"
    assert r["signals"]["days_since_hard_workout"] is None
    json.dumps(r)


def test_readiness_defaults_to_today(store, today):
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=80))
    store.upsert_daily(make_day(today + timedelta(days=1), hrv_status="LOW", sleep_score=30))
    assert store.get_readiness()["day"] == today.isoformat()   # a future row is ignored


def test_readiness_baseline_excludes_the_day_itself(store, today):
    """Otherwise the current value dampens its own delta."""
    for i in range(1, 11):
        store.upsert_daily(make_day(today - timedelta(days=i), resting_hr=40))
    store.upsert_daily(make_day(today, resting_hr=51, sleep_score=85, body_battery_high=90,
                                hrv_status="BALANCED"))
    r = store.get_readiness(today)
    assert r["signals"]["resting_hr_baseline"] == 40.0        # 41.0 if today were included
    assert r["verdict"] == "REST"                             # +11 above baseline
    assert [f["key"] for f in r["reason_flags"]] == ["resting_hr"]


def test_readiness_baseline_window_is_27_days(store, today):
    store.upsert_daily(make_day(today - timedelta(days=28), resting_hr=90))    # just outside
    store.upsert_daily(make_day(today - timedelta(days=27), resting_hr=50))    # just inside
    for i in range(2, 8):                                                      # …and inside
        store.upsert_daily(make_day(today - timedelta(days=i), resting_hr=45))
    store.upsert_daily(make_day(today - timedelta(days=1), resting_hr=40))
    store.upsert_daily(make_day(today, resting_hr=45, sleep_score=85, hrv_status="BALANCED"))
    # (50 + 45*6 + 40) / 8 = 45.0 — the 90 from day 28 is outside and does not count.
    assert store.get_readiness(today)["signals"]["resting_hr_baseline"] == 45.0


def test_a_single_night_is_not_a_resting_hr_baseline(store, today):
    """Resting HR is the one signal that can raise a rest-rank flag on its own.
    With an ungated AVG() a single noisy reference night became "the baseline":
    one 40 bpm night followed by an otherwise green 48 bpm day produced REST —
    the app's headline answer, from n=1. Reachable on any fresh install, because
    `runcoach sync` defaults to three days."""
    store.upsert_daily(make_day(today - timedelta(days=1), resting_hr=40))
    store.upsert_daily(make_day(today, resting_hr=48, sleep_score=82,
                                hrv_status="BALANCED", body_battery_high=88))
    r = store.get_readiness(today)
    assert r["signals"]["resting_hr_baseline"] is None
    assert r["verdict"] != "REST"
    assert not any("Resting HR" in reason for reason in r["reasons"])

    # With a real sample the flag works exactly as before.
    for i in range(2, 9):
        store.upsert_daily(make_day(today - timedelta(days=i), resting_hr=40))
    r = store.get_readiness(today)
    assert r["signals"]["resting_hr_baseline"] == 40.0
    assert r["verdict"] == "REST"


def test_readiness_is_anchored_on_the_data_day_not_on_today(store, today):
    """With a stale sync the latest data row is days old. Baseline, ACWR and
    days-since-hard must all be measured from THAT day."""
    data_day = today - timedelta(days=4)
    for i in range(1, 8):
        store.upsert_daily(make_day(data_day - timedelta(days=i), resting_hr=40))
    store.upsert_daily(make_day(data_day, resting_hr=50, sleep_score=85, body_battery_high=90,
                                hrv_status="BALANCED", acwr_ratio=1.0))
    store.upsert_activity(make_activity(1, data_day - timedelta(days=2), aerobic_te=3.5))
    # A hard workout AFTER the data day must not shorten the distance.
    store.upsert_activity(make_activity(2, today - timedelta(days=1), aerobic_te=4.0))

    r = store.get_readiness()
    assert r["day"] == data_day.isoformat()
    assert r["signals"]["resting_hr_baseline"] == 40.0        # the data day is not in its own baseline
    assert r["signals"]["days_since_hard_workout"] == 2       # from the data day, not from today
    assert r["signals"]["acwr"] == 1.0 and r["signals"]["acwr_source"] == "garmin"


def test_readiness_ignores_rows_without_any_recovery_signal(store, today):
    store.upsert_daily(make_day(today - timedelta(days=1), hrv_status="LOW", sleep_score=80))
    store.upsert_daily(make_day(today, steps=3000, vo2max=50.0))               # steps only
    r = store.get_readiness(today)
    assert r["day"] == (today - timedelta(days=1)).isoformat()
    assert r["verdict"] == "REST"


@pytest.mark.parametrize("te", [dict(aerobic_te=3.0, anaerobic_te=0.0),
                                dict(aerobic_te=1.0, anaerobic_te=3.2),
                                dict(aerobic_te=2.8, anaerobic_te=2.4)])   # quality by anaerobic TE
def test_readiness_days_since_hard_counts_aerobic_or_anaerobic(store, today, te):
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=85))
    store.upsert_activity(make_activity(1, today - timedelta(days=3), **te))
    store.upsert_activity(make_activity(2, today - timedelta(days=1), aerobic_te=2.9, anaerobic_te=1.9))
    assert store.get_readiness(today)["signals"]["days_since_hard_workout"] == 3


def test_readiness_days_since_hard_uses_the_local_day(store, today):
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=85))
    # 23:30 UTC the day before yesterday = 01:30 local time YESTERDAY
    late = datetime.combine(today - timedelta(days=2), datetime.min.time(),
                            tzinfo=timezone.utc).replace(hour=23, minute=30)
    store.upsert_activity(Activity(activity_id=1, start_time=late, activity_type="running",
                                   aerobic_te=4.0))
    assert store.get_readiness(today)["signals"]["days_since_hard_workout"] == 1


def test_readiness_and_training_load_share_one_acwr(store, today):
    for i in range(28):
        store.upsert_activity(make_activity(100 + i, today - timedelta(days=i), load=100,
                                            aerobic_te=2.0))
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=85, body_battery_high=90))
    r = store.get_readiness(today)
    t = store.get_training_load(today, 28)
    assert (r["signals"]["acwr"], r["signals"]["acwr_source"]) == (t["acwr"], t["acwr_source"])
    assert r["signals"]["acwr_source"] == "computed"


# ── lactate threshold history ────────────────────────────────────────────────

def test_lactate_history_is_update_only(store):
    """Measurement points land as an UPDATE on the rows of their measurement
    days — never as a new row (a ghost row without daily metrics)."""
    for d in (date(2026, 7, 13), date(2026, 7, 16), date(2026, 9, 7)):
        store.upsert_daily(make_day(d, steps=100))
    n = store.upsert_lactate_history([
        {"day": date(2026, 7, 13), "lthr_bpm": 178, "lt_speed_mps": 3.53},
        {"day": date(2026, 7, 16), "lthr_bpm": 176, "lt_speed_mps": 3.47},
        {"day": date(2026, 8, 1), "lthr_bpm": 170, "lt_speed_mps": 3.0},    # no day row → nothing
        {"day": None, "lthr_bpm": 170},
    ])
    assert n == 2
    assert store.get_day(date(2026, 8, 1)) is None
    assert store.counts()["days"] == 3
    assert [(h["day"], h["lthr_bpm"], h["lt_speed_mps"]) for h in store.lactate_threshold_history()] == [
        ("2026-07-13", 178, 3.53), ("2026-07-16", 176, 3.47)]
    assert store.get_day(date(2026, 7, 13))["steps"] == 100                 # the rest of the row is untouched


def test_lactate_history_is_idempotent(store):
    store.upsert_daily(make_day(date(2026, 7, 13), steps=100))
    point = {"day": date(2026, 7, 13), "lthr_bpm": 178, "lt_speed_mps": 3.53}
    assert store.upsert_lactate_history([point]) == 1
    assert store.upsert_lactate_history([point]) == 0
    assert store.upsert_lactate_history([{**point, "lthr_bpm": 177}]) == 1  # a correction is written
    assert store.lactate_threshold_history()[0]["lthr_bpm"] == 177


def test_lactate_history_partial_point_keeps_the_other_value(store):
    store.upsert_daily(make_day(date(2026, 7, 13), steps=100))
    store.upsert_lactate_history([{"day": date(2026, 7, 13), "lthr_bpm": 178, "lt_speed_mps": 3.53}])
    store.upsert_lactate_history([{"day": date(2026, 7, 13), "lthr_bpm": None, "lt_speed_mps": 3.6}])
    h = store.lactate_threshold_history()
    assert h == [{"day": "2026-07-13", "lthr_bpm": 178, "lt_speed_mps": 3.6}]


def test_latest_lactate_threshold(store):
    assert store.latest_lactate_threshold() is None
    for d in (date(2026, 7, 13), date(2026, 9, 7)):
        store.upsert_daily(make_day(d, steps=100))
    store.upsert_lactate_history([
        {"day": date(2026, 9, 7), "lthr_bpm": 176, "lt_speed_mps": 3.44},
        {"day": date(2026, 7, 13), "lthr_bpm": 178, "lt_speed_mps": 3.53}])
    lt = store.latest_lactate_threshold()
    assert lt == {"lthr_bpm": 176, "lt_speed_mps": 3.44, "lt_measured_on": "2026-09-07",
                  "seen_on": "2026-09-07"}


# ── update_daily_fields ──────────────────────────────────────────────────────

def test_update_daily_fields_allowlist_and_no_ghost_row(store):
    day = date(2026, 9, 7)
    store.upsert_daily(make_day(day, steps=100))
    # (1) allowlisted columns get through
    assert store.update_daily_fields(day, {"race_5k_s": 1450, "race_10k_s": 3065}) == 1
    rp = store.latest_race_predictions()
    assert rp["race_5k_s"] == 1450 and rp["race_10k_s"] == 3065
    # (2) fields OUTSIDE the allowlist are dropped, never interpolated into SQL
    assert store.update_daily_fields(day, {"steps": 999}) == 0
    assert store.update_daily_fields(day, {"race_5k_s = 1; DROP TABLE daily_metrics; --": 1}) == 0
    assert store.get_day(day)["steps"] == 100
    # mixed: only the allowed column takes effect
    assert store.update_daily_fields(day, {"race_hm_s": 6915, "steps": 1}) == 1
    assert store.get_day(day)["steps"] == 100 and store.get_day(day)["race_hm_s"] == 6915
    # (3) without an existing day row NO ghost row appears
    assert store.update_daily_fields(date(2026, 9, 30), {"race_5k_s": 1400}) == 0
    assert store.get_day(date(2026, 9, 30)) is None
    assert store.update_daily_fields(day, {}) == 0
    assert store.update_daily_fields(day, None) == 0


def test_update_daily_fields_drops_none_instead_of_nulling(store):
    day = date(2026, 9, 7)
    store.upsert_daily(make_day(day, steps=100))
    store.update_daily_fields(day, {"race_5k_s": 1450, "race_m_s": 15000})
    assert store.update_daily_fields(day, {"race_5k_s": None, "race_m_s": 14900}) == 1
    row = store.get_day(day)
    assert row["race_5k_s"] == 1450 and row["race_m_s"] == 14900
    assert store.update_daily_fields(day, {"race_5k_s": None}) == 0           # nothing left to set


def test_latest_race_predictions_picks_the_newest_day(store):
    assert store.latest_race_predictions() is None
    store.upsert_daily(make_day(date(2026, 9, 6), steps=1, race_5k_s=1460, race_10k_s=3100,
                                race_hm_s=7000, race_m_s=15600))
    store.upsert_daily(make_day(date(2026, 9, 7), steps=1, race_5k_s=1450, race_10k_s=3065,
                                race_hm_s=6915, race_m_s=15424))
    store.upsert_daily(make_day(date(2026, 9, 8), steps=1))                   # no prediction
    rp = store.latest_race_predictions()
    assert rp["day"] == "2026-09-07" and rp["race_10k_s"] == 3065


def test_max_hr_since(store):
    store.upsert_activity(Activity(activity_id=1, activity_type="running", max_hr=191,
                                   start_time=datetime(2026, 7, 1, 8, tzinfo=timezone.utc)))
    store.upsert_activity(Activity(activity_id=2, activity_type="running", max_hr=188,
                                   start_time=datetime(2026, 9, 7, 6, 22, tzinfo=timezone.utc)))
    store.upsert_activity(Activity(activity_id=3, activity_type="running", max_hr=None,
                                   start_time=datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)))
    assert store.max_hr_since(date(2026, 6, 15)) == {"max_hr": 191, "day": "2026-07-01"}
    assert store.max_hr_since(date(2026, 8, 1)) == {"max_hr": 188, "day": "2026-09-07"}
    assert store.max_hr_since(date(2026, 9, 8)) is None


# ── scheduled workouts ───────────────────────────────────────────────────────

def test_scheduled_workouts_full_replace_inside_the_window(store):
    store.replace_scheduled_workouts(
        [ScheduledWorkout(1, date(2026, 8, 20), title="old"),
         ScheduledWorkout(2, date(2026, 9, 7), title="Threshold", sport="running"),
         ScheduledWorkout(3, date(2026, 9, 9), title="Easy")],
        date(2026, 8, 20), date(2026, 9, 21))
    # New sync with a narrower window: entry 3 is gone in Garmin, 1 lies outside
    n = store.replace_scheduled_workouts(
        [ScheduledWorkout(2, date(2026, 9, 7), workout_id=55, title="Threshold 35", sport="running")],
        date(2026, 9, 1), date(2026, 9, 21))
    assert n == 1
    got = store.get_scheduled_workouts(date(2026, 8, 1), date(2026, 9, 30))
    assert [(g["schedule_id"], g["day"], g["title"], g["workout_id"]) for g in got] == [
        (1, "2026-08-20", "old", None), (2, "2026-09-07", "Threshold 35", 55)]


def test_scheduled_workouts_ignores_items_outside_the_window(store):
    n = store.replace_scheduled_workouts(
        [ScheduledWorkout(1, date(2026, 9, 1), title="in"),
         ScheduledWorkout(2, date(2026, 10, 1), title="out")],
        date(2026, 9, 1), date(2026, 9, 21))
    assert n == 1
    assert [g["schedule_id"] for g in store.get_scheduled_workouts(date(2026, 1, 1), date(2026, 12,
            31))] == [1]


def test_scheduled_workouts_empty_list_clears_the_window(store):
    store.replace_scheduled_workouts([ScheduledWorkout(1, date(2026, 9, 7), title="x")],
                                     date(2026, 9, 1), date(2026, 9, 21))
    assert store.replace_scheduled_workouts([], date(2026, 9, 1), date(2026, 9, 21)) == 0
    assert store.get_scheduled_workouts(date(2026, 9, 1), date(2026, 9, 21)) == []


def test_scheduled_workout_moved_to_another_day(store):
    # The same schedule id reappears on a different day outside the old window.
    store.replace_scheduled_workouts([ScheduledWorkout(1, date(2026, 9, 7), title="x")],
                                     date(2026, 9, 1), date(2026, 9, 10))
    store.replace_scheduled_workouts([ScheduledWorkout(1, date(2026, 9, 15), title="x")],
                                     date(2026, 9, 11), date(2026, 9, 21))
    got = store.get_scheduled_workouts(date(2026, 9, 1), date(2026, 9, 30))
    assert [(g["schedule_id"], g["day"]) for g in got] == [(1, "2026-09-15")]


# ── series reads (web app) ───────────────────────────────────────────────────

def test_get_daily_series_has_gaps_not_zeros(store):
    """Missing days stay AWAY (not filled with 0) — the UI draws a gap instead
    of claiming a collapse."""
    store.upsert_daily(make_day(date(2026, 6, 1), resting_hr=46, vo2max=50.0))
    store.upsert_daily(make_day(date(2026, 6, 3), resting_hr=48, vo2max=None))
    rows = store.get_daily_series(date(2026, 6, 1), date(2026, 6, 5))
    assert [r["day"] for r in rows] == ["2026-06-01", "2026-06-03"]
    assert rows[0]["vo2max"] == 50.0 and rows[1]["vo2max"] is None
    json.dumps(rows)


def test_get_recent_runs_detail_joins_splits_and_limits(store):
    d = date(2026, 6, 8)
    store.upsert_activity(make_activity(1, d))
    store.update_activity_detail(1, DETAIL)
    store.upsert_activity_splits(1, [
        make_split(1, 0, "INTERVAL_ACTIVE", 240, avg_hr=170, max_hr=182),
        make_split(1, 1, "INTERVAL_RECOVERY", 120, avg_hr=138)])
    store.upsert_activity(make_activity(2, d - timedelta(days=1)))             # without splits
    runs = store.get_recent_runs_detail(limit=10)
    assert [r["activity_id"] for r in runs] == [1, 2]                          # newest first
    assert runs[0]["structure"]["kind"] == "intervals"
    assert runs[0]["structure"]["label"] == "1×4′"
    assert runs[0]["zones_s"]["z5"] == DETAIL["hr_z5_s"]
    assert runs[0]["has_detail"] is True
    assert runs[1]["structure"]["kind"] == "unknown"       # no splits → no claim
    assert runs[1]["has_detail"] is False
    assert runs[1]["zones_s"] == {f"z{i}": None for i in range(1, 6)}
    assert runs[0]["day"] == "2026-06-08"
    assert "local_day" not in runs[0] and "synced_at" not in runs[0]
    json.dumps(runs)
    assert [r["activity_id"] for r in store.get_recent_runs_detail(limit=1)] == [1]
    assert store.get_recent_runs_detail(limit=0) == []


def test_get_weekly_volume_distance_and_zones(store):
    mon = date(2026, 6, 8)
    store.upsert_activity(make_activity(1, mon, distance_m=10700, duration_s=3000, load=120))
    store.update_activity_detail(1, DETAIL)
    store.upsert_activity(make_activity(2, mon + timedelta(days=2), distance_m=8000,
                                        duration_s=2400, load=90))
    store.upsert_activity(make_activity(3, mon + timedelta(days=1), activity_type="strength_training",
                                        distance_m=0, duration_s=1800, load=40))
    weeks = store.get_weekly_volume(mon, mon + timedelta(days=6))
    assert len(weeks) == 1
    w = weeks[0]
    assert w["week_start"] == mon.isoformat()
    assert w["distance_m"] == 18700
    assert w["duration_s"] == 7200
    assert w["runs"] == 2 and w["workouts"] == 3          # strength counts as a workout, not a run
    assert w["load"] == 250
    assert w["easy_s"] == DETAIL["hr_z1_s"] + DETAIL["hr_z2_s"]
    assert w["moderate_s"] == DETAIL["hr_z3_s"]
    assert w["hard_s"] == DETAIL["hr_z4_s"] + DETAIL["hr_z5_s"]
    assert w["z5_s"] == DETAIL["hr_z5_s"]
    assert w["partial"] is False
    json.dumps(weeks)


def test_get_weekly_volume_fills_empty_weeks(store):
    """A week without a session is a RESULT, not a missing measurement — it
    must arrive as a zero row. Otherwise a two-week break becomes invisible and
    the reported weekly average is too high."""
    mon = date(2026, 6, 1)
    store.upsert_activity(make_activity(1, mon, distance_m=10000))
    store.upsert_activity(make_activity(2, mon + timedelta(days=21), distance_m=8000))
    weeks = store.get_weekly_volume(mon, mon + timedelta(days=27))
    assert [w["week_start"] for w in weeks] == ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22"]
    assert [w["distance_m"] for w in weeks] == [10000, 0, 0, 8000]
    empty = weeks[1]
    assert empty["runs"] == 0 and empty["workouts"] == 0 and empty["load"] == 0
    assert empty["easy_s"] == 0 and empty["z5_s"] == 0
    assert all(w["partial"] is False for w in weeks)      # full weeks: not flagged


def test_get_weekly_volume_flags_partial_weeks(store):
    # The window starts MID-week: the bucket carries the Monday date but only
    # holds the days from the window start on — the row is cut and flagged.
    wed = date(2026, 6, 3)
    weeks = store.get_weekly_volume(wed, wed + timedelta(days=6))
    assert [(w["week_start"], w["partial"]) for w in weeks] == [
        ("2026-06-01", True), ("2026-06-08", True)]
    # The running week (window ends today, a Wednesday) is partial at the END.
    mon = date(2026, 6, 1)
    running = store.get_weekly_volume(mon, mon + timedelta(days=9))
    assert [(w["week_start"], w["partial"]) for w in running] == [
        ("2026-06-01", False), ("2026-06-08", True)]


def test_get_weekly_volume_empty_rows_are_independent(store):
    mon = date(2026, 6, 1)
    weeks = store.get_weekly_volume(mon, mon + timedelta(days=13))
    weeks[0]["runs"] = 99                                  # mutating one row must not leak
    assert weeks[1]["runs"] == 0
    assert store.get_weekly_volume(mon, mon + timedelta(days=13))[0]["runs"] == 0


def test_today_fixture_matches_constant(today):
    from runcoach import paths

    assert paths.today() == TODAY == today
