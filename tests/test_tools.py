"""Tool handlers: compact English text, empty and error paths never raise."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from conftest import DETAIL, FakeGarmin, make_activity, make_day, make_split
from runcoach import demo, garmin, sync, tools
from runcoach.models import ScheduledWorkout
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


EXPECTED_MCP_TOOLS = {
    "get_training_readiness", "get_recovery_summary", "get_daily_metrics", "get_trend",
    "get_training_load", "get_recent_activities", "get_intensity_distribution",
    "analyze_workout", "get_vo2max_history", "sync_garmin",
    # the write path: propose (local) and, after a human's yes, apply (Garmin)
    "propose_workout", "propose_week", "apply_workout"}


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
    assert store.is_own_workout(it["workout_id"]), "what we uploaded is recorded as ours"
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
    assert out.startswith("Cannot build that week") and "mirrors the calendar" in out
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
    assert len(wids) == 4 and all(store.is_own_workout(w) for w in wids)
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


def test_a_package_that_fails_half_way_is_applied_with_the_missing_day_named(store, today,
                                                                             monkeypatch):
    from runcoach import plan

    class Flaky(FakeGarmin):
        """The third upload fails, ONCE - the fourth goes through."""

        def upload_running_workout(self, w):
            if len(self.data.get("library", {})) == 2 and not self.data.get("hiccuped"):
                self.data["hiccuped"] = True
                raise ConnectionError("hiccup")
            return super().upload_running_workout(w)

    _seed_zones(store, today)
    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: Flaky())
    pid = tools.propose_week(store, start_day=today.isoformat(), days_per_week=4,
                             long_run_day="sun").split("proposal ")[1].split()[0]
    out = tools.apply_workout(store, pid)
    p = plan.read(pid)
    assert p["status"] == "applied", "three sessions ARE on Garmin - a re-apply would double them"
    assert len(plan.workouts_of(p)) == 3 and out.count("On Garmin:") == 3
    missing = next(it for it in p["items"] if not it["workout_id"])
    assert f"! {missing['day']}" in out and "NOT uploaded" in out and "hiccup" in out


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
