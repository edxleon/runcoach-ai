"""What the planner and the Today tab say comes next, against the situations a
real runner is in: weeks that shrank, a long pause, a first run back, a plateau,
a plan already on the calendar. Each test is one scenario from the v0.3 review,
written from the scenario rather than from the code."""

from __future__ import annotations

from datetime import date, timedelta

from conftest import make_activity, make_day
from runspector import logic, plan, planning, snapshot, tools
from runspector.models import ScheduledWorkout

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
    # By kind, not by target: with a pace source the reps carry no zone at all.
    assert not any(f"({k};" in out for k in planning.HARD_KINDS), out
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
    from runspector import plan as plan_mod  # noqa: F401

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
    from runspector.models import DailyMetrics

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



def test_a_held_week_after_a_down_week_is_no_bigger_than_the_week_before_that(store, today):
    """Found by the synthetic `daily_even` runner: held 293, then the down week,
    then "hold" again at 353 - the comparison skipped the down week and with it
    every check. Held means no bigger than the last regular week planned."""
    from conftest import make_day

    for k, minutes in enumerate((400, 350, 300, 250, 200, 200)):
        _run(store, today - timedelta(days=7 * k + 1), minutes)
    for back in (0, 7, 14):
        store.upsert_daily(make_day(today - timedelta(days=back), hrv_status="BALANCED",
                                    sleep_score=80, body_battery_high=80, acwr_ratio=1.35))
    # The week before is a down week (weeks % 4 == 3), the one before it is not.
    monday = planning.CADENCE_EPOCH + timedelta(weeks=204)
    budget, lines, mode = plan._week_plan(store, today, monday)
    _, _, mode_before = plan._week_plan(store, today - timedelta(days=7), monday - timedelta(days=7))
    regular, _, mode_regular = plan._week_plan(store, today - timedelta(days=14),
                                               monday - timedelta(days=14))
    assert mode_before == "down" and mode_regular == "hold", (mode_before, mode_regular)
    assert mode == "hold" and budget <= regular, (budget, regular, lines)

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



def _planned_minutes(proposal: dict) -> dict:
    """Weekday -> (kind, minutes) of a proposal's sessions."""
    from datetime import date as _date

    out = {}
    for it in proposal["items"]:
        d = _date.fromisoformat(it["day"])
        out[planning.WEEKDAYS[d.weekday()]] = (it["spec"]["kind"],
                                               round(it["spec"]["estimated_seconds"] / 60))
    return out


def test_a_short_weekday_stays_as_short_as_the_athlete_runs_it(store, today):
    """Tuesday is the athlete's short day - 35 minutes, every week, while the
    other runs go an hour. A planner that sizes by kind put 45 there, and a
    hard session that does not fit 35 minutes; neither is the athlete's week."""
    _habit_weeks(store, today, 8, {1: 35, 3: 60, 5: 60, 6: 100})
    week = _planned_minutes(plan.propose_week(store, today=today))
    assert "tue" in week, week
    kind, minutes = week["tue"]
    assert minutes <= 35 and kind not in planning.HARD_KINDS, week


def test_a_runner_whose_runs_are_all_alike_has_no_short_day(store, today):
    """The counter-case: equal runs on every day are a rhythm, not a limit -
    capping each day at its longest run would freeze the week for good."""
    _habit_weeks(store, today, 8, {1: 50, 3: 50, 5: 50, 6: 90})
    out = plan.propose_week(store, today=today)
    assert not any("short" in a for a in out["preview"].splitlines() if "assumption:" in a), \
        out["preview"]

# ── the athlete's own settings (the You tab, profile.py) ─────────────────────

def _set_profile(**values):
    import json

    from runspector import paths

    paths.profile_path().write_text(json.dumps(values), encoding="utf-8")


def _hard(days: dict) -> set:
    return {d for d, name in days.items() if name.split()[0] in ("VO2max", "Threshold", "Steady")}


def test_a_day_the_athlete_cannot_run_on_gets_no_run(store, today):
    """The history says Thursday; the athlete says Thursday is gone."""
    _habit_weeks(store, today, 8, {1: 40, 3: 40, 5: 90})
    _set_profile(off_days=["thu"])
    out = tools.propose_week(store)
    days = _week_days(out)
    assert "Thu" not in days and len(days) == 3, out
    assert days.get("Sat", "").startswith("Long run"), out


def test_a_long_run_day_made_a_day_off_moves_the_long_run(store, today):
    _habit_weeks(store, today, 8, {1: 40, 3: 40, 5: 90})
    _set_profile(off_days=["sat"])
    out = tools.propose_week(store)
    days = _week_days(out)
    assert "Sat" not in days and days.get("Sun", "").startswith("Long run"), out
    assert "your usual sat is a day off now" in out, out


def test_the_days_the_athlete_set_beat_the_history(store, today):
    _habit_weeks(store, today, 8, {1: 40, 3: 40, 5: 90})
    _set_profile(run_days=["mon", "wed", "fri"], long_run_day="sun", days_per_week=4)
    out = tools.propose_week(store)
    days = _week_days(out)
    assert days.get("Sun", "").startswith("Long run"), out
    assert set(days) == {"Mon", "Wed", "Fri", "Sun"}, out
    assert "from your history" not in out and "your longest run fell on" not in out, out


def test_a_sport_day_the_athlete_set_keeps_the_quality_off_it(store, today):
    _habit_weeks(store, today, 8, {1: 45, 3: 45, 4: 45, 6: 90})
    _set_profile(sport_days=["tue"])
    days = _week_days(out := tools.propose_week(store))
    assert _hard(days) and not _hard(days) & {"Tue", "Wed"}, out


def test_more_days_than_the_days_off_leave_are_cut_to_the_free_ones(store, today):
    _habit_weeks(store, today, 8, {1: 40, 3: 40, 5: 90})
    _set_profile(days_per_week=6, off_days=["mon", "wed", "fri"])
    out = tools.propose_week(store)
    days = _week_days(out)
    assert not {"Mon", "Wed", "Fri"} & set(days), out
    assert "mon, wed, fri are days off" in out, out


def test_several_days_off_still_get_every_free_day_that_was_asked_for(store, today):
    """Review finding: the easy runs only tried offsets 3, 5, 1, 6 - with the
    days off on exactly those, Tuesday and Thursday stayed empty, no line said so."""
    _set_profile(off_days=["mon", "fri", "sat"], days_per_week=3)
    days = _week_days(out := tools.propose_week(store))
    assert len(days) == 3 and not {"Mon", "Fri", "Sat"} & set(days), out
    _set_profile(off_days=["mon", "tue", "fri", "sat"], days_per_week=4)
    days = _week_days(out := tools.propose_week(store))
    assert len(days) == 3 and "4 days asked, 3 planned" in out, out


def test_the_coach_is_told_the_week_the_planner_goes_by_not_the_raw_history(store, today):
    """Review finding: with Saturday made a day off, the planner moved the long
    run to Sunday while get_athlete_profile still said "Long run: sat"."""
    _habit_weeks(store, today, 8, {1: 40, 3: 40, 5: 90})
    _set_profile(off_days=["sat", "thu"])
    profile_text = tools.get_athlete_profile(store)
    assert "- Long run: sun - your usual sat is a day off now" in profile_text, profile_text
    assert "- Your days: tue -" in profile_text, profile_text
    out = tools.propose_week(store)
    assert "easy runs on your days first - you run on tue" in out, out
    assert "thu, sat are days off now" in out, out


def test_the_runs_a_week_shown_are_the_runs_the_planner_plans(store, today):
    """Round 2: the history's five days were shown next to three days off,
    while every week planned four."""
    from runspector import facts, habits

    _habit_weeks(store, today, 8, {0: 40, 1: 40, 3: 40, 4: 40, 5: 90})
    _set_profile(off_days=["mon", "wed", "fri"])
    week = facts.week_settings(store, today)
    assert week["effective"]["days_per_week"] == {"value": 4, "source": "history"}
    assert week["effective"]["off_days"] == {"value": ["mon", "wed", "fri"], "source": "you"}
    fs = {f.key: f for f in facts.learned(store, today)}
    assert fs["days"].value == "4 a week" and "mon, wed, fri are days off" in fs["days"].evidence
    assert len(_week_days(tools.propose_week(store))) == 4
    assert habits.effective({}, plan.athlete_habits(store, today))["days_per_week"].value == 5


# ── the shape of the load (monotony) ─────────────────────────────────────────

def _loaded(store, day: date, minutes: int, load: int):
    _AID[0] += 1
    store.upsert_activity(make_activity(_AID[0], day, duration_s=minutes * 60, load=load,
                                        aerobic_te=2.5, anaerobic_te=0.3))


#: Neither this week nor the one before is a down week (every 4th week).
_NO_DOWN_WEEK = planning.CADENCE_EPOCH + timedelta(weeks=201)


def test_a_week_of_the_same_run_every_day_is_not_stepped_up(store, today):
    """Five weeks of 50 minutes every single day: the ratio is a calm 1.0, the
    median is steady - and nothing told the planner the athlete never had an
    easier day. A heavy week with no variation is held, and the line says why."""
    for back in range(35):
        _loaded(store, today - timedelta(days=back), 50, 80)
    budget, lines, mode = plan._week_plan(store, today, _NO_DOWN_WEEK)
    assert mode == "hold", (mode, lines)
    assert "monotony" in lines[0], lines


def test_the_same_load_with_rest_days_between_is_stepped_up(store, today):
    """The counter-case: the same weekly load in four runs and three rest days
    is ordinary training - the monotony rule must not touch it."""
    for back in range(35):
        if (today - timedelta(days=back)).weekday() in (0, 2, 4, 6):
            _loaded(store, today - timedelta(days=back), 87, 140)
    budget, lines, mode = plan._week_plan(store, today, _NO_DOWN_WEEK)
    assert mode == "step", (mode, lines)
    assert not any("monotony" in line for line in lines), lines


def test_an_even_but_light_week_is_not_held(store, today):
    """Even days are not a risk on their own: a light even week after heavier
    varied ones (a few easy jogs while busy) carries no strain."""
    for back in range(7, 35):
        if (today - timedelta(days=back)).weekday() in (0, 2, 4, 6):
            _loaded(store, today - timedelta(days=back), 87, 140)
    for back in range(7):
        _loaded(store, today - timedelta(days=back), 30, 40)
    budget, lines, mode = plan._week_plan(store, today, _NO_DOWN_WEEK)
    assert not any("monotony" in line for line in lines), lines


def test_the_coach_hears_about_an_even_heavy_week_and_only_then(store, today):
    for back in range(35):
        _loaded(store, today - timedelta(days=back), 50, 80)
    _green(store, today)
    out = tools.get_training_load(store)
    assert "Monotony, last 7 days" in out and "does not step the next week up" in out, out
    state = snapshot.assemble(store)["load"]["monotony"]
    assert state["monotony"] == logic.MONOTONY_MAX and state["base"] == 560, state


def test_a_varied_week_adds_nothing_to_the_coachs_load_text(store, today):
    for back in range(35):
        if (today - timedelta(days=back)).weekday() in (0, 2, 4, 6):
            _loaded(store, today - timedelta(days=back), 87, 140)
    _green(store, today)
    assert "Monotony" not in tools.get_training_load(store)


def test_the_short_day_is_on_the_you_tab_with_its_evidence(store, today):
    from runspector import facts

    _habit_weeks(store, today, 8, {1: 35, 3: 60, 5: 60, 6: 100})
    found = {f.key: f for f in facts.learned(store, today)}
    assert found["short_days"].value == "tue up to 35 min", found.get("short_days")
    assert "never went past 35 min" in found["short_days"].evidence


# ── Garmin's own readiness ───────────────────────────────────────────────────

def test_garmins_readiness_never_changes_the_apps_decision(store, today):
    """Garmin says 95, Garmin says 5 - the light, the reasons and the day's
    decision are the app's, from the same signals either way. Shown beside
    the light, read by no rule."""
    from conftest import make_day

    _run(store, today - timedelta(days=1), 50, z5=900)      # a hard day yesterday
    answers = []
    for score, level in ((95, "PRIME"), (5, "POOR"), (None, None)):
        store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=82,
                                    body_battery_high=80, garmin_readiness=score,
                                    garmin_readiness_level=level))
        r = store.get_readiness(today)
        d = snapshot.assemble(store, today=today)["decision_today"]
        answers.append((r["verdict"], tuple(r["reasons"]), d["decision"], d["sentence"]))
    assert answers[0] == answers[1] == answers[2], answers


def test_the_coach_sees_garmins_score_as_garmins_opinion(store, today):
    from conftest import make_day

    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=82,
                                body_battery_high=80, garmin_readiness=85,
                                garmin_readiness_level="HIGH"))
    out = tools.get_training_readiness(store)
    assert "Garmin's own Training Readiness: 85 (HIGH) - its score, not this app's rule" in out
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=82,
                                body_battery_high=80))
    # make_day writes None for the score: a profile column keeps what it had.
    assert "Garmin's own Training Readiness: 85" in tools.get_training_readiness(store)


# ── body weight ──────────────────────────────────────────────────────────────

def test_the_coach_hears_how_much_of_a_vo2max_rise_the_weight_alone_explains(store, today):
    from conftest import make_day

    for back in range(40, -1, -1):
        d = today - timedelta(days=back)
        store.upsert_daily(make_day(d, hrv_status="BALANCED", sleep_score=80,
                                    vo2max=50.0 if back > 10 else 51.0))
        if back % 3 == 0:
            store.replace_weights(d, d, {d: 73.0 if back > 20 else 71.4})
    out = tools.get_vo2max_history(store)
    assert "Body weight: 71.4 kg" in out and "-1.6 kg since" in out, out
    assert "the weight alone moves it by about +1.1" in out, out


def test_the_coach_is_told_the_hold_the_planner_will_make_on_the_same_days(store, today):
    """Review finding: the tool read the seven days to the last day with a
    recovery row, the planner the seven days to today - before the morning
    sync the coach said "the planner holds the next week" and the proposal
    stepped it up. One window, both places."""
    from conftest import make_day

    for back in range(1, 36):         # runs up to yesterday: the morning before today's
        _loaded(store, today - timedelta(days=back), 50, 100)
    for back in range(1, 30):        # recovery rows up to YESTERDAY only
        store.upsert_daily(make_day(today - timedelta(days=back), hrv_status="BALANCED",
                                    sleep_score=80))
    said = "does not step the next week up" in tools.get_training_load(store)
    _, lines, mode = plan._week_plan(store, today, _NO_DOWN_WEEK)
    assert said == (mode != "step"), (said, mode, lines)


def test_a_short_day_that_blocks_nothing_costs_the_week_no_quality_session(store, today):
    """Planner review: a short Monday - the day after the long run, never a
    quality slot - cut every week to one quality session, and the line said
    two "do not fit". Only a short day that pushes the pair off the athlete's
    days may trade it for one on their day."""
    _habit_weeks(store, today, 8, {0: 25, 2: 50, 5: 45, 6: 90})
    week = _planned_minutes(plan.propose_week(store, today=today))
    hard = [d for d, (k, _) in week.items() if k in planning.HARD_KINDS]
    assert len(hard) == 2, week


def test_the_short_day_line_says_why_there_is_one_quality_session(store, today):
    _habit_weeks(store, today, 8, {1: 35, 3: 60, 5: 60, 6: 100})
    preview = plan.propose_week(store, today=today)["preview"]
    line = next(ln for ln in preview.splitlines() if "one quality session this week" in ln)
    assert "do not fit" not in line and "on a day you run" in line, line


def test_a_hold_from_alike_days_does_not_blame_the_load_ratio(store, today):
    for back in range(35):
        _loaded(store, today - timedelta(days=back), 50, 80)
    _, lines, mode = plan._week_plan(store, today, _NO_DOWN_WEEK)
    assert mode == "hold" and not any("raised the ratio" in ln for ln in lines), lines


def test_a_short_day_that_leaves_one_of_the_runners_days_keeps_both_sessions(store, today):
    """Round 2: Tue/Thu/Sat/Sun runner, short Thursday. Tue and Fri still hold
    two sessions 48 hours apart, one of them on a day they run - the trade to
    a single session is for a short day that leaves NONE of their days."""
    _habit_weeks(store, today, 8, {1: 50, 3: 30, 5: 55, 6: 100})
    week = _planned_minutes(plan.propose_week(store, today=today))
    hard = sorted(d for d, (k, _) in week.items() if k in planning.HARD_KINDS)
    assert len(hard) == 2 and "thu" not in hard, week


# ── the athlete's own word ───────────────────────────────────────────────────

def _decision(store, today):
    return snapshot.assemble(store, today=today)["decision_today"]


def test_a_cold_said_before_the_watch_sees_it_is_a_rest_day(store, today):
    """HRV, sleep and Body Battery are green - the watch reacts to a cold a day
    or two late. The athlete's word is the first signal there is."""
    _green(store, today)
    assert _decision(store, today)["decision"] == "hard"
    tools.check_in(store, "ill")
    d = _decision(store, today)
    assert d["decision"] == "rest" and "ill" in d["sentence"], d
    assert "stimulus" not in d, d
    out = tools.get_training_readiness(store)
    assert "The app's decision for today: REST" in out and "Which hard session" not in out, out


def test_a_check_in_made_before_the_morning_sync_still_counts(store, today):
    """No values for today yet: the readiness light cannot be called, the day
    can - the athlete said they are ill."""
    _green(store, today - timedelta(days=1))
    tools.check_in(store, "ill")
    assert _decision(store, today)["decision"] == "rest"


def test_pain_stops_the_run_and_sore_legs_only_the_hard_session(store, today):
    _green(store, today)
    tools.check_in(store, "sore")
    d = _decision(store, today)
    assert d["decision"] == "easy" and "sore" in d["sentence"], d
    tools.check_in(store, "pain", note="left calf")
    assert _decision(store, today)["decision"] == "rest"


def test_stress_said_by_the_athlete_takes_the_hard_session(store, today):
    _green(store, today)
    tools.check_in(store, "stressed")
    d = _decision(store, today)
    assert d["decision"] == "easy" and "stress" in d["sentence"], d


def test_after_a_cold_as_many_easy_days_as_it_lasted(store, today):
    """Ill Sunday to Tuesday, over on Wednesday: Wednesday, Thursday and Friday
    are easy, Saturday may be hard again."""
    store.say_check_in("ill", today - timedelta(days=3))
    store.say_check_in("ill", today - timedelta(days=1))
    tools.check_in(store, "ill", over=True)
    for back in range(5):
        _green(store, today + timedelta(days=back))
    for ahead, want in ((0, "easy"), (1, "easy"), (2, "easy"), (3, "hard")):
        d = _decision(store, today + timedelta(days=ahead))
        assert d["decision"] == want, (ahead, d)
    assert "3 days" in _decision(store, today)["sentence"], _decision(store, today)


def test_a_cold_nobody_ends_runs_out_and_the_days_after_are_still_easy(store, today):
    """Said once, four days ago, never again: it held three days, then ran out -
    and the three days it held still cost three easy days."""
    store.say_check_in("ill", today - timedelta(days=4))
    _green(store, today)
    d = _decision(store, today)
    assert d["decision"] == "easy" and "ill" in d["sentence"], d
    _green(store, today + timedelta(days=2))
    assert _decision(store, today + timedelta(days=2))["decision"] == "hard"


def test_saying_it_again_keeps_it_open(store, today):
    store.say_check_in("pain", today - timedelta(days=6))
    store.say_check_in("pain", today - timedelta(days=4))
    store.say_check_in("pain", today - timedelta(days=2))
    _green(store, today)
    d = _decision(store, today)
    assert d["decision"] == "rest", d


def test_a_check_in_taken_back_the_same_day_leaves_nothing_behind(store, today):
    """A tap by mistake, undone the same day, leaves no easy days behind."""
    _green(store, today)
    tools.check_in(store, "ill")
    assert tools.check_in(store, "ill", undo=True).startswith("Taken back:")
    assert _decision(store, today)["decision"] == "hard"


def test_the_morning_text_says_what_the_athlete_said(store, today):
    _green(store, today)
    tools.check_in(store, "pain", note="left calf")
    text, fresh = tools.today_text(store)
    assert fresh and text.splitlines()[0].startswith(f"{today:%a %d %b}: REST"), text
    assert "pain" in text and "left calf" in text, text


def test_a_week_planned_while_it_hurts_puts_no_hard_session_on_the_first_days(store, today):
    """Sunday, the calf has hurt since Friday: the week is not a step up, and
    no hard session lands before the earliest day the day rule could allow one
    - had the athlete said "over" on Monday, three easy days after three days
    of pain - so the watch never shows intervals the app will call off."""
    _weeks(store, today, [200, 200, 200, 200])
    sunday = today + timedelta(days=(6 - today.weekday()) % 7)
    store.say_check_in("pain", sunday - timedelta(days=2), note="calf")
    store.say_check_in("pain", sunday)
    p = plan.propose_week(store, today=sunday, days_per_week=4, long_run_day="sun")
    monday = sunday + timedelta(days=1)
    hard_from = monday + timedelta(days=3)          # over Mon at the earliest: Mon-Wed easy
    early = {d: k for d, k in _planned_kinds(p).items()
             if k in planning.HARD_KINDS and d < hard_from}
    assert not early, (early, p["preview"])
    assert any(k in planning.HARD_KINDS for k in _planned_kinds(p).values()), p["preview"]
    lines = p["preview"]
    assert "you said you are in pain" in lines and "no step up" in lines, lines
    assert "easy until" in lines, lines


def test_a_week_planned_after_the_pain_ended_keeps_its_easy_days(store, today):
    _weeks(store, today, [200, 200, 200, 200])
    sunday = today + timedelta(days=(6 - today.weekday()) % 7)
    store.say_check_in("ill", sunday - timedelta(days=4))
    store.say_check_in("ill", sunday - timedelta(days=2))
    assert store.end_check_in("ill", sunday) == "over"      # ill Wed-Sat: easy Sun-Wed
    week = _planned_kinds(plan.propose_week(store, today=sunday, days_per_week=4,
                                            long_run_day="sun"))
    early = {d: k for d, k in week.items()
             if k in planning.HARD_KINDS and d < sunday + timedelta(days=4)}
    assert not early, week


def _planned_kinds(proposal: dict) -> dict:
    """Day -> kind of a proposal's sessions."""
    return {date.fromisoformat(it["day"]): it["spec"]["kind"] for it in proposal["items"]}


def test_a_sick_athlete_never_gets_a_bigger_week_than_a_healthy_one(store):
    """Review: "ill" said for the first time on the Sunday of a full week
    dropped the due down week, and the hold made the week 200 min instead of
    140 - with a line claiming the week just run was lighter."""
    sunday = planning.CADENCE_EPOCH + timedelta(weeks=3) - timedelta(days=1)   # down week next
    assert planning.down_week_due(sunday + timedelta(days=1))[0]
    _weeks(store, sunday + timedelta(days=1), [200, 200, 200, 200])
    healthy, _, mode = plan._week_plan(store, sunday)
    assert mode == "down", mode
    store.say_check_in("ill", sunday)
    sick, lines, _ = plan._week_plan(store, sunday)
    assert sick <= healthy, (sick, healthy, lines)
    assert not any("already lighter" in ln for ln in lines), lines


def test_no_session_is_proposed_for_a_day_the_athlete_is_ill(store, today):
    """Review: the gate refused only hard kinds, and its own text pointed the
    coach to "an easy run" on a day the decision says no run at all."""
    store.say_check_in("ill", today)
    for kind in ("easy", "long", "vo2max"):
        try:
            plan.propose(store, kind, duration_min=40 if kind == "easy" else 75, today=today)
        except ValueError as exc:
            assert "easy run" not in str(exc), exc
            continue
        raise AssertionError(f"a {kind} session was filed for a day the athlete is ill")


def test_a_pain_that_went_away_the_same_day_still_leaves_an_easy_morning(store, today):
    """Review: "over" on the day it was said deleted the row - a calf that
    hurt in the morning and was fine by the evening left no easy day behind."""
    store.say_check_in("pain", today)
    store.end_check_in("pain", today)
    tomorrow = today + timedelta(days=1)
    _green(store, tomorrow)
    d = _decision(store, tomorrow)
    assert d["decision"] == "easy" and "pain" in d["sentence"], d


_CHECK_INS = {
    "ill said today": lambda s, d: s.say_check_in("ill", d),
    "pain one day, over yesterday": lambda s, d: (s.say_check_in("pain", d - timedelta(days=2)),
                                                  s.end_check_in("pain", d - timedelta(days=1))),
    "pain Tue, over Wed": lambda s, d: (s.say_check_in("pain", d - timedelta(days=5)),
                                        s.end_check_in("pain", d - timedelta(days=4))),
    "ill four days, over three days ago": lambda s, d: (
        s.say_check_in("ill", d - timedelta(days=7)), s.say_check_in("ill", d - timedelta(days=5)),
        s.end_check_in("ill", d - timedelta(days=3))),
    "ill still open since Thursday": lambda s, d: (s.say_check_in("ill", d - timedelta(days=3)),
                                                   s.say_check_in("ill", d - timedelta(days=1))),
    "sore today": lambda s, d: s.say_check_in("sore", d),
    "stressed since Friday": lambda s, d: s.say_check_in("stressed", d - timedelta(days=2)),
}


def test_a_check_in_never_makes_a_week_bigger(tmp_path):
    """Round 2 closed the class, not the case: whatever the athlete said, the
    week planned on the same history is never bigger than without it - in a
    down week, a step, a held week or one after a lighter one."""
    from runspector.store import Store

    base = planning.CADENCE_EPOCH + timedelta(weeks=3) - timedelta(days=1)   # down week next
    sundays = [base, base + timedelta(weeks=1)]
    histories = [[200, 200, 200, 200], [200, 200, 200, 150], [120, 140, 160, 180], [300, 80, 300, 300]]
    i = 0
    for sunday in sundays:
        for weeks in histories:
            for name, said in _CHECK_INS.items():
                i += 1
                store = Store(tmp_path / f"w{i}.db")
                _weeks(store, sunday + timedelta(days=1), weeks)
                without, _, _ = plan._week_plan(store, sunday)
                said(store, sunday)
                with_, lines, _ = plan._week_plan(store, sunday)
                assert with_ <= without, (name, weeks, sunday, without, with_, lines)


def test_a_week_from_today_holds_no_run_today_while_the_athlete_is_ill(store, today):
    """Round 2: a week starting today, planned while ill, filed an easy run for
    today - the day the decision rests."""
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
    _weeks(store, monday, [200, 200, 200, 200])
    store.say_check_in("ill", monday)
    p = plan.propose_week(store, start=monday, today=monday, days_per_week=5, long_run_day="sun")
    days = _planned_kinds(p)
    assert monday not in days, (days, p["preview"])
    assert "no run today" in p["preview"], p["preview"]


def test_a_cold_that_ran_out_and_is_said_again_is_one_illness(store, today):
    """Fresh reader: "ill" on Monday, nothing until it ran out on Thursday,
    "ill" again on Thursday, "over" on Saturday - the second tap began a new
    illness, and two easy days followed six days ill."""
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
    store.say_check_in("ill", monday)
    assert store.say_check_in("ill", monday + timedelta(days=3)) == "said again"
    assert store.end_check_in("ill", monday + timedelta(days=5)) == "over"
    for ahead in range(5, 11):
        _green(store, monday + timedelta(days=ahead))
    calls = {ahead: _decision(store, monday + timedelta(days=ahead))["decision"]
             for ahead in range(5, 11)}
    # Ill Mon-Fri (5 days): Sat to Wed easy, Thursday may be hard again.
    assert calls == {5: "easy", 6: "easy", 7: "easy", 8: "easy", 9: "easy", 10: "hard"}, calls


def test_a_check_in_dated_back_into_an_ended_one_can_still_be_ended(store, today):
    """Fresh reader: Wed "ill", Thu "over", Fri the coach records "ill since
    Monday", Sat "over" - the Monday row could not be found to end, and the
    weekend stayed REST."""
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
    store.say_check_in("ill", monday + timedelta(days=2))
    store.end_check_in("ill", monday + timedelta(days=3))
    store.say_check_in("ill", monday + timedelta(days=4), since=monday)
    assert store.end_check_in("ill", monday + timedelta(days=5)) == "over"
    _green(store, monday + timedelta(days=5))
    assert _decision(store, monday + timedelta(days=5))["decision"] == "easy"
    assert len(store.check_ins(monday + timedelta(days=5))) == 1
