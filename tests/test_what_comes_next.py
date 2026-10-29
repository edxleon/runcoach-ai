"""What the planner and the Today tab say comes next, against the situations a
real runner is in: weeks that shrank, a long pause, a first run back, a plateau,
a plan already on the calendar. Each test is one scenario from the v0.3 review,
written from the scenario rather than from the code."""

from __future__ import annotations

from datetime import date, timedelta

from conftest import make_activity, make_day
from runcoach import logic, plan, planning, snapshot, tools
from runcoach.models import ScheduledWorkout

_AID = [5_000_000_000]


def _run(store, day: date, minutes: int, *, z4=0, z5=0, detail=True, an=0.3):
    _AID[0] += 1
    store.upsert_activity(make_activity(_AID[0], day, duration_s=minutes * 60, load=None,
                                        aerobic_te=2.5, anaerobic_te=an))
    if detail:
        easy = max(0, minutes * 60 - z4 - z5)
        store.update_activity_detail(_AID[0], {"hr_z1_s": 0, "hr_z2_s": easy, "hr_z3_s": 0,
                                               "hr_z4_s": z4, "hr_z5_s": z5, "splits": [],
                                               "hr_z4_low": 156, "hr_z5_low": 172})


def _weeks(store, today: date, minutes_per_week: list[int]):
    """Completed Monday-weeks before today's week, oldest first; two runs each."""
    monday = today - timedelta(days=today.weekday())
    for back, mins in enumerate(reversed(minutes_per_week), start=1):
        if mins > 0:
            start = monday - timedelta(weeks=back)
            _run(store, start + timedelta(days=1), mins // 2)
            _run(store, start + timedelta(days=4), mins - mins // 2)


def _green(store, day: date):
    store.upsert_daily(make_day(day, hrv_status="BALANCED", sleep_score=85, body_battery_high=90))


def _session_minutes(out: str) -> int:
    import re

    return sum(int(m) for m in re.findall(r"~(\d+) min\)", out.split("assumption:")[0]))


# ── the week's size ──────────────────────────────────────────────────────────

def test_weeks_that_shrank_are_the_base_not_the_big_weeks_before_them(store, today):
    _weeks(store, today, [300, 300, 300, 300, 300, 0, 60, 60])
    budget, lines = plan._week_budget(store, today)
    assert budget is not None and budget <= 70, (budget, lines)


def test_a_long_pause_gives_a_small_return_week_without_hard_sessions(store, today):
    _weeks(store, today, [600, 600, 0, 0, 0, 0, 0, 0])
    budget, lines = plan._week_budget(store, today)
    assert budget <= 300 and "return week" in lines[0], (budget, lines)
    out = tools.propose_week(store, days_per_week=4, long_run_day="sun")
    assert "HR zone 5" not in out and "HR zone 4" not in out, out
    assert _session_minutes(out) <= budget * 1.2, (_session_minutes(out), budget)


def test_a_pause_longer_than_the_window_is_still_a_return_week(store, today):
    # Months of 90 min, then seven weeks off: one running week left in the window.
    _weeks(store, today, [90] * 6 + [0] * 7)
    budget, lines = plan._week_budget(store, today)
    assert budget is not None and budget <= 90, (budget, lines)
    assert any("return week" in line for line in lines), lines


def test_the_return_week_on_the_page_adds_up_to_what_its_line_says(store, today):
    _weeks(store, today, [40, 40, 40, 40, 0, 0])
    out = tools.propose_week(store, days_per_week=4, long_run_day="sun")
    budget, _ = plan._week_budget(store, today)
    assert _session_minutes(out) <= max(budget * 1.2, budget + 10), (out, budget)


def test_the_week_being_run_counts_when_planning_on_sunday(store, today):
    sunday = today + timedelta(days=6 - today.weekday())
    _weeks(store, sunday, [150, 200, 215, 245])
    monday = sunday - timedelta(days=6)
    _run(store, monday + timedelta(days=1), 130)
    _run(store, monday + timedelta(days=5), 130)
    _, lines = plan._week_budget(store, sunday)
    assert "260" in lines[0], lines


def test_following_the_plan_brings_a_down_week_every_fourth_week_and_progress(store, today):
    """The athlete plans every Sunday and runs exactly what was planned."""
    sunday = today - timedelta(days=today.weekday() + 1) - timedelta(weeks=16)
    _weeks(store, sunday + timedelta(days=1), [150, 150, 150, 150])
    sizes, downs = [], []
    for _ in range(12):
        budget, lines = plan._week_budget(store, sunday)
        sizes.append(budget)
        downs.append(": down week" in lines[0])
        _run(store, sunday + timedelta(days=2), budget // 2)
        _run(store, sunday + timedelta(days=5), budget - budget // 2)
        sunday += timedelta(weeks=1)
    assert sum(downs) == 3, (sizes, downs)
    assert not any(a and b for a, b in zip(downs, downs[1:], strict=False)), (sizes, downs)
    peaks = [max(sizes[i:i + 4]) for i in range(0, 12, 4)]
    assert peaks[0] < peaks[1] < peaks[2], (sizes, downs)


# ── which hard session ───────────────────────────────────────────────────────

def test_after_a_pause_the_hard_day_is_not_a_vo2max_session(store, today):
    _weeks(store, today - timedelta(weeks=3), [120] * 6)   # six weeks, then three off
    for i in range(22):
        _green(store, today - timedelta(days=i))
    out = tools.get_training_readiness(store)
    assert "Which hard session: VO2max" not in out, out
    assert "HARD" not in out.split("The app's decision for today:")[1][:8], out


def test_a_session_already_on_todays_calendar_is_not_named_again_as_lacking(store, today):
    from runcoach import plan as plan_mod  # noqa: F401

    _green(store, today)
    store.record_workout(4711, name="VO2max 5x4 min", kind="vo2max", spec_json="{}",
                         schedule_id=9, scheduled_day=today)
    store.replace_scheduled_workouts(
        [ScheduledWorkout(9, today, workout_id=4711, title="VO2max 5x4 min", sport="running")],
        today - timedelta(days=7), today + timedelta(days=14))
    out = tools.get_training_readiness(store)
    assert "still lacks: VO2max" not in out and "Which hard session" not in out, out


def test_a_strength_session_on_the_calendar_does_not_stand_in_for_the_run(store, today):
    _green(store, today)
    store.replace_scheduled_workouts(
        [ScheduledWorkout(3, today, title="Gym", sport="strength_training")],
        today - timedelta(days=7), today + timedelta(days=14))
    out = tools.get_training_readiness(store)
    assert "Which hard session: VO2max" in out, out


def _plateau(store, today, days=40, value=50.0):
    for i in range(days):
        store.upsert_daily(make_day(today - timedelta(days=i), vo2max=value, hrv_status="BALANCED",
                                    sleep_score=85, body_battery_high=90))


def test_a_steady_block_run_without_the_app_counts_as_the_measurement(store, today):
    _plateau(store, today)
    _run(store, today - timedelta(days=5), 45, z4=15 * 60)      # 15 min at threshold
    snap = snapshot.assemble(store, today=today)
    assert snap["vo2max"]["measurement"]["due"] is False, snap["vo2max"]["measurement"]
    assert snap["next_stimulus"]["kind"] != "steady"


def test_a_plateau_never_starves_the_vo2max_stimulus(store, today):
    _plateau(store, today)
    snap = snapshot.assemble(store, today=today)
    assert snap["next_stimulus"]["kind"] == "vo2max", snap["next_stimulus"]
    # ...and once the Z5 stimulus is set, the measurement takes the next hard day.
    _run(store, today - timedelta(days=3), 50, z5=4 * 60, an=2.4)   # an interval session
    snap = snapshot.assemble(store, today=today)
    assert snap["next_stimulus"]["kind"] == "steady", snap["next_stimulus"]


def test_sparse_vo2max_values_count_by_the_calendar_not_by_the_rows():
    today = date(2026, 6, 10)
    series = [{"day": (today - timedelta(days=i)).isoformat(), "vo2max": 50.0}
              for i in range(0, 56, 4)][::-1]                 # a value every fourth day
    v = snapshot.build_vo2max(series, [], today=today)
    assert v["measurement"]["due"] is True and v["measurement"]["days"] >= 52, v["measurement"]


def test_a_steady_block_that_never_reached_the_calendar_does_not_hold_the_rule_back():
    own = [{"kind": "steady", "scheduled_day": "2026-06-05", "schedule_id": None}]
    assert snapshot.last_steady_day(own) is None


# ── smaller ──────────────────────────────────────────────────────────────────

def test_the_structure_alternates_across_a_53_week_year():
    a, b = date(2026, 12, 28), date(2027, 1, 4)
    assert a.isocalendar()[1] == 53 and b.isocalendar()[1] == 1
    assert planning.week_variant(a) != planning.week_variant(b)


def test_an_old_or_inconsistent_race_prediction_is_not_a_pace_source():
    today = date(2026, 6, 10)
    old = {"pred_10k_s": 2910, "pred_5k_s": 1392, "pred_day": "2025-01-01"}
    assert planning._paces(old, today=today)[0] == planning.DEFAULT_LT_PACE_S
    upside_down = {"pred_10k_s": 2700, "pred_5k_s": 1500, "pred_day": "2026-06-09"}
    assert planning._paces(upside_down, today=today)[0] == planning.DEFAULT_LT_PACE_S


def test_a_three_day_measurement_week_says_what_it_did(store, today):
    _plateau(store, today)
    _run(store, today - timedelta(days=3), 50, z5=4 * 60, an=2.4)   # an interval session
    out = tools.propose_week(store, days_per_week=3, long_run_day="sun")
    assert "the VO2max session stays" not in out, out


def test_the_measurement_line_says_only_what_the_fitted_week_holds(store, today):
    _plateau(store, today)
    _streak(store, today - timedelta(days=27), today - timedelta(days=4), 45)
    _run(store, today - timedelta(days=3), 50, z5=4 * 60, an=2.4)   # an interval session
    start = today + timedelta(days=7 - today.weekday())
    store.replace_scheduled_workouts(
        [ScheduledWorkout(1, start + timedelta(days=1), title="Intervals 6x3", sport="running")],
        today - timedelta(days=7), today + timedelta(days=14))
    out = tools.propose_week(store, start_day=start.isoformat(), days_per_week=4,
                             long_run_day="sun")
    sessions = out.split("assumption:")[0]
    # The calendar's interval day stands in for the VO2max session; the steady
    # block survives - and the line must not claim a VO2max session that is not there.
    assert "Steady threshold" in sessions and "VO2max" not in sessions, out
    assert "measurement week" in out and "the VO2max session stays" not in out, out


def test_the_return_threshold_is_one_number():
    assert logic.RETURN_AFTER_DAYS == 10


# ── round 2: the neighbours ──────────────────────────────────────────────────

def _streak(store, first: date, last: date, minutes: int, every: int = 2):
    d = first
    while d <= last:
        _run(store, d, minutes, detail=False)
        d += timedelta(days=every)


def test_the_week_after_a_return_week_that_was_run_is_not_another_return_week(store):
    _streak(store, date(2026, 4, 6), date(2026, 5, 31), 40)      # 4 x 40 a week, then ill
    sunday = date(2026, 6, 14)
    _, _, mode = plan._week_plan(store, sunday, sunday + timedelta(days=1))
    assert mode == "return"
    for d in (1, 3, 5, 6):                                        # the return week, run Mon-Sat
        _run(store, sunday + timedelta(days=d), 28, detail=False)
    nxt = sunday + timedelta(weeks=1)
    _, lines, mode = plan._week_plan(store, nxt, nxt + timedelta(days=1))
    assert mode != "return" and "return week" not in lines[0], lines


def test_dropped_days_do_not_pour_the_budget_into_one_long_session(store, today):
    _streak(store, today - timedelta(days=27), today - timedelta(days=1), 20, every=2)
    out = tools.propose_week(store, days_per_week=3, long_run_day="sun")
    import re

    sessions = [int(m) for m in re.findall(r"~(\d+) min\)", out.split("assumption:")[0])]
    assert sessions and max(sessions) <= 25, out              # 1.25 x the longest run (20)


def test_a_very_long_pause_never_returns_to_more_than_the_weeks_before_it(store, today):
    _streak(store, today - timedelta(days=57 + 28), today - timedelta(days=57), 20, every=7)
    budget, lines, mode = plan._week_plan(store, today)
    assert mode == "return" and budget <= 20, (budget, lines)


def test_the_day_after_the_first_run_back_is_not_a_hard_day(store, today):
    _streak(store, today - timedelta(days=70), today - timedelta(days=25), 40)
    _run(store, today - timedelta(days=1), 25, detail=False)
    _green(store, today)
    snap = snapshot.assemble(store, today=today)
    assert snap["decision_today"]["decision"] != "hard", snap["decision_today"]
    assert snap["next_stimulus"]["kind"] == "easy"


def test_a_short_comeback_and_a_gap_do_not_fall_back_to_the_default_week(store, today):
    _streak(store, today - timedelta(days=100), today - timedelta(days=45), 30)
    for back in (13, 11, 8):
        _run(store, today - timedelta(days=back), 30, detail=False)
    budget, lines, mode = plan._week_plan(store, today)
    assert mode != "default" and budget is not None and budget <= 110, (budget, lines)


def test_no_down_week_lands_right_behind_a_return_week(store):
    _streak(store, date(2026, 3, 9), date(2026, 6, 10), 40, every=1)   # ~280 min a week
    first_back = date(2026, 6, 25)                                      # 15 days later, a Thursday
    _run(store, first_back, 30, detail=False)
    sunday = date(2026, 6, 28)            # plans the week of 29 June: a return week
    assert planning.down_week_due(sunday + timedelta(days=8))[0], "the week after is a cadence down week"
    modes = []
    for _ in range(4):
        budget, _, mode = plan._week_plan(store, sunday, sunday + timedelta(days=1))
        modes.append(mode)
        for d in (2, 4, 6):
            _run(store, sunday + timedelta(days=d), max(20, (budget or 60) // 3), detail=False)
        sunday += timedelta(weeks=1)
    assert modes[0] == "return" and "down" not in modes[:4], modes


def test_a_single_session_ignores_a_stale_race_prediction(store, today):
    from runcoach.models import DailyMetrics

    m = DailyMetrics(day=today - timedelta(days=400))
    m.race_5k_s, m.race_10k_s = 1392, 2910
    store.upsert_daily(m)
    out = tools.propose_workout(store, "threshold", distance_km=10)
    assert "no usable Garmin race prediction" in out, out


def test_a_three_day_week_measures_only_after_the_z5_stimulus(store, today):
    _plateau(store, today)
    _streak(store, today - timedelta(days=27), today - timedelta(days=8), 40, every=3)
    out = tools.propose_week(store, days_per_week=3, long_run_day="sun")
    sessions = out.split("assumption:")[0]
    assert "VO2max" in sessions and "Steady threshold" not in sessions, out


def test_a_steady_block_scheduled_ahead_is_not_said_to_be_in_the_past():
    today = date(2026, 6, 10)
    d = logic.measurement_due(today - timedelta(days=40), today - timedelta(days=56),
                              today + timedelta(days=3), today)
    assert d["due"] is False and "-3" not in d["reason"] and "scheduled for 2026-06-13" in d["reason"]


# ── the fresh reader: real Garmin values ─────────────────────────────────────

def _drifting(today: date, days: int = 40) -> list[dict]:
    """Garmin's precise value moves by tenths, run or no run."""
    vals = [49.2 - 0.1 * (i // 4) for i in range(days)]
    return [{"day": (today - timedelta(days=days - 1 - i)).isoformat(), "vo2max": v}
            for i, v in enumerate(vals)]


def test_a_drifting_value_without_runs_is_not_a_measurement():
    today = date(2026, 6, 10)
    v = snapshot.build_vo2max(_drifting(today), [], today=today)
    assert v["measurement"]["due"] is True, v["measurement"]
    assert v["last_change_day"] is None, "no run, no measurement"


def test_a_change_on_a_run_day_is_a_measurement():
    today = date(2026, 6, 10)
    series = _drifting(today)
    i = max(k for k in range(1, len(series) - 3) if series[k]["vo2max"] != series[k - 1]["vo2max"])
    run_day = date.fromisoformat(series[i]["day"])
    runs = [{"day": run_day.isoformat(), "type": "running"}]
    v = snapshot.build_vo2max(series, runs, today=today)
    assert v["last_change_day"] == run_day.isoformat() and v["measurement"]["due"] is False


def test_a_steady_block_taken_off_the_calendar_does_not_hold_the_rule_back():
    today = date(2026, 6, 10)
    past = {"kind": "steady", "scheduled_day": "2026-06-05", "schedule_id": 7, "workout_id": 1}
    ahead = {"kind": "steady", "scheduled_day": "2026-06-13", "schedule_id": 8, "workout_id": 2}
    assert snapshot.last_steady_day([past], [], today=today, on_calendar={7, 8}) is None, (
        "a past block counts through the run it produced, not through the plan")
    assert snapshot.last_steady_day([ahead], [], today=today, on_calendar={8}) == date(2026, 6, 13)
    assert snapshot.last_steady_day([ahead], [], today=today, on_calendar=set()) is None


def test_a_structured_steady_workout_counts_as_the_block_it_was():
    run = {"day": "2026-06-07", "type": "running", "has_detail": True,
           "zones_s": {"z4": 800, "z5": 60},
           "structure": {"kind": "intervals", "rep_count": 1}}
    assert snapshot.last_steady_day([], [run], today=date(2026, 6, 10)) == date(2026, 6, 7)
    reps = {**run, "structure": {"kind": "intervals", "rep_count": 5}}
    assert snapshot.last_steady_day([], [reps], today=date(2026, 6, 10)) is None


# ── found by the synthetic athletes (evals/sim.py) ───────────────────────────

def test_a_small_week_is_a_shorter_week_not_a_week_of_hard_sessions():
    """A budget too small for four days used to lose its easy runs first and
    keep both quality sessions: 'vo2max, threshold, long' as a three-day week,
    or a lone VO2max session as the whole week."""
    monday = date(2026, 6, 15)
    for total in range(40, 130, 6):
        sessions, _ = planning.build_week(monday, days_per_week=4, long_run_day="sun",
                                          total_minutes=total)
        kinds = [s.kind for _, s in sessions]
        hard = sum(k in planning.HARD_KINDS for k in kinds)
        assert hard <= len(kinds) - hard, (total, kinds)      # never more hard than not


def test_a_small_week_can_grow():
    """Dropped days kept their size, so a 72- and an 80-minute budget gave the
    same 70-minute week, and a runner who ran it exactly was planned it again,
    forever: the next day never fit and nothing else grew."""
    monday = date(2026, 6, 15)
    got = []
    for total in (70, 77, 85, 94, 103):
        sessions, _ = planning.build_week(monday, days_per_week=4, long_run_day="sun",
                                          total_minutes=total)
        got.append(sum(s.estimated_seconds for _, s in sessions) // 60)
    assert all(a < b for a, b in zip(got, got[1:], strict=False)), got


def test_the_week_after_the_return_week_does_not_shrink_below_it(store):
    """Back from four weeks off, the return week was run in full (4 x 24 min).
    The next week took the median of the windows since the pause - one of them
    holding a single run - and came out SMALLER than the week just run."""
    sunday = date(2026, 6, 14)
    for k in range(10):                     # ~200 min weeks until five weeks ago
        _run(store, sunday - timedelta(days=36 + 3 * k), 50)
    _run(store, sunday - timedelta(days=7), 34)        # the first run back
    for back in (6, 4, 2, 0):
        _run(store, sunday - timedelta(days=back), 24)
    budget, lines, mode = plan._week_plan(store, sunday)
    assert mode != "return" and budget >= 96, (budget, mode, lines)


def test_a_held_week_is_no_bigger_than_the_week_before_it(store, today):
    """Found by the synthetic `overreacher`: it ran more than planned every
    week, Garmin's ratio went over 1.3, the line said "hold" - and the plan
    grew 424 -> 448. "Hold" held the MEDIAN, and the median rose with every
    week run above plan. Held means no bigger than the week planned before."""
    from conftest import make_day

    # Rolling weeks ending today: 200, 250, 300, 350, 400 min - each run above
    # the plan before it - and Garmin's ratio over 1.3 now and a week ago.
    for k, minutes in enumerate((400, 350, 300, 250, 200)):
        _run(store, today - timedelta(days=7 * k + 1), minutes)
    for back in (0, 7):
        store.upsert_daily(make_day(today - timedelta(days=back), hrv_status="BALANCED",
                                    sleep_score=80, body_battery_high=80, acwr_ratio=1.35))
    # Neither this week nor the one before is a down week (every 4th, weeks % 4 == 3).
    monday = planning.CADENCE_EPOCH + timedelta(weeks=201)
    budget, lines, mode = plan._week_plan(store, today, monday)
    before, _, _ = plan._week_plan(store, today - timedelta(days=7), monday - timedelta(days=7))
    assert mode == "hold" and budget <= before, (budget, before, lines)
    assert "no bigger than the" in lines[-1], lines


# ── the athlete's own days (habits.py) ───────────────────────────────────────

def _habit_weeks(store, today: date, n: int, runs: dict, other: dict | None = None,
                 end_weeks_ago: int = 1):
    """`n` Monday-weeks ending `end_weeks_ago` weeks before this one: `runs`
    maps a weekday (0 = Monday) to minutes, `other` to (type, minutes)."""
    monday = today - timedelta(days=today.weekday())
    for back in range(end_weeks_ago, end_weeks_ago + n):
        week = monday - timedelta(weeks=back)
        for wd, minutes in runs.items():
            _run(store, week + timedelta(days=wd), minutes)
        for wd, (kind, minutes) in (other or {}).items():
            _AID[0] += 1
            store.upsert_activity(make_activity(_AID[0], week + timedelta(days=wd), activity_type=kind,
                                                duration_s=minutes * 60, load=None, aerobic_te=3.0,
                                                anaerobic_te=0.2))


def _week_days(out: str) -> dict:
    import re

    return {m[0]: m[1] for m in re.findall(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \S+: (\w[^(]*?)\s+\(",
                                           out, re.M)}


def test_a_saturday_long_runner_gets_the_long_run_on_saturday_without_a_profile(store, today):
    _habit_weeks(store, today, 8, {1: 40, 3: 40, 5: 90})
    out = tools.propose_week(store)
    days = _week_days(out)
    assert days.get("Sat", "").startswith("Long run"), out
    assert set(days) <= {"Tue", "Thu", "Sat"}, out
    assert "your longest run fell on sat in 8 of 8 weeks" in out, out


def test_a_runner_who_runs_twice_a_week_is_planned_two_days(store, today):
    _habit_weeks(store, today, 8, {2: 40, 6: 90})
    out = tools.propose_week(store)
    assert len(_week_days(out)) == 2, out
    assert "2 running days - from your history" in out, out


def test_no_quality_run_on_or_right_after_the_weekly_gym_day(store, today):
    _habit_weeks(store, today, 8, {2: 45, 4: 45, 5: 45, 6: 90}, other={1: ("strength_training", 60)})
    out = tools.propose_week(store)
    days = _week_days(out)
    hard = [d for d, name in days.items() if name.split()[0] in ("VO2max", "Threshold", "Steady")]
    assert hard and not set(hard) & {"Tue", "Wed"}, out


def test_after_a_pause_the_habits_are_the_ones_from_before_it(store, today):
    # Tuesday, Thursday, Saturday for eight weeks - then three weeks off.
    _habit_weeks(store, today, 8, {1: 40, 3: 40, 5: 90}, end_weeks_ago=4)
    out = tools.propose_week(store)
    assert "return week" in out, out
    assert set(_week_days(out)) <= {"Tue", "Thu", "Sat"}, out


def test_a_step_week_is_never_smaller_than_the_week_planned_before_it():
    """Found by the synthetic athletes: four weeks after a comeback the base
    switched from the last seven days back to the median of the climbing
    weeks, and the next week - labelled a step - came out smaller than the one
    before it (153 -> 145)."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent / "evals"))
    import sim

    course = sim.simulate(sim.jittered(sim.PERSONAS["pause_and_return"], 1002), weeks=16)
    weeks = [r for r in course.days if r.week]
    for before, now in zip(weeks, weeks[1:], strict=False):
        if now.week["mode"] == "step" and before.week["mode"] in ("step", "hold"):
            planned_before = sum(m for _, _, m in before.week["sessions"])
            planned_now = sum(m for _, _, m in now.week["sessions"])
            assert planned_now >= planned_before - 5, (now.day, planned_before, planned_now)


def test_no_down_week_while_rebuilding_after_a_pause():
    """Found by the synthetic athletes: after a 25-day pause a down week landed
    in week six of the way back, at 60 % of the old level, and with the median
    lagging the climb the runner was at 68 % of their old weeks after eight. A
    down week relieves a full load; a rebuild has none."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent / "evals"))
    import sim

    course = sim.simulate(sim.jittered(sim.PERSONAS["pause_and_return"], 1010), weeks=16)
    (last_before, first_back), = sim._gaps(course)
    level = max(sim._window(course, last_before - timedelta(days=7 * k)) for k in range(4))
    weeks = [r for r in course.days if r.week and r.day > first_back]
    rebuilding = [r for r in weeks if sim._window(course, r.day) < 0.8 * level]
    assert rebuilding and all(r.week["mode"] != "down" for r in rebuilding), \
        [(str(r.day), r.week["mode"], sim._window(course, r.day)) for r in rebuilding]


def test_three_held_weeks_in_a_row_stay_held(store, today):
    """Review finding: the cap compared with the week before as recomputed
    WITHOUT its own cap, so a chain of holds let the third one grow again
    (275, 275, 325 with the line "no bigger than the 325 planned a week ago")."""
    from conftest import make_day

    for k, minutes in enumerate((450, 400, 350, 300, 250, 200, 150)):
        _run(store, today - timedelta(days=7 * k + 1), minutes)
    for back in (0, 7, 14):
        store.upsert_daily(make_day(today - timedelta(days=back), hrv_status="BALANCED",
                                    sleep_score=80, body_battery_high=80, acwr_ratio=1.35))
    # Weeks 200-202 of the cadence: three in a row without a down week (weeks % 4 == 3).
    monday = planning.CADENCE_EPOCH + timedelta(weeks=202)
    now, lines, mode = plan._week_plan(store, today, monday)
    before, _, before_mode = plan._week_plan(store, today - timedelta(days=7),
                                             monday - timedelta(days=7))
    assert mode == before_mode == "hold" and now <= before, (now, before, lines)


def test_the_rebuild_ends_once_the_athlete_is_back():
    """Review finding: the rebuild was re-checked every week, so the down week
    after it dipped the athlete under 80 % again and the next week was a
    "rebuild" once more - sized from the down week, down weeks suppressed."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent / "evals"))
    import sim

    course = sim.simulate(sim.PERSONAS["pause_and_return"], weeks=20)
    weeks = [r for r in course.days if r.week]
    first_down = next(i for i, r in enumerate(weeks)
                      if r.week["mode"] == "down" and r.day > sim.START + timedelta(weeks=10))
    after = [r for r in weeks[first_down + 1:]]
    assert after and not any("rebuilding after a pause" in r.week["lines"][0] for r in after), \
        [(str(r.day), r.week["lines"][0][:80]) for r in after]


def test_a_week_cut_to_one_quality_session_follows_the_next_stimulus():
    """Review finding: a week reduced to one quality session by a gym day
    always took VO2max - threshold never, and in a measurement week the steady
    block was dropped without a word."""
    sessions, _ = planning.build_week(date(2026, 6, 15), days_per_week=4, long_run_day="sun",
                                      quality=("vo2max", "steady"), quality_one="steady",
                                      avoid_hard=frozenset({"tue", "wed"}))
    assert [s.kind for _, s in sessions if s.kind in planning.HARD_KINDS] == ["steady"]
