"""Tool handlers: compact English text, empty and error paths never raise."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import DETAIL, FakeGarmin, make_activity, make_day, make_split, own_ids
from runcoach import demo, garmin, sync, tools
from runcoach.models import ScheduledWorkout
from runcoach.store import CLAIM_STALE_MINUTES, Store
from runcoach.tools import _hm


@pytest.fixture(autouse=True)
def _reset_sync_cooldown():
    """`tools.sync_garmin`'s cooldown is process-global on purpose - it throttles
    an AGENT, which gets a fresh MCP process per job, not a caller. So it has to
    be cleared between tests.

    Here rather than in `conftest.py`: reaching into one production module's
    private state from a fixture shared by sixteen files made every test file
    depend on `tools`, whether it used it or not."""
    from runcoach import tools

    tools._last_sync[0] = None
    yield
    tools._last_sync[0] = None


#: Everything the MCP server offers that only READS. The write tools live in
#: `tools.WRITE_TOOLS`; the two together have to be the whole registry.
READ_ONLY_TOOLS = {
    "get_training_readiness", "get_recovery_summary", "get_daily_metrics", "get_trend",
    "get_training_load", "get_recent_activities", "get_intensity_distribution",
    "analyze_workout", "get_vo2max_history", "sync_garmin",
    "propose_workout", "propose_week"}

EXPECTED_MCP_TOOLS = {
    "get_training_readiness", "get_recovery_summary", "get_daily_metrics", "get_trend",
    "get_training_load", "get_recent_activities", "get_intensity_distribution",
    "analyze_workout", "get_vo2max_history", "sync_garmin",
    # the write path: propose (local) and, after a human's yes, apply (Garmin)
    "propose_workout", "propose_week", "apply_workout", "undo_workout"}


def test_hm_formatting():
    assert _hm(None) == "-"
    assert _hm(0) == "-"
    assert _hm(24060) == "6h 41m"
    assert _hm(3600) == "1h 00m"
    assert _hm(2340) == "39m"          # under an hour: no "0h"
    assert _hm(3660) == "1h 01m"


# ── recovery / daily / trend ─────────────────────────────────────────────────

def test_recovery_summary_empty_returns_hint(store, today):
    out = tools.get_recovery_summary(store, period_days=7)
    assert out.startswith("No data") and "sync_garmin" in out


def test_recovery_summary_populated(store, today):
    for i in range(3):
        store.upsert_daily(make_day(today - timedelta(days=i), sleep_seconds=23400, sleep_score=80,
                                    resting_hr=44, hrv_avg_ms=60, stress_avg=30,
                                    body_battery_high=90, body_battery_low=20, steps=12000,
                                    hrv_status="BALANCED"))
    out = tools.get_recovery_summary(store, period_days=7)
    assert out.startswith("Recovery 2026-06-04 .. 2026-06-10 (3 days with data)")
    assert "Resting HR avg: 44.0 bpm" in out       # the average is a float
    assert "resting HR 44 bpm" in out              # the latest-day snapshot is an integer
    assert "Sleep avg: 6h 30m" in out and "Steps avg: 12,000" in out
    assert "HRV status (latest): BALANCED" in out
    assert "Latest day (2026-06-10)" in out and "Body Battery 20-90" in out


def test_recovery_summary_clamps_the_window(store, today):
    old = today - timedelta(days=89)
    store.upsert_daily(make_day(old, resting_hr=44))
    assert "(1 days with data)" in tools.get_recovery_summary(store, period_days=5000)
    # period_days=0 is clamped to one day, and the window ends on the analysis
    # anchor - the last day that carries a signal, not on today.
    assert f"Recovery {old} .. {old}" in tools.get_recovery_summary(store, period_days=0)


def test_daily_metrics_no_data(store):
    assert tools.get_daily_metrics(store) == "No data in the database yet."
    store.upsert_daily(make_day(date(2026, 6, 1), steps=1))
    assert tools.get_daily_metrics(store, "2020-01-01") == "No data for 2020-01-01."


def test_daily_metrics_invalid_date(store):
    out = tools.get_daily_metrics(store, "not-a-date")
    assert "Invalid date" in out and "YYYY-MM-DD" in out


def test_daily_metrics_default_latest(store):
    d = date(2026, 6, 1)
    store.upsert_daily(make_day(d, resting_hr=43, sleep_seconds=24060, sleep_score=81,
                                hrv_avg_ms=58, hrv_status="BALANCED", steps=16213))
    out = tools.get_daily_metrics(store)           # no day → the latest one
    assert out.splitlines()[0] == "2026-06-01"
    assert "Resting HR: 43 bpm" in out
    assert "Sleep: 6h 41m (score 81)" in out
    assert "HRV: 58 ms (BALANCED)" in out and "Steps: 16213" in out


def test_trend_invalid_metric_message(store, today):
    out = tools.get_trend(store, "bogus_metric", period_days=28)
    assert "unknown metric 'bogus_metric'" in out and "resting_hr" in out


def test_trend_empty(store, today):
    assert "No data for 'resting_hr'" in tools.get_trend(store, "resting_hr")


def test_trend_populated(store, today):
    for i in range(7):
        store.upsert_daily(make_day(today - timedelta(days=i), resting_hr=40 + i))
    out = tools.get_trend(store, "resting_hr", period_days=14)
    assert "Trend `resting_hr`" in out
    # TODAY is a Wednesday: Mon–Wed (40, 41, 42) and Thu–Sun of the week before (43..46)
    assert "- week of 2026-06-08: 41.0 bpm (3 days)" in out
    assert "- week of 2026-06-01: 44.5 bpm (4 days)" in out


def test_trend_formats_durations(store, today):
    store.upsert_daily(make_day(today, sleep_seconds=27000))
    assert "7h 30m (1 days)" in tools.get_trend(store, "sleep_seconds", period_days=7)


# ── readiness ────────────────────────────────────────────────────────────────

def test_training_readiness_empty(store, today):
    assert "No data yet" in tools.get_training_readiness(store)


def test_training_readiness_render(store, today):
    for i in range(1, 9):     # enough nights for a real resting-HR baseline
        store.upsert_daily(make_day(today - timedelta(days=i), resting_hr=44))
    store.upsert_daily(make_day(today, hrv_status="UNBALANCED", sleep_score=80,
                                body_battery_high=90, resting_hr=45, training_status="PRODUCTIVE"))
    store.upsert_activity(make_activity(1, today - timedelta(days=2), aerobic_te=3.4))
    out = tools.get_training_readiness(store)
    assert out.startswith("[EASY] Readiness 2026-06-10: EASY")
    assert "Reasons: HRV unbalanced" in out
    assert "resting HR 45 bpm (baseline 44.0)" in out
    assert "status PRODUCTIVE" in out and "last hard workout 2 day(s) ago" in out
    assert "NOTE" not in out
    assert out.endswith("Garmin calendar: nothing scheduled in the next 7 days.")


def test_training_readiness_lists_the_garmin_calendar(store, today):
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=85))
    store.replace_scheduled_workouts(
        [ScheduledWorkout(1, today, title="Threshold 3x10", sport="running"),
         ScheduledWorkout(2, today + timedelta(days=6), title=None),
         ScheduledWorkout(3, today + timedelta(days=7), title="beyond the 7 days"),
         ScheduledWorkout(4, today - timedelta(days=1), title="yesterday")],
        today - timedelta(days=7), today + timedelta(days=14))
    out = tools.get_training_readiness(store)
    assert ('Garmin calendar, next 7 days (untrusted labels): TODAY "Threshold 3x10" [schedule 1]; '
            '2026-06-16 "?" [schedule 2]') in out
    assert "beyond" not in out and "yesterday" not in out


def test_training_readiness_flags_stale_data(store, today):
    store.upsert_daily(make_day(today - timedelta(days=2), hrv_status="BALANCED", sleep_score=85))
    out = tools.get_training_readiness(store)
    assert "[GO] Readiness 2026-06-08" in out
    assert "NOTE: latest data is from 2026-06-08, not today" in out and "stale" in out


# ── training load / recent activities ────────────────────────────────────────

def test_training_load_empty(store, today):
    assert "No training data" in tools.get_training_load(store, 28)


def test_training_load_render(store, today):
    store.upsert_activity(make_activity(77, today, load=120, aerobic_te=3.1))
    store.upsert_daily(make_day(today - timedelta(days=1), vo2max=47.9))
    store.upsert_daily(make_day(today, acwr_ratio=1.2, acwr_status="OPTIMAL",
                                training_status="MAINTAINING", vo2max=48.0))
    out = tools.get_training_load(store, 28)
    assert out.startswith("Training load 2026-05-14 .. 2026-06-10")
    assert "- Status: MAINTAINING" in out
    assert "- VO2max: 48.0 (+0.1 in window)" in out
    assert "ACWR (acute:chronic): 1.2 [garmin] / OPTIMAL" in out
    assert "acute 7d 120" in out and "1 workouts/7d" in out
    assert "- week of 2026-06-08: load 120 (1 workouts)" in out
    assert "Lactate threshold" not in out


def test_training_load_prints_the_lactate_threshold(store, today):
    store.upsert_activity(make_activity(77, today, load=120))
    store.upsert_daily(make_day(today - timedelta(days=5), steps=1))
    store.upsert_lactate_history([{"day": today - timedelta(days=5), "lthr_bpm": 172,
                                   "lt_speed_mps": 3.5}])
    out = tools.get_training_load(store, 28)
    assert "- Lactate threshold (Garmin, measured 2026-06-05): 172 bpm / 4:46 per km" in out


def test_training_load_without_vo2max_change(store, today):
    store.upsert_activity(make_activity(77, today, load=120))
    store.upsert_daily(make_day(today, vo2max=48.0, steps=1))
    out = tools.get_training_load(store, 28)
    assert "- VO2max: 48.0" in out and "in window" not in out
    assert "ACWR" not in out                       # neither Garmin's nor enough history


def test_recent_activities_empty(store, today):
    assert tools.get_recent_activities(store, 14).startswith("No workouts")


def test_recent_activities_render(store, today):
    store.upsert_activity(make_activity(88, today, distance_m=6100, avg_hr=150, load=120,
                                        aerobic_te=3.2, anaerobic_te=0.4))
    store.upsert_activity(make_activity(89, today - timedelta(days=1), activity_type="strength_training",
                                        distance_m=None, duration_s=1800, load=15, aerobic_te=None))
    out = tools.get_recent_activities(store, 14)
    assert out.startswith("Workouts 2026-05-28 .. 2026-06-10")
    assert "- running: 1x / load 120 / 6.1 km / 40m, avg aerobic TE 3.2" in out
    assert "- 2026-06-10 running (Quality): 6.1 km/40m / load 120 TE 3.2/0.4 / 150 bpm" in out
    assert "- 2026-06-09 strength_training: -/30m / load 15" in out


def test_recent_activities_tags_long_run(store, today):
    """End to end: an 80-minute Z2 run shows up in the digest as a Long Run."""
    store.upsert_activity(make_activity(89, today, distance_m=10100, duration_s=4800, avg_hr=136,
                                        load=48, aerobic_te=2.4, anaerobic_te=0.0))
    store.upsert_activity(make_activity(90, today - timedelta(days=1), aerobic_te=3.9,
                                        anaerobic_te=2.6))
    out = tools.get_recent_activities(store, 14)
    assert "running (Long Run)" in out and "running (Quality)" in out


# ── intensity distribution ───────────────────────────────────────────────────

def test_intensity_distribution_no_runs(store, today):
    assert tools.get_intensity_distribution(store).startswith("No runs")


def test_intensity_distribution_no_detail_hint(store, today):
    store.upsert_activity(make_activity(1, today))          # run without detail
    out = tools.get_intensity_distribution(store, period_days=28)
    assert "No zone detail yet" in out and "1 run(s) without detail" in out


def test_intensity_distribution_grey_zone_notes(store, today):
    # Six runs, all with detail: enough of a sample for the notes to speak.
    for i in range(1, 7):
        store.upsert_activity(make_activity(i, today - timedelta(days=i)))
        # a lot of Z3 (grey zone), hardly any Z5
        store.update_activity_detail(i, {"hr_z1_s": 60, "hr_z2_s": 60, "hr_z3_s": 1200,
                                         "hr_z4_s": 300, "hr_z5_s": 20})
    out = tools.get_intensity_distribution(store, period_days=28)
    assert "Intensity distribution" in out and "(6/6 runs with detail)" in out
    assert "Moderate (Z3):   2h 00m (73.2%)" in out
    assert "grey zone" in out and "little true easy running (7.3% vs ~80%)" in out
    assert "hardly any time in Z5 (1.2%)" in out


def test_intensity_distribution_says_nothing_on_a_thin_sample(store, today):
    """Only runs WITH zone detail carry seconds. Judging the training from one
    detailed run out of twenty would be a verdict on the backfill."""
    for i in range(1, 21):
        store.upsert_activity(make_activity(i, today - timedelta(days=i)))
    store.update_activity_detail(1, {"hr_z1_s": 60, "hr_z2_s": 60, "hr_z3_s": 200,
                                     "hr_z4_s": 900, "hr_z5_s": 600})
    out = tools.get_intensity_distribution(store, period_days=28)
    assert "only 1 of 20 runs carry zone detail" in out
    assert "little true easy running" not in out and "Rule-based notes" not in out


def test_intensity_distribution_polarised(store, today):
    for i in range(1, 7):
        store.upsert_activity(make_activity(i, today - timedelta(days=i)))
        store.update_activity_detail(i, {"hr_z1_s": 600, "hr_z2_s": 3000, "hr_z3_s": 200,
                                         "hr_z4_s": 300, "hr_z5_s": 300})
    out = tools.get_intensity_distribution(store)
    assert "Looks polarised" in out and "Rule-based notes" not in out


# ── analyze_workout ──────────────────────────────────────────────────────────

def test_analyze_workout_short_reps_notes(store, today):
    store.upsert_activity(make_activity(1, today, avg_hr=150, max_hr=178))
    store.update_activity_detail(1, DETAIL)
    store.upsert_activity_splits(1, [
        make_split(1, 0, "INTERVAL_ACTIVE", 40, avg_hr=160, max_hr=178),
        make_split(1, 1, "INTERVAL_RECOVERY", 160, avg_hr=158),
        make_split(1, 2, "INTERVAL_ACTIVE", 44, avg_hr=164, max_hr=170)])
    out = tools.analyze_workout(store)
    assert out.startswith("Analysis 2026-06-10 - running 6.0 km/40m")
    assert '"act-1" (untrusted label)' in out
    assert "HR avg/max: 150/178 bpm / cadence 170" in out
    assert "VO2max range (>= 176 bpm): 31s" in out           # under a minute: seconds, not "0m"
    assert "- Garmin zone bounds on this run: Z4 from 156 bpm, Z5 from 176 bpm" in out
    # The rep count is labelled as Garmin's auto-detection (not the planned
    # structure) so the coach never plays it as fact against the athlete.
    assert "Intervals (Garmin auto-detection" in out
    assert "2x ~42s (avg work HR 162.0, avg recovery HR 158.0)" in out
    assert "reps short (~42s)" in out
    assert "recoveries too hot" in out                       # recovery 158 close to work 162
    assert "a lot of hard time but only" in out


def test_analyze_workout_vo2max_rep_length_and_cool_recoveries(store, today):
    store.upsert_activity(make_activity(1, today))
    store.update_activity_detail(1, {"hr_z1_s": 300, "hr_z2_s": 900, "hr_z3_s": 300,
                                     "hr_z4_s": 600, "hr_z5_s": 600})
    splits = []
    for i in range(5):
        splits.append(make_split(1, 2 * i, "INTERVAL_ACTIVE", 240, avg_hr=172))
        splits.append(make_split(1, 2 * i + 1, "INTERVAL_RECOVERY", 150, avg_hr=140))
    store.upsert_activity_splits(1, splits)
    out = tools.analyze_workout(store)
    assert "5x ~4m" in out and "rep length (~4 min) suits VO2max work" in out
    assert "too hot" not in out and "a lot of hard time" not in out


def test_analyze_workout_steady_run(store, today):
    store.upsert_activity(make_activity(1, today))
    store.update_activity_detail(1, {"hr_z1_s": 60, "hr_z2_s": 2000, "hr_z3_s": 300})
    store.upsert_activity_splits(1, [make_split(1, 0, "RWD_RUN", 2400, avg_hr=140)])
    out = tools.analyze_workout(store)
    assert "Steady run (no interval structure)." in out and "Intervals" not in out
    assert "zone bounds" not in out                           # none delivered → no line


def test_analyze_workout_context_line(store, today):
    """The context line renders ambient temperature/humidity and the performance
    condition — labelled "median" because it is the per-run median, not the
    momentary value the watch shows."""
    store.upsert_activity(make_activity(1, today))
    store.update_activity_detail(1, {"hr_z1_s": 60, "hr_z2_s": 900, "hr_z3_s": 600, "hr_z4_s": 0,
                                     "hr_z5_s": 0, "temperature_c": 15, "humidity_pct": 72,
                                     "performance_condition": -3})
    out = tools.analyze_workout(store, today.isoformat())
    assert "- Context: 15 C/72% rh / performance condition (median) -3" in out


def test_analyze_workout_positive_performance_condition_has_a_plus(store, today):
    store.upsert_activity(make_activity(1, today))
    store.update_activity_detail(1, {"hr_z1_s": 60, "hr_z2_s": 900, "temperature_c": 12,
                                     "performance_condition": 4})
    out = tools.analyze_workout(store, today.isoformat())
    assert "12 C / performance condition (median) +4" in out


def test_analyze_workout_context_omitted_when_null(store, today):
    store.upsert_activity(make_activity(1, today))
    store.update_activity_detail(1, {"hr_z1_s": 60, "hr_z2_s": 900, "hr_z3_s": 600})
    assert "Context" not in tools.analyze_workout(store, today.isoformat())


def test_analyze_workout_by_id_and_by_day(store, today):
    store.upsert_activity(make_activity(1, today - timedelta(days=3), distance_m=5000))
    store.update_activity_detail(1, DETAIL)
    store.upsert_activity(make_activity(2, today, distance_m=9000))
    store.update_activity_detail(2, DETAIL)
    assert "9.0 km" in tools.analyze_workout(store)
    assert "5.0 km" in tools.analyze_workout(store, activity_id=1)
    assert "5.0 km" in tools.analyze_workout(store, day=(today - timedelta(days=3)).isoformat())


def test_analyze_workout_no_detail_hint(store, today):
    assert "No run with detail data found" in tools.analyze_workout(store)
    assert "for 2026-06-01" in tools.analyze_workout(store, day="2026-06-01")


def test_analyze_workout_bad_date(store):
    assert "Invalid date" in tools.analyze_workout(store, day="nope")


# ── VO2max history ───────────────────────────────────────────────────────────

def test_vo2max_history_empty(store, today):
    assert tools.get_vo2max_history(store) == "No VO2max values yet."


def test_vo2max_history_on_demo_data(tmp_path, today):
    out = tools.get_vo2max_history(demo.seed(tmp_path / "demo.db"))
    assert out.startswith("VO2max ") and "(as of 2026-06-10)" in out
    assert "carries the value forward" in out
    assert "Last 28 days vs the 28 before (descriptive, not causal):" in out
    assert "- last 28d:" in out and "- prev 28d:" in out
    assert "NOTE: the older block" not in out                # 12 weeks of data cover both blocks


def test_vo2max_history_steps_and_uncovered_block(store, today):
    for i in range(10):
        store.upsert_daily(make_day(today - timedelta(days=i), vo2max=50.0 if i < 4 else 49.6))
    store.upsert_activity(make_activity(1, today - timedelta(days=2), distance_m=8000))
    out = tools.get_vo2max_history(store)
    assert out.startswith("VO2max 50.0")
    assert "- 2026-06-07: 50.0" in out                       # the ONE day the value changed
    assert out.count("\n- 2026-") == 1
    assert "- last 28d: 1 runs, 8.0 km" in out
    assert "NOTE: the older block is NOT fully covered" in out


# ── sync_garmin ──────────────────────────────────────────────────────────────

def test_sync_garmin_login_failure_returns_hint_no_raise(store, monkeypatch):
    def boom(tokenstore=None):
        raise RuntimeError("token expired")

    monkeypatch.setattr(garmin, "login", boom)
    out = tools.sync_garmin(store, days=1)
    assert "Garmin login failed (RuntimeError)" in out and "runcoach login" in out


def test_sync_garmin_reports_a_rate_limit_without_losing_the_report(store, today, monkeypatch):
    """A fatal in a side channel used to re-raise out of `run()`, so the days that
    WERE written went unreported. Now the agent is told both: what landed, and
    that Garmin stopped us."""
    class Throttled(FakeGarmin):
        def get_lactate_threshold(self, **kw):
            raise garmin._FATAL[-1]("429")

    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: Throttled())
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    out = tools.sync_garmin(store)
    assert "day(s)" in out and "Aborted by Garmin" in out and "429" in out


def test_sync_garmin_returns_text_when_the_fetch_itself_raises(store, monkeypatch):
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: FakeGarmin())
    monkeypatch.setattr(sync, "run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = tools.sync_garmin(store)
    assert "not possible right now" in out and "retry later" in out


def test_sync_garmin_runs_a_small_window(store, today, monkeypatch):
    client = FakeGarmin(user_summary={"restingHeartRate": 44})
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: client)
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    out = tools.sync_garmin(store, days=50)                  # capped at 7
    assert "7 day(s)" in out and "0 error(s)" in out
    assert store.counts()["days"] == 7


def test_sync_garmin_is_disabled_on_demo_data(store, monkeypatch):
    monkeypatch.setenv("RUNCOACH_DEMO", "1")
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: (_ for _ in ()).throw(AssertionError))
    out = tools.sync_garmin(store)
    assert "synthetic demo athlete" in out
    # Tool output is data the agent reads: no imperative phrasing in it, or a careful
    # agent rightly reports it as an embedded instruction.
    assert "do not" not in out.lower()


# ── MCP transport ────────────────────────────────────────────────────────────

def test_mcp_server_registers_exactly_the_expected_tools():
    from runcoach import mcp_server

    registered = asyncio.run(mcp_server.mcp.list_tools())
    assert {t.name for t in registered} == EXPECTED_MCP_TOOLS
    assert len(registered) == len(EXPECTED_MCP_TOOLS)
    assert all((t.description or "").strip() for t in registered)


# ── propose / apply ──────────────────────────────────────────────────────────

def _seed_zones(store, today):
    """A run with Garmin zone bounds and a measured threshold, so proposals
    carry the athlete's numbers instead of assumptions."""
    store.upsert_activity(make_activity(9_000_000_001, today - timedelta(days=2)))
    store.update_activity_detail(9_000_000_001, {**DETAIL, "splits": [], "unknown": set()})
    d = today - timedelta(days=5)
    store.upsert_daily(make_day(d))
    store.upsert_lactate_history([{"day": d, "lthr_bpm": 168, "lt_speed_mps": 3.5}])


def test_propose_files_a_preview_and_writes_nothing_to_garmin(store, today, monkeypatch):
    from runcoach import plan

    _seed_zones(store, today)
    logins = []
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: logins.append(1))
    out = tools.propose_workout(store, "vo2max", distance_km=10)
    assert "warmup" in out and "HR zone 5" in out and "NOT on Garmin yet" in out
    assert "assumption" not in out, "zones were seeded - nothing to assume"
    pid = out.split("proposal ")[1].split()[0]
    p = plan.read(pid)
    assert p and p["status"] == "open" and p["day"] == today.isoformat()
    assert logins == [], "proposing must not even log in"


def test_apply_uploads_schedules_pushes_verifies_and_refreshes_the_mirror(store, today, monkeypatch):
    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin()
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    pid = tools.propose_workout(store, "threshold", distance_km=10).split("proposal ")[1].split()[0]

    out = tools.apply_workout(store, pid)
    assert "On Garmin" in out and "verified" in out, out
    p = plan.read(pid)
    (it,) = p["items"]
    assert p["status"] == "applied" and it["workout_id"] and it["schedule_id"]
    assert it["workout_id"] in own_ids(store), "what we uploaded is recorded as ours"
    assert fake.data["pushed"] == [it["workout_id"]]
    mirror = store.get_scheduled_workouts(today, today)
    assert [m["workout_id"] for m in mirror] == [it["workout_id"]], "Today tab sees it now"

    # once only
    again = tools.apply_workout(store, pid)
    assert "already applied" in again and len(fake.data["library"]) == 1


def test_apply_with_an_unknown_id_lists_the_open_proposals(store, today, monkeypatch):
    _seed_zones(store, today)
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: FakeGarmin())
    pid = tools.propose_workout(store, "easy", distance_km=8).split("proposal ")[1].split()[0]
    out = tools.apply_workout(store, "p-20260101-000000-dead")
    assert "no proposal" in out and pid in out


def test_apply_reports_a_mismatch_instead_of_hiding_it(store, today, monkeypatch):
    """Garmin stored something else than what was sent - the read-back is the
    only place that can notice, and it must say so, not claim success."""
    _seed_zones(store, today)

    class Mangling(FakeGarmin):
        def get_workout_by_id(self, workout_id):
            dto = super().get_workout_by_id(workout_id)
            import copy
            dto = copy.deepcopy(dto)
            dto["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][1]["targetType"] = {
                "workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}
            return dto

    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: Mangling())
    pid = tools.propose_workout(store, "vo2max", distance_km=10).split("proposal ")[1].split()[0]
    out = tools.apply_workout(store, pid)
    assert "MISMATCH" in out and "recovery carries a target" in out


def test_the_write_tool_the_agent_is_denied_really_exists():
    """`agent.WRITE_TOOL` is a string on a command line. If the tool were renamed,
    the flag would deny nothing and every card run could write to Garmin."""
    from runcoach import mcp_server
    from runcoach.web import agent

    registered = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert agent.WRITE_TOOL == "mcp__runcoach__apply_workout"
    assert agent.WRITE_TOOL.split("__")[-1] in registered


def test_apply_in_demo_mode_refuses(store, monkeypatch):
    monkeypatch.setenv("RUNCOACH_DEMO", "1")
    assert "Demo mode" in tools.apply_workout(store, "p-20260101-000000-dead")


# ── a week as one package, and the readiness swap ───────────────────────────

def test_propose_week_files_one_package_and_writes_nothing(store, today, monkeypatch):
    from runcoach import plan

    _seed_zones(store, today)
    logins = []
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: logins.append(1))
    out = tools.propose_week(store, days_per_week=4, long_run_day="sun")
    assert "4 sessions" in out and out.count("Long run") == 1 and "NOT on Garmin yet" in out
    assert "HR zone 5" in out and "HR zone 4" in out, "both quality sessions, with their targets"
    pid = out.split("proposal ")[1].split()[0]
    p = plan.read(pid)
    assert p["days"] == 4 and len(p["items"]) == 4 and p["status"] == "open"
    assert logins == []


def test_propose_week_reads_the_profile_and_names_what_it_had_to_assume(store, today, tmp_path):
    from runcoach import paths

    _seed_zones(store, today)
    out = tools.propose_week(store)
    assert "assumption: 4 running days" in out and "assumption: long run on sun" in out
    paths.profile_path().write_text('{"days_per_week": 5, "long_run_day": "sat"}', encoding="utf-8")
    out = tools.propose_week(store)
    assert "5 sessions" in out and "assumption: long run" not in out
    assert "assumption: 4 running" not in out


def test_propose_week_refuses_a_start_outside_the_calendar_mirror(store, today):
    out = tools.propose_week(store, start_day=(today + timedelta(days=20)).isoformat())
    assert out.startswith("Cannot build that week") and "mirrors from the Garmin calendar" in out
    assert "not-a-date" in tools.propose_week(store, start_day="not-a-date")


def test_apply_puts_the_whole_week_on_garmin_after_one_yes(store, today, monkeypatch):
    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin()
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    pid = tools.propose_week(store, start_day=today.isoformat(), days_per_week=4,
                             long_run_day="sun").split("proposal ")[1].split()[0]
    out = tools.apply_workout(store, pid)
    assert out.count("On Garmin:") == 4 and "verified" in out, out
    p = plan.read(pid)
    wids = plan.workouts_of(p)
    assert len(wids) == 4 and all(w in own_ids(store) for w in wids)
    assert all(it["schedule_id"] for it in p["items"])
    assert fake.data["pushed"] == wids
    mirror = store.get_scheduled_workouts(today, today + timedelta(days=14))
    assert {m["workout_id"] for m in mirror} == set(wids), "the Today tab sees the week"
    assert "already applied" in tools.apply_workout(store, pid) and len(fake.data["library"]) == 4


def test_a_package_whose_first_upload_fails_stays_open_and_owns_nothing(store, today, monkeypatch):
    from runcoach import plan

    class Down(FakeGarmin):
        def upload_running_workout(self, _w):
            raise ConnectionError("garmin is down")

    _seed_zones(store, today)
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: Down())
    pid = tools.propose_week(store, start_day=today.isoformat()).split("proposal ")[1].split()[0]
    out = tools.apply_workout(store, pid)
    assert "upload failed" in out and "garmin is down" in out
    assert plan.read(pid)["status"] == "open" and store.own_workouts() == []


def test_the_readiness_tool_prints_the_schedule_id_the_swap_needs(store, today):
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=85))
    store.replace_scheduled_workouts([ScheduledWorkout(777, today, workout_id=4242,
                                                       title="VO2max 5x3", sport="running")],
                                     today, today)
    assert 'TODAY "VO2max 5x3" [schedule 777]' in tools.get_training_readiness(store)


def test_a_swap_proposal_unschedules_the_old_entry_when_applied(store, today, monkeypatch):
    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin(schedule={777: {"workoutId": 4242, "date": today.isoformat()}})
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    store.replace_scheduled_workouts([ScheduledWorkout(777, today, workout_id=4242,
                                                       title="VO2max 5x3", sport="running")],
                                     today, today)
    out = tools.propose_workout(store, "easy", duration_min=40, replaces_schedule_id=777)
    assert 'replaces on the calendar: ' + today.isoformat() + ' "VO2max 5x3" (schedule 777)' in out
    pid = out.split("proposal ")[1].split()[0]
    assert plan.read(pid)["replaces"][0]["workout_id"] == 4242

    out = tools.apply_workout(store, pid)
    assert 'Removed from the calendar: "VO2max 5x3"' in out and "stays in your library" in out
    assert 777 not in fake.data["schedule"], "unscheduled, not deleted"
    assert 4242 not in fake.data.get("deleted", []), "the athlete's workout is not ours to delete"
    mirror = store.get_scheduled_workouts(today, today)
    assert [m["workout_id"] for m in mirror] == plan.workouts_of(plan.read(pid))


def test_a_swap_may_only_name_an_entry_the_mirror_knows(store, today, monkeypatch):
    _seed_zones(store, today)
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: FakeGarmin())
    out = tools.propose_workout(store, "easy", duration_min=40, replaces_schedule_id=999)
    assert out.startswith("Cannot build") and "not on the calendar the app knows" in out


# ── two yeses at the same moment, and what a half-applied package can do ─────

def test_two_applies_at_the_same_moment_upload_the_session_once(store, today, monkeypatch):
    """A click on the card and a yes in a Claude Code session are two processes
    on one proposal file. Both used to read `status == "open"` and upload, and
    the athlete got the same session twice on the watch with the app knowing
    about one. The claim decides the race; the loser says so."""
    import threading

    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin()
    p = plan.propose(store, "vo2max", distance_km=10, today=today)

    # Both threads are held AT the claim, so they race for it the way two
    # processes do. What decides the race is the real INSERT, not this barrier.
    # Generous: the barrier only ever waits for the sibling thread, which is
    # microseconds away when both are alive. Ten seconds was tight enough to
    # break under a loaded full-suite run and made this test flake.
    both_in = threading.Barrier(2, timeout=60)
    real_claim = store.claim_proposal_item

    def claim(proposal_id, index):
        # Only the SESSION claim - the race under test. Waiting on every claim
        # deadlocked once the scheduling step got one of its own.
        if index == 0:
            both_in.wait()
        return real_claim(proposal_id, index)

    monkeypatch.setattr(store, "claim_proposal_item", claim)
    results, errors = [], []

    def run():
        try:
            results.append(plan.apply(store, fake, p["id"], today=today))
        except Exception as exc:    # noqa: BLE001 — a thread's failure must reach the assert
            errors.append(exc)
            both_in.abort()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)

    assert not errors, errors
    assert len(fake.data.get("library", {})) == 1, "the session went up exactly once"
    assert len(store.own_workouts()) == 1
    assert len(store.proposal_items(p["id"])) == 1, "one claim, one session"
    # The apply that lost says so in its own answer. NOT asserted on the file:
    # two processes merging warnings into one JSON can still lose one of them
    # to a read-modify-write, and that is the honest limit of a file here. What
    # must never be lost is the id, and that comes from the claim table.
    assert sum(any("another apply" in w for w in r.get("warnings", [])) for r in results) >= 1, (
        "the apply that found the session already claimed says so")
    # ...and neither saved its own stale copy over the other's result.
    after = plan.read(p["id"])
    assert after["status"] == "applied" and plan.workouts_of(after) == list(fake.data["library"])


def test_a_claim_is_given_back_when_the_upload_created_nothing(store, today, monkeypatch):
    """The claim serialises two applies; it is not an idempotency key against
    Garmin. A refused upload created nothing, so the proposal stays retryable."""
    from runcoach import plan

    _seed_zones(store, today)
    p = plan.propose(store, "vo2max", distance_km=10, today=today)

    class Down(FakeGarmin):
        def upload_running_workout(self, _w):
            raise ConnectionError("garmin is down")

    out = plan.apply(store, Down(), p["id"], today=today)
    assert "upload failed" in out["error"]
    assert store.proposal_items(p["id"]) == {}, "the claim was handed back"

    fake = FakeGarmin()
    again = plan.apply(store, fake, p["id"], today=today)
    assert not again.get("error") and plan.workouts_of(again), "the retry goes through"


def test_a_half_applied_week_is_finished_by_applying_it_again(store, today, monkeypatch):
    """Three of four sessions on Garmin used to be the end of the road: the
    proposal counted as applied, and the only way to the fourth was proposing
    the week again - which would have uploaded the first three a second time."""
    from runcoach import plan

    class Flaky(FakeGarmin):
        def upload_running_workout(self, w):
            if len(self.data.get("library", {})) == 2 and not self.data.get("hiccuped"):
                self.data["hiccuped"] = True
                raise ConnectionError("hiccup")
            return super().upload_running_workout(w)

    _seed_zones(store, today)
    fake = Flaky()
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    pid = tools.propose_week(store, start_day=today.isoformat(), days_per_week=4,
                             long_run_day="sun").split("proposal ")[1].split()[0]
    first = tools.apply_workout(store, pid)
    assert first.count("On Garmin:") == 3 and "apply the proposal again" in first

    second = tools.apply_workout(store, pid)
    assert second.count("On Garmin:") == 4, "the missing day is picked up, the others are not"
    p = plan.read(pid)
    assert len(plan.workouts_of(p)) == 4 and len(fake.data["library"]) == 4
    assert sorted(store.proposal_items(pid)) == [0, 1, 2, 3]
    assert "already applied" in tools.apply_workout(store, pid), "and now it is done"


def test_a_swap_is_checked_again_at_apply_time_not_only_when_proposed(store, today, monkeypatch):
    """A proposal may sit for a week. Garmin hands calendar ids out again, so
    unscheduling one on the strength of a days-old check is how the app would
    delete an entry the athlete made in the meantime."""
    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin(schedule={777: {"workoutId": 4242, "date": today.isoformat()}})
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    store.replace_scheduled_workouts([ScheduledWorkout(777, today, workout_id=4242,
                                                       title="VO2max 5x3", sport="running")],
                                     today, today)
    pid = tools.propose_workout(store, "easy", duration_min=40,
                                replaces_schedule_id=777).split("proposal ")[1].split()[0]

    # ...and by the time the athlete says yes, 777 belongs to something else.
    store.replace_scheduled_workouts([ScheduledWorkout(777, today, workout_id=5150,
                                                       title="Parkrun with friends",
                                                       sport="running")], today, today)
    out = tools.apply_workout(store, pid)
    assert "did NOT remove schedule 777" in out and "no longer shows the entry" in out
    # ...and the summary does not claim the opposite two lines higher up. The
    # first version of this test asserted only the warning, and the summary
    # said "Removed from the calendar" right above it.
    assert "Removed from the calendar" not in out
    assert 777 in fake.data["schedule"], "the athlete's entry is still there"
    assert plan.workouts_of(plan.read(pid)), "the easy session was still written"


# ── the swap the decision itself argues for ─────────────────────────────────

def _readiness_with(store, today, decision, entries):
    """`decision` is what the app should conclude for today: "rest" (a red
    morning), "easy" (green light, but the last hard session was today - the
    48-hour rule) or "hard" (green, and room for it)."""
    green = decision != "rest"
    store.upsert_daily(make_day(today, hrv_status="BALANCED" if green else "POOR",
                                sleep_score=85 if green else 48,
                                resting_hr=45 if green else 57))
    if decision == "easy":
        store.upsert_activity(make_activity(9_000_000_777, today, aerobic_te=3.6))
    store.replace_scheduled_workouts(entries, today, today)
    return tools.get_training_readiness(store)


def test_the_readiness_tool_names_the_entry_the_decision_argues_against(store, today):
    """Which session to replace is decided from the RECORD - `runcoach_workouts`
    knows the kind of everything this app uploaded. The titles above it are the
    athlete's free text, and a coach reading only those is guessing twice."""
    store.record_workout(900001, name="VO2max 4x4 min", kind="vo2max",
                         spec_json="{}", schedule_id=777, scheduled_day=today)
    out = _readiness_with(store, today, "rest",
                          [ScheduledWorkout(777, today, workout_id=900001,
                                            title="Tuesday run", sport="running")])
    assert "decision argues against what is scheduled" in out
    assert "schedule 777" in out and "uploaded by this app" in out
    assert "not run" in out, "a rest day is not a day for a replacement session"


def test_a_foreign_entry_is_named_as_a_guess_and_an_easy_day_offers_the_swap(store, today):
    out = _readiness_with(store, today, "easy",
                          [ScheduledWorkout(778, today, workout_id=4242,
                                            title="VO2max 5x3 min", sport="running")])
    assert "schedule 778" in out and "guessed from its name" in out
    assert 'replaces_schedule_id' in out


def test_a_green_day_says_nothing_about_swapping(store, today):
    out = _readiness_with(store, today, "hard",
                          [ScheduledWorkout(779, today, workout_id=4242,
                                            title="VO2max 5x3 min", sport="running")])
    assert "argues against" not in out


def test_an_own_easy_run_is_not_swapped_because_its_name_sounds_hard(store, today):
    """The app knows what it built. A name is data."""
    store.record_workout(900002, name="VO2max recovery jog", kind="easy",
                         spec_json="{}", schedule_id=780, scheduled_day=today)
    out = _readiness_with(store, today, "easy",
                          [ScheduledWorkout(780, today, workout_id=900002,
                                            title="VO2max recovery jog", sport="running")])
    assert "argues against" not in out


def _age_claims(store, proposal_id: str, *, minutes: int) -> None:
    """Backdate a proposal's claims, so the reaper's clock can be tested
    without one."""
    import sqlite3
    from datetime import timedelta as _td

    when = (datetime.now(timezone.utc) - _td(minutes=minutes)).isoformat()
    conn = sqlite3.connect(store.path)
    try:
        conn.execute("UPDATE proposal_items SET claimed_at = ? WHERE proposal_id = ?",
                     (when, proposal_id))
        conn.commit()
    finally:
        conn.close()


def _age_proposal(proposal_id: str, *, days: int) -> None:
    import json as _json
    from datetime import timedelta as _td

    from runcoach import plan as _plan

    f = _plan._path(proposal_id)
    d = _json.loads(f.read_text(encoding="utf-8"))
    d["created"] = (datetime.now(timezone.utc) - _td(days=days)).isoformat(timespec="seconds")
    f.write_text(_json.dumps(d), encoding="utf-8")


# ── what the summary is allowed to claim ────────────────────────────────────
#
# One entry per way a step of `plan.apply` can fail. The assertion is always
# the same shape and it is the NEGATIVE one: after this failure, the sentence
# that would claim the step happened must not be in the summary. Written as a
# table rather than as five tests because the sixth failure branch is the one
# that will be added without a test - the summary used to read the wording of
# a warning to decide what to claim, and reported a calendar entry as removed
# in the same breath as the warning saying it was not.

class _ScheduleRefused(FakeGarmin):
    def schedule_workout(self, workout_id, date_str):
        raise ConnectionError("no route")


class _ScheduleSilent(FakeGarmin):
    """Garmin answers 200 with a body that carries no calendar id."""

    def schedule_workout(self, workout_id, date_str):
        return {}


class _PushRefused(FakeGarmin):
    def push_workout_to_device(self, workout_id=None, device_id=None):
        raise ConnectionError("watch offline")


class _ReadBackRefused(FakeGarmin):
    def get_workout_by_id(self, workout_id):
        raise ConnectionError("gateway")


class _UnscheduleRefused(FakeGarmin):
    def unschedule_workout(self, scheduled_workout_id):
        raise ConnectionError("no route")


#: (label, client, the AFFIRMATIVE fragment that must be gone, what must appear
#: instead). The forbidden fragment is the exact affirmative wording, not a
#: loose substring: "pushed to the watch" also occurs inside "Not pushed to the
#: watch", and a test that forbids the short form passes on a summary that is
#: already correct while failing on one that is too.
_CLAIMS_MUST_VANISH = [
    ("schedule refused", _ScheduleRefused, ") scheduled for", "NOT scheduled"),
    ("schedule answered without an id", _ScheduleSilent, None, "no calendar id"),
    ("watch refused the push", _PushRefused, ", pushed to the watch.", "Not pushed"),
    ("read-back refused", _ReadBackRefused, "and verified", "could not read the workout back"),
]


@pytest.mark.parametrize("label,client,forbidden,expected",
                         _CLAIMS_MUST_VANISH, ids=[c[0] for c in _CLAIMS_MUST_VANISH])
def test_the_summary_drops_the_claim_whose_step_failed(store, today, monkeypatch,
                                                       label, client, forbidden, expected):
    _seed_zones(store, today)
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: client())
    pid = tools.propose_workout(store, "vo2max", distance_km=10).split("proposal ")[1].split()[0]
    out = tools.apply_workout(store, pid)
    assert expected in out, f"{label}: the failure is not reported at all\n{out}"
    if forbidden:
        assert forbidden not in out, f"{label}: the summary still claims it\n{out}"


def test_the_summary_drops_the_removal_claim_when_the_unschedule_failed(store, today, monkeypatch):
    from runcoach import plan

    _seed_zones(store, today)
    fake = _UnscheduleRefused(schedule={777: {"workoutId": 4242, "date": today.isoformat()}})
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    store.replace_scheduled_workouts([ScheduledWorkout(777, today, workout_id=4242,
                                                       title="VO2max 5x3", sport="running")],
                                     today, today)
    pid = tools.propose_workout(store, "easy", duration_min=40,
                                replaces_schedule_id=777).split("proposal ")[1].split()[0]
    out = tools.apply_workout(store, pid)
    assert "could not remove" in out and "Removed from the calendar" not in out
    assert plan.read(pid)["replaces"][0]["removed"] is False


def test_an_unschedule_that_happened_is_not_attempted_twice(store, today, monkeypatch):
    """The entry is gone after the first apply. A resume that tried again would
    aim a delete at a calendar id Garmin may have handed to something else -
    and the early exit on a failed upload used to throw the record of it away."""
    from runcoach import plan

    class Flaky(FakeGarmin):
        def upload_running_workout(self, w):
            if not self.data.get("tried"):
                self.data["tried"] = True
                raise ConnectionError("garmin is down")
            return super().upload_running_workout(w)

    _seed_zones(store, today)
    fake = Flaky(schedule={777: {"workoutId": 4242, "date": today.isoformat()}})
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    store.replace_scheduled_workouts([ScheduledWorkout(777, today, workout_id=4242,
                                                       title="VO2max 5x3", sport="running")],
                                     today, today)
    pid = tools.propose_workout(store, "easy", duration_min=40,
                                replaces_schedule_id=777).split("proposal ")[1].split()[0]

    first = tools.apply_workout(store, pid)
    assert "upload failed" in first
    # The removal HAPPENED and is on the record, even though the upload after
    # it did not: the proposal file was saved before the first upload.
    assert 777 not in fake.data["schedule"]
    assert plan.read(pid)["replaces"][0]["removed"] is True

    unscheduled = []
    fake.unschedule_workout = lambda sid: unscheduled.append(sid)
    second = tools.apply_workout(store, pid)
    assert unscheduled == [], "the entry was already removed; do not touch that id again"
    assert "On Garmin" in second and "Removed from the calendar" in second


# ── claims that outlive their apply ─────────────────────────────────────────

def test_a_claim_from_a_crashed_apply_is_reaped_so_the_session_can_be_written(store, today):
    """A process that dies between the claim and the upload leaves a row that
    nothing completes. Without a reaper the session is unappliable for good and
    the card keeps offering a button that reports 'another apply is handling
    it' - about a process that no longer exists."""
    from runcoach import plan

    _seed_zones(store, today)
    p = plan.propose(store, "vo2max", distance_km=10, today=today)
    assert store.claim_proposal_item(p["id"], 0)
    assert plan.apply(store, FakeGarmin(), p["id"], today=today)["items"][0]["workout_id"] is None

    _age_claims(store, p["id"], minutes=CLAIM_STALE_MINUTES + 1)
    out = plan.apply(store, FakeGarmin(), p["id"], today=today)
    assert plan.workouts_of(out) , "after the reaper the session goes up"


def test_an_upload_whose_answer_was_lost_keeps_its_claim_and_is_reported(store, today):
    """A read timeout does not prove Garmin created nothing. Handing the claim
    back would let the next apply upload the same session again - so it stays,
    out of the reaper's reach, and `doctor` asks the human."""
    from runcoach import plan

    class LostAnswer(FakeGarmin):
        def upload_running_workout(self, w):
            raise TimeoutError("read timed out")

    _seed_zones(store, today)
    p = plan.propose(store, "vo2max", distance_km=10, today=today)
    out = plan.apply(store, LostAnswer(), p["id"], today=today)
    assert "unclear whether Garmin created it" in " ".join(out["warnings"])
    assert [u["proposal_id"] for u in store.unresolved_claims()] == [p["id"]]

    _age_claims(store, p["id"], minutes=CLAIM_STALE_MINUTES + 60)
    assert store.reap_stale_claims() == 0, "an unresolved upload is never reaped"
    again = plan.apply(store, FakeGarmin(), p["id"], today=today)
    assert not plan.workouts_of(again), "and it is not silently uploaded a second time"


# ── the lifecycle of a proposal file ────────────────────────────────────────

def test_a_proposal_expires_and_is_swept_but_what_it_wrote_is_kept(store, today, monkeypatch):
    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin()
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    pid = tools.propose_workout(store, "vo2max", distance_km=10).split("proposal ")[1].split()[0]
    tools.apply_workout(store, pid)
    wid = plan.workouts_of(plan.read(pid))[0]

    _age_proposal(pid, days=plan.TTL_DAYS + 1)
    assert plan.cleanup() == 1
    assert plan.read(pid) is None, "the preview and its button are gone"
    assert wid in own_ids(store), "what it put on Garmin is not"
    assert store.proposal_items(pid) == {0: wid}


def test_an_expired_proposal_is_no_longer_offered_and_an_open_one_is(store, today):
    from runcoach import plan

    _seed_zones(store, today)
    fresh = plan.propose(store, "easy", duration_min=40, today=today)
    old = plan.propose(store, "easy", duration_min=45, today=today)
    _age_proposal(old["id"], days=plan.TTL_DAYS + 1)
    assert [p["id"] for p in plan.open_proposals()] == [fresh["id"]]


def test_a_proposal_file_that_is_not_a_proposal_is_ignored_not_crashed_on(store, today):
    """A file that parses but has no sessions must cost its own card, never the
    page: `server.App.state` reads these, and one KeyError there is a 500 for
    every request the dashboard makes."""
    from runcoach import paths as paths_mod
    from runcoach import plan

    (paths_mod.proposals_dir() / "p-20260101-000000-dead.json").write_text(
        '{"id": "p-20260101-000000-dead"}', encoding="utf-8")
    assert plan.read("p-20260101-000000-dead") is None
    assert plan.open_proposals() == []


# ── the write tool is denied twice, not once ────────────────────────────────

def test_a_card_run_is_refused_by_the_server_itself(store, today, monkeypatch):
    """`--disallowedTools` is a CLI argument and holds only while that flag
    keeps its name and keeps beating the prefix allow. This is the half that
    lives in the process that owns the tool."""
    from runcoach import plan

    _seed_zones(store, today)
    logins = []
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: logins.append(1))
    pid = tools.propose_workout(store, "vo2max", distance_km=10).split("proposal ")[1].split()[0]
    monkeypatch.setenv("RUNCOACH_UNATTENDED", "1")
    out = tools.apply_workout(store, pid)
    assert "unattended card run" in out and "click" in out
    assert logins == [], "it does not even reach Garmin"
    assert plan.read(pid)["status"] == "open"


# ── the four guards that could not see their own subject ────────────────────

@pytest.mark.parametrize("mirror", ["gone", "unknown-workout"], ids=["entry gone", "no workout id"])
def test_a_swap_touches_nothing_the_mirror_cannot_still_confirm(store, today, monkeypatch, mirror):
    """The check has to be POSITIVE. Written as "skip if the mirror shows a
    DIFFERENT workout" it passed only the case where the mirror still knows the
    id - an entry that fell out of the window, or one Garmin lists without a
    workout id, fell through and was unscheduled on trust. That is the recycled
    -id case the whole guard exists for."""
    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin(schedule={777: {"workoutId": 4242, "date": today.isoformat()}})
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    store.replace_scheduled_workouts([ScheduledWorkout(777, today, workout_id=4242,
                                                       title="VO2max 5x3", sport="running")],
                                     today, today)
    pid = tools.propose_workout(store, "easy", duration_min=40,
                                replaces_schedule_id=777).split("proposal ")[1].split()[0]

    if mirror == "gone":                     # the sync window moved on
        store.replace_scheduled_workouts([], today, today)
    else:                                    # Garmin lists the day without a workout id
        store.replace_scheduled_workouts([ScheduledWorkout(777, today, workout_id=None,
                                                           title="Something", sport="running")],
                                         today, today)

    out = tools.apply_workout(store, pid)
    assert "did NOT remove schedule 777" in out
    assert 777 in fake.data["schedule"], "an id the mirror cannot confirm is not touched"
    assert plan.read(pid)["replaces"][0]["removed"] is False


def test_an_unschedule_survives_the_process_that_did_it(store, today, monkeypatch):
    """The removal is saved BEFORE the first upload. Without that write, a
    process that dies between the two leaves Garmin without the entry and the
    app without any record of having taken it - and the next apply aims a
    delete at the same calendar id."""
    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin(schedule={777: {"workoutId": 4242, "date": today.isoformat()}})
    store.replace_scheduled_workouts([ScheduledWorkout(777, today, workout_id=4242,
                                                       title="VO2max 5x3", sport="running")],
                                     today, today)
    p = plan.propose(store, "easy", duration_min=40, replaces=[777], today=today)

    # The process dies at the first upload - AFTER the calendar step and its
    # save, which is the window this guard is about. `KeyboardInterrupt` is not
    # an `Exception`, so nothing in `apply` catches it: the call simply stops,
    # the way a killed process does.
    monkeypatch.setattr(garmin, "upload_workout",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt("killed")))
    with pytest.raises(KeyboardInterrupt):
        plan.apply(store, fake, p["id"], today=today)

    assert 777 not in fake.data["schedule"], "the entry really is gone from Garmin"
    assert plan.read(p["id"])["replaces"][0]["removed"] is True, "and the app knows it took it"


def test_a_proposal_older_than_a_week_is_swept_and_a_days_old_one_is_not(store, today):
    """ABSOLUTE ages, not `TTL_DAYS ± 1`: a test that ages a file relative to
    the constant it is testing moves with it, and setting the TTL to a hundred
    thousand days left this suite green."""
    from runcoach import plan

    _seed_zones(store, today)
    young = plan.propose(store, "easy", duration_min=40, today=today)
    old = plan.propose(store, "easy", duration_min=45, today=today)
    _age_proposal(young["id"], days=1)
    _age_proposal(old["id"], days=8)

    assert plan.cleanup() == 1
    assert plan.read(old["id"]) is None, "a proposal from last week is gone"
    assert plan.read(young["id"]) is not None, "yesterday's is not"
    assert [p["id"] for p in plan.open_proposals()] == [young["id"]]


def test_a_week_that_stops_in_the_middle_is_not_reported_as_done(store, today, monkeypatch):
    """The error field is only set when the FIRST pending session fails, so a
    package that stopped at session two carried `error: None` - and the HTTP
    route turned that into 200 {"ok": true} for a week that is half written."""
    from runcoach import plan

    class FailsOnTheSecond(FakeGarmin):
        """The second session, once - the ones after it go through, so the hole
        sits in the MIDDLE of the package rather than at its start."""

        def upload_running_workout(self, w):
            if len(self.data.get("library", {})) == 1 and not self.data.get("failed_once"):
                self.data["failed_once"] = True
                raise ConnectionError("garmin is down")
            return super().upload_running_workout(w)

    _seed_zones(store, today)
    p = plan.propose_week(store, start=today, days_per_week=4, long_run_day="sun", today=today)
    result = plan.apply(store, FailsOnTheSecond(), p["id"], today=today)

    assert len(plan.workouts_of(result)) == 3 and plan.pending_of(result) == 1
    text = plan.describe_result(result)
    assert "Still NOT on Garmin: 1 session" in text, text
    assert "verified" not in text, "a package with a hole is not a verified package"


def test_the_click_reports_a_half_written_week_as_half_written(tmp_path, monkeypatch, today):
    """The HTTP answer is what the page believes. A package that stopped in the
    middle used to come back 200 {"ok": true}."""
    from runcoach import plan
    from runcoach.web import server

    class FailsOnTheSecond(FakeGarmin):
        def upload_running_workout(self, w):
            if len(self.data.get("library", {})) == 1 and not self.data.get("failed_once"):
                self.data["failed_once"] = True
                raise ConnectionError("garmin is down")
            return super().upload_running_workout(w)

    monkeypatch.setenv("RUNCOACH_HOME", str(tmp_path))
    app = server.App.__new__(server.App)
    app.db_path = str(tmp_path / "t.db")
    app.store = Store(app.db_path)
    app.demo = False
    app.token = None
    app.reset_runtime_state()
    _seed_zones(app.store, today)
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: FailsOnTheSecond())
    p = plan.propose_week(app.store, start=today, days_per_week=4, long_run_day="sun", today=today)

    body, code = app.apply_proposal({"proposal_id": p["id"]})
    assert code == 200
    assert body["ok"] is False and body["pending"] == 1, body
    assert "Still NOT on Garmin" in body["result"]


def test_two_applies_race_for_the_calendar_step_and_only_one_unschedules(store, today,
                                                                        monkeypatch):
    """The calendar step is claimed like a session is. Without that, a second
    apply - an impatient click after the browser gave up, or a chat alongside
    the app - sent its own unschedule at a calendar id the first one had just
    freed, and then overwrote the record of it."""
    import threading

    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin(schedule={777: {"workoutId": 4242, "date": today.isoformat()}})
    store.replace_scheduled_workouts([ScheduledWorkout(777, today, workout_id=4242,
                                                       title="VO2max 5x3", sport="running")],
                                     today, today)
    p = plan.propose(store, "easy", duration_min=40, replaces=[777], today=today)

    calls = []
    fake.unschedule_workout = lambda sid: calls.append(sid)
    # Held at the FIRST thing every apply does, not at the claim: a barrier on
    # the claim itself is never reached once the claim is gone, so the test
    # would pass by failing to interleave - blind to exactly its own subject.
    both_in = threading.Barrier(2, timeout=60)
    seen, lock, real_items = [], threading.Lock(), store.proposal_items

    def items(proposal_id):
        with lock:
            first = len(seen) < 2
            seen.append(1)
        if first:
            both_in.wait()
        return real_items(proposal_id)

    monkeypatch.setattr(store, "proposal_items", items)
    threads = [threading.Thread(target=lambda: plan.apply(store, fake, p["id"], today=today))
               for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=90)

    assert calls == [777], f"the calendar entry was unscheduled {len(calls)} times"
    assert plan.read(p["id"], store)["replaces"][0]["removed"] is True


def test_a_proposal_whose_apply_died_before_saving_is_not_reported_as_waiting(store, today):
    """The claim table knows the session went up; the file still says "open".
    Every surface that reports on proposals passes the store, so none of them
    tells the athlete it is waiting for a yes it already had."""
    from runcoach import plan

    _seed_zones(store, today)
    p = plan.propose(store, "vo2max", distance_km=10, today=today)
    # Exactly the state a killed process leaves: claimed, uploaded, recorded in
    # the database - and the file never written.
    store.claim_proposal_item(p["id"], 0)
    store.record_proposal_item(p["id"], 0, 900_777)

    assert plan.read(p["id"])["status"] == "open", "the file on its own still says open"
    reconciled = plan.read(p["id"], store)
    assert reconciled["status"] == "applied"
    assert plan.workouts_of(reconciled) == [900_777]
    assert plan.open_proposals(store) == [], "it is not waiting for anything"
    assert [x["id"] for x in plan.open_proposals()] == [p["id"]], "...and without a store it is"


def _web_app(tmp_path, monkeypatch, today):
    from runcoach.web import server

    monkeypatch.setenv("RUNCOACH_HOME", str(tmp_path))
    app = server.App.__new__(server.App)
    app.db_path = str(tmp_path / "t.db")
    app.store = Store(app.db_path)
    app.demo = False
    app.token = None
    app.reset_runtime_state()
    _seed_zones(app.store, today)
    return app


@pytest.mark.parametrize("scenario", ["login refused", "every upload refused"])
def test_the_click_answers_502_when_nothing_reached_garmin(tmp_path, monkeypatch, today,
                                                           scenario):
    """Both ways the write can come to nothing over HTTP. The equivalent paths
    in `tools.apply_workout` are tested; these two were not, so either branch
    could be deleted and the suite stayed green - one of them by turning into
    an uncaught exception, i.e. a 500 with a traceback in the log."""
    from runcoach import plan

    app = _web_app(tmp_path, monkeypatch, today)
    p = plan.propose(app.store, "vo2max", distance_km=10, today=today)

    if scenario == "login refused":
        def login(tokenstore=None):
            raise ConnectionError("garmin is unreachable")
    else:
        class Down(FakeGarmin):
            def upload_running_workout(self, w):
                raise ConnectionError("garmin is down")

        def login(tokenstore=None):
            return Down()

    monkeypatch.setattr(garmin, "login", login)
    body, code = app.apply_proposal({"proposal_id": p["id"]})
    assert code == 502, body
    assert "error" in body and body["error"]
    assert plan.read(p["id"], app.store)["status"] == "open", "and it can be tried again"


def test_a_partial_write_carries_no_error_field_over_http(tmp_path, monkeypatch, today):
    """`error` is the "this apply achieved nothing" signal, set only when the
    FIRST pending session fails. A package that got three of four up must not
    set it - otherwise the page cannot tell "nothing happened" from "not
    everything happened", and both answers look the same to the athlete."""
    from runcoach import plan

    class FailsOnTheSecond(FakeGarmin):
        def upload_running_workout(self, w):
            if len(self.data.get("library", {})) == 1 and not self.data.get("failed_once"):
                self.data["failed_once"] = True
                raise ConnectionError("garmin is down")
            return super().upload_running_workout(w)

    app = _web_app(tmp_path, monkeypatch, today)
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: FailsOnTheSecond())
    p = plan.propose_week(app.store, start=today, days_per_week=4, long_run_day="sun", today=today)

    body, code = app.apply_proposal({"proposal_id": p["id"]})
    assert code == 200 and body["ok"] is False and body["pending"] == 1
    assert body["error"] is None, "three of four went up - that is not 'nothing happened'"


# ── the spacing rules hold on BOTH paths, not only in the week builder ──────

def test_a_hard_session_too_close_to_the_last_one_says_so_on_the_proposal(store, today):
    """`build_week` keeps 48 hours between hard sessions in code. The single
    moved session went through `propose_workout`, where the same rule was a
    sentence asking the model to do the arithmetic - and a red morning uses
    exactly that path."""
    from runcoach import plan

    _seed_zones(store, today)
    store.upsert_activity(make_activity(9_000_000_888, today, aerobic_te=3.8))
    p = plan.propose(store, "vo2max", distance_km=10, day=today + timedelta(days=1), today=today)
    assert "48 hours apart" in p["preview"], p["preview"]

    later = plan.propose(store, "vo2max", distance_km=10, day=today + timedelta(days=3),
                         today=today)
    assert "48 hours apart" not in later["preview"]


def test_the_day_before_the_long_run_says_so_and_a_guessed_one_says_it_is_a_guess(store, today):
    from runcoach import plan

    _seed_zones(store, today)
    long_day = today + timedelta(days=3)
    store.record_workout(900_500, name="Long run 20 km", kind="long", spec_json="{}",
                         schedule_id=888, scheduled_day=long_day)
    store.replace_scheduled_workouts(
        [ScheduledWorkout(888, long_day, workout_id=900_500, title="Anything", sport="running")],
        today, today + timedelta(days=7))
    p = plan.propose(store, "vo2max", distance_km=10, day=long_day - timedelta(days=1),
                     today=today)
    assert "the day before your long run" in p["preview"]
    assert "judged from its name" not in p["preview"], "this one is on the record, not a guess"

    # A calendar entry the app did not create can only be judged by its title.
    store.replace_scheduled_workouts(
        [ScheduledWorkout(889, long_day, workout_id=4242, title="Long run with the club",
                          sport="running")], today, today + timedelta(days=7))
    guessed = plan.propose(store, "threshold", distance_km=10,
                           day=long_day - timedelta(days=1), today=today)
    assert "looks like a long run" in guessed["preview"] and "check it" in guessed["preview"]


def test_an_easy_session_is_not_lectured_about_spacing(store, today):
    """The rules are about hard stimuli. An easy run the day before the long
    run is the normal shape of a week, not a warning."""
    from runcoach import plan

    _seed_zones(store, today)
    store.upsert_activity(make_activity(9_000_000_889, today, aerobic_te=3.8))
    p = plan.propose(store, "easy", duration_min=40, day=today + timedelta(days=1), today=today)
    assert "48 hours" not in p["preview"] and "long run" not in p["preview"]


def test_a_second_click_on_a_finished_proposal_does_not_open_a_garmin_session(tmp_path,
                                                                              monkeypatch, today):
    from runcoach import plan

    app = _web_app(tmp_path, monkeypatch, today)
    p = plan.propose(app.store, "vo2max", distance_km=10, today=today)
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: FakeGarmin())
    body, code = app.apply_proposal({"proposal_id": p["id"]})
    assert code == 200 and body["ok"]

    logins = []
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: logins.append(1))
    body, code = app.apply_proposal({"proposal_id": p["id"]})
    assert code == 409 and "already applied" in body["error"]
    assert logins == [], "nothing to do is decided before Garmin is contacted"


def test_a_hard_session_on_a_red_day_carries_the_days_own_decision(store, today):
    """The template asks the coach to file the lighter session on a red day.
    That is a sentence in a prompt; the spacing rule two lines further down is
    annotated in code. Same promise, so this one is annotated too - the preview
    says what the app decided, whatever the coach concluded."""
    from runcoach import plan

    _seed_zones(store, today)
    store.upsert_daily(make_day(today, hrv_status="POOR", sleep_score=48, resting_hr=57))
    p = plan.propose(store, "vo2max", distance_km=10, today=today)
    assert "the app's decision for today is REST" in p["preview"], p["preview"]

    easy = plan.propose(store, "easy", duration_min=40, today=today)
    assert "decision for today" not in easy["preview"], "the note is about hard sessions"


@pytest.mark.parametrize("day_kind", ["red", "clear"])
def test_the_decision_note_appears_exactly_when_the_day_decided_against_it(tmp_path, monkeypatch,
                                                                           today, day_kind):
    """If and only if: the note is tied to the app's own DECISION, not to the
    readiness light. A GO morning whose week has had its hard share already
    decides EASY - and the note belongs there too."""
    from runcoach import plan, tools

    monkeypatch.setenv("RUNCOACH_HOME", str(tmp_path))
    store = Store(tmp_path / f"{day_kind}.db")
    if day_kind == "red":
        _seed_zones(store, today)
        store.upsert_daily(make_day(today, hrv_status="POOR", sleep_score=48, resting_hr=57))
    else:
        # Green, and nothing hard on record: the decision is to go hard.
        store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=85, resting_hr=45))

    decision = tools._decide(store, today)["decision"]
    p = plan.propose(store, "vo2max", distance_km=10, today=today)
    assert ("decision for today" in p["preview"]) == (decision in ("easy", "rest")), (
        f"decision {decision!r} against preview {p['preview']!r}")


def test_the_week_shows_the_weeks_it_was_sized_from(store, today):
    """A median hides a down week or a taper, and nothing in here can tell one
    from a normal week - so the athlete sees the weeks themselves."""
    from runcoach import plan

    _seed_zones(store, today)
    monday = today - timedelta(days=today.weekday())
    for week, minutes in enumerate((240, 60, 250, 260), start=1):
        day = monday - timedelta(weeks=week)
        store.upsert_activity(make_activity(9_100_000 + week, day, duration_s=minutes * 60,
                                            distance_m=minutes * 160))
    p = plan.propose_week(store, start=today, days_per_week=4, long_run_day="sun", today=today)
    assert "60" in p["preview"] and "median" in p["preview"]
    assert "down week or taper" in p["preview"]


def test_uploaded_but_never_scheduled_is_not_done_and_is_finished_by_a_resume(store, today,
                                                                              monkeypatch):
    """Uploaded alone is a workout in the library that no watch will show.
    Counting it as finished put the green "On Garmin" pill on the card for a
    session that never reached the calendar - and the only place the athlete
    could have learned that was `runcoach doctor`."""
    from runcoach import plan

    class NoCalendar(FakeGarmin):
        def schedule_workout(self, workout_id, date_str):
            raise ConnectionError("calendar is down")

    _seed_zones(store, today)
    p = plan.propose(store, "vo2max", distance_km=10, today=today)
    first = plan.apply(store, NoCalendar(), p["id"], today=today)

    assert plan.workouts_of(first) == [900001], "the session IS in the library"
    assert plan.pending_of(first) == 1, "...and it is not done"
    assert "NOT scheduled" in plan.describe_result(first)
    assert "apply the proposal again" in plan.describe_result(first)

    # The resume gives it a day instead of uploading it a second time.
    fake = FakeGarmin(library=dict(first and {}))
    fake.data["library"] = {900001: {"workoutName": "VO2max 5x4 min", "workoutId": 900001}}
    second = plan.apply(store, fake, p["id"], today=today)
    assert plan.pending_of(second) == 0
    assert plan.workouts_of(second) == [900001], "no second upload"
    assert second["items"][0]["schedule_id"]
    assert "scheduled for" in plan.describe_result(second)


# ── every step that takes a claim, raced ────────────────────────────────────
#
# One row per step of `plan.apply` that is protected by the claim table. Two
# applies are held at the same point and released together; the Garmin call
# behind that step must happen exactly ONCE. Written as a table because this
# is the third such step - a fourth adds a row here instead of a fourth test
# that looks almost like the other three.

def _race(store, fake, proposal_id, today, monkeypatch, threads=2):
    """Run `plan.apply` twice at once, both held until the other has arrived."""
    import threading

    from runcoach import plan

    gate = threading.Barrier(threads, timeout=60)
    seen, lock, real_items = [], threading.Lock(), store.proposal_items

    def items(pid):
        with lock:
            first = len(seen) < threads
            seen.append(1)
        if first:
            gate.wait()
        return real_items(pid)

    monkeypatch.setattr(store, "proposal_items", items)
    errors = []

    def run():
        try:
            plan.apply(store, fake, proposal_id, today=today)
        except Exception as exc:                        # noqa: BLE001
            errors.append(exc)
            gate.abort()

    workers = [threading.Thread(target=run) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=90)
    assert not errors, errors


@pytest.mark.parametrize("step", ["session upload", "calendar entry", "scheduling"])
def test_every_claimed_step_of_an_apply_happens_exactly_once(store, today, monkeypatch, step):
    import time as _time

    from runcoach import plan

    _seed_zones(store, today)
    calls: list = []

    def spy(value=None):
        """Record the call and HOLD it briefly.

        Arriving together is not the same as overlapping: released from the
        barrier, one thread can finish the whole apply before the other is
        scheduled by the OS, and the second one then finds the work already
        recorded and skips it - the test passes without the lock it is
        testing. The pause keeps the first call in flight while the second
        makes its own decision."""
        calls.append(value)
        _time.sleep(0.25)

    if step == "session upload":
        fake = FakeGarmin()
        real = fake.upload_running_workout
        fake.upload_running_workout = lambda w: (spy(1), real(w))[1]
        p = plan.propose(store, "vo2max", distance_km=10, today=today)

    elif step == "calendar entry":
        fake = FakeGarmin(schedule={777: {"workoutId": 4242, "date": today.isoformat()}})
        fake.unschedule_workout = spy
        store.replace_scheduled_workouts(
            [ScheduledWorkout(777, today, workout_id=4242, title="VO2max 5x3", sport="running")],
            today, today)
        p = plan.propose(store, "easy", duration_min=40, replaces=[777], today=today)

    else:                                   # scheduling, on a resume
        class NoCalendar(FakeGarmin):
            def schedule_workout(self, workout_id, date_str):
                raise ConnectionError("calendar is down")

        p = plan.propose(store, "vo2max", distance_km=10, today=today)
        plan.apply(store, NoCalendar(), p["id"], today=today)   # uploaded, no day
        fake = FakeGarmin()
        fake.data["library"] = {900001: {"workoutName": "VO2max 5x4 min", "workoutId": 900001}}
        real_schedule = fake.schedule_workout
        fake.schedule_workout = lambda wid, day: (spy(wid), real_schedule(wid, day))[1]

    _race(store, fake, p["id"], today, monkeypatch)
    assert len(calls) == 1, f"{step}: Garmin was asked {len(calls)} times"


# ── taking it back off ──────────────────────────────────────────────────────

def test_undo_takes_the_session_off_the_calendar_and_leaves_the_workout(store, today,
                                                                        monkeypatch):
    """The counterpart the write path was missing. Without it the first real
    write is a one-way door - and a one-way door is the reason a first real
    write does not get made."""
    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin()
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    pid = tools.propose_workout(store, "vo2max", distance_km=10).split("proposal ")[1].split()[0]
    tools.apply_workout(store, pid)
    wid = plan.workouts_of(plan.read(pid, store))[0]
    assert store.get_scheduled_workouts(today, today), "it is on the calendar"

    out = tools.undo_workout(store, pid)
    assert "Taken off the calendar: 1 session" in out and "stay in your Garmin library" in out
    assert fake.data["schedule"] == {}, "the calendar entry is gone"
    assert wid in fake.data["library"], "the workout itself is not"
    assert store.get_scheduled_workouts(today, today) == [], "and the mirror agrees"
    assert plan.pending_of(plan.read(pid, store)) == 1, "so the proposal has work again"


def test_undo_puts_the_proposal_back_within_reach_of_a_second_apply(store, today, monkeypatch):
    """Undo releases the scheduling claim, otherwise applying again would be
    refused by the table for the very day it just freed."""
    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin()
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    pid = tools.propose_workout(store, "vo2max", distance_km=10).split("proposal ")[1].split()[0]
    tools.apply_workout(store, pid)
    tools.undo_workout(store, pid)

    again = tools.apply_workout(store, pid)
    assert "On Garmin" in again and "scheduled for" in again
    assert len(fake.data["library"]) == 1, "the workout was NOT uploaded a second time"
    assert plan.pending_of(plan.read(pid, store)) == 0


def test_undo_leaves_alone_what_this_app_did_not_upload(store, today, monkeypatch):
    """`runcoach_workouts` is the record. A calendar id whose workout is not
    ours - the athlete's own, or one Garmin has since given to something else -
    is reported, not removed."""
    from runcoach import plan

    _seed_zones(store, today)
    fake = FakeGarmin()
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: fake)
    pid = tools.propose_workout(store, "vo2max", distance_km=10).split("proposal ")[1].split()[0]
    tools.apply_workout(store, pid)

    # The app forgets it ever made this one - as if the athlete had.
    p = plan.read(pid, store)
    wid = plan.workouts_of(p)[0]
    with __import__("sqlite3").connect(store.path) as conn:
        conn.execute("DELETE FROM runcoach_workouts WHERE workout_id = ?", (wid,))

    out = tools.undo_workout(store, pid)
    assert "not one this app uploaded" in out and "left alone" in out
    assert fake.data["schedule"], "the entry is still there"


def test_undo_on_something_that_was_never_applied_says_so(store, today, monkeypatch):
    from runcoach import plan

    _seed_zones(store, today)
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: FakeGarmin())
    p = plan.propose(store, "easy", duration_min=40, today=today)
    assert "nothing of this proposal is on the calendar" in tools.undo_workout(store, p["id"])
    assert "nothing to take back" in tools.undo_workout(store, "p-20260101-000000-dead")


def test_undo_is_refused_in_a_card_run_like_every_other_write(store, today, monkeypatch):
    _seed_zones(store, today)
    logins = []
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: logins.append(1))
    monkeypatch.setenv("RUNCOACH_UNATTENDED", "1")
    out = tools.undo_workout(store, "p-20260101-000000-dead")
    assert "unattended card run" in out
    assert logins == [], "it does not even reach Garmin"


def test_every_registered_tool_is_classified_as_reading_or_writing():
    """The mechanism, not a third remembered list. `--allowedTools
    mcp__runcoach` is a PREFIX allow: a tool nobody classified is allowed, and
    an unattended card run would be able to call it. Adding a tool without
    deciding what it does makes this red."""
    import asyncio
    import pathlib
    import tempfile

    from runcoach import mcp_server
    from runcoach.web import agent

    registered = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    writes = set(tools.WRITE_TOOLS)
    assert writes <= registered, f"WRITE_TOOLS names something unregistered: {writes - registered}"

    reads = registered - writes
    assert reads == READ_ONLY_TOOLS, (
        f"a tool appeared or changed sides: {sorted(reads ^ READ_ONLY_TOOLS)} - list it in "
        f"READ_ONLY_TOOLS if it only reads, in tools.WRITE_TOOLS if it can change Garmin")

    with tempfile.TemporaryDirectory() as d:
        cmd = agent.command(pathlib.Path(d), db=None)
    denied = set(cmd[cmd.index("--disallowedTools") + 1].split(","))
    assert denied == {f"mcp__runcoach__{n}" for n in writes}, (
        f"the card run denies {sorted(denied)}, the write tools are {sorted(writes)}")
