"""`habits.derive` and `habits.resolve`: the athlete's weekdays from their
history, and where each planning value comes from."""

from __future__ import annotations

from datetime import date, timedelta

from runspector import habits

TODAY = date(2026, 6, 10)                       # a Wednesday
MONDAY = TODAY - timedelta(days=TODAY.weekday())


def _weeks(n: int, runs: dict, *, other: dict | None = None, end_weeks_ago: int = 1) -> list[dict]:
    out = []
    for back in range(end_weeks_ago, end_weeks_ago + n):
        week = MONDAY - timedelta(weeks=back)
        for wd, minutes in runs.items():
            out.append({"day": week + timedelta(days=wd), "type": "running",
                        "duration_s": minutes * 60, "hard": False})
        for wd, (kind, minutes) in (other or {}).items():
            out.append({"day": week + timedelta(days=wd), "type": kind,
                        "duration_s": minutes * 60, "hard": False})
    return out


def test_the_days_the_long_run_and_the_preferred_weekdays():
    h = habits.derive(_weeks(8, {1: 40, 3: 40, 5: 90}), today=TODAY)
    assert h.known and h.weeks == 8
    assert h.days_per_week == 3 and h.long_run_day == "sat"
    assert set(h.preferred) == {"tue", "thu", "sat"}
    assert h.evidence["long"] == "your longest run fell on sat in 8 of 8 weeks"


def test_too_few_running_weeks_are_no_habits():
    h = habits.derive(_weeks(3, {1: 40, 5: 90}), today=TODAY)
    assert not h.known and h.long_run_day is None
    assert "only 3 week(s)" in h.evidence["weeks"]


def test_the_window_is_the_last_weeks_with_a_run_not_the_last_weeks():
    """Eight weeks of running, then six weeks off: an eight-week window would
    be empty right when the athlete starts again."""
    h = habits.derive(_weeks(8, {1: 40, 3: 40, 5: 90}, end_weeks_ago=7), today=TODAY)
    assert h.known and h.long_run_day == "sat"


def test_until_reads_only_the_weeks_before_a_pause():
    before = _weeks(8, {1: 40, 3: 40, 5: 90}, end_weeks_ago=6)
    after = _weeks(2, {0: 20, 2: 20, 4: 20, 6: 20}, end_weeks_ago=1)
    h = habits.derive(before + after, today=TODAY, until=MONDAY - timedelta(weeks=6))
    assert h.long_run_day == "sat" and set(h.preferred) == {"tue", "thu", "sat"}


def test_equal_runs_have_no_long_run_day():
    """In a week of equal runs "the longest" is whichever came first."""
    h = habits.derive(_weeks(8, {1: 45, 3: 45, 5: 45}), today=TODAY)
    assert h.known and h.long_run_day is None and "long" not in h.evidence


def test_a_rotating_rhythm_has_no_weekdays_of_its_own():
    acts = []
    d = MONDAY - timedelta(weeks=8)
    while d < MONDAY:
        acts.append({"day": d, "type": "running", "duration_s": 2700, "hard": False})
        d += timedelta(days=2)
    h = habits.derive(acts, today=TODAY)
    assert h.known and h.preferred == () and "different weekdays" in h.evidence["rhythm"]


def test_a_weekly_other_sport_keeps_that_day_and_the_next_clear():
    h = habits.derive(_weeks(8, {2: 45, 6: 90}, other={1: ("strength_training", 60)}), today=TODAY)
    assert h.avoid_hard == frozenset({"tue", "wed"})
    assert h.evidence["other"] == "another sport on tue (8/8)"


def test_a_walk_is_not_another_sport():
    h = habits.derive(_weeks(8, {2: 45, 6: 90}, other={1: ("walking", 60)}), today=TODAY)
    assert h.avoid_hard == frozenset()


def test_one_run_a_week_is_planned_as_two_and_says_so():
    h = habits.derive(_weeks(8, {6: 60}), today=TODAY)
    assert h.days_per_week == 2 and "planned as 2" in h.evidence["days"]


def test_the_argument_beats_the_profile_beats_the_history_beats_the_default():
    h = habits.derive(_weeks(8, {1: 40, 3: 40, 5: 90}), today=TODAY)
    assert habits.resolve(days_per_week=5, long_run_day="mon", profile={"days_per_week": 4},
                          habits=h) == (5, "mon", [])
    assert habits.resolve(days_per_week=None, long_run_day=None,
                          profile={"days_per_week": 4, "long_run_day": "sun"}, habits=h) == (4, "sun", [])
    days, long_day, lines = habits.resolve(days_per_week=None, long_run_day=None, profile={}, habits=h)
    assert (days, long_day) == (3, "sat")
    assert lines == ["3 running days - from your history: 3 run day(s) in a typical week "
                     "(median of 8 weeks)",
                     "long run on sat - your longest run fell on sat in 8 of 8 weeks"]


def test_without_a_history_the_default_lines_are_the_ones_they_always_were():
    days, long_day, lines = habits.resolve(days_per_week=None, long_run_day=None, profile={},
                                           habits=habits.NONE)
    assert (days, long_day) == (4, "sun")
    assert lines == ["4 running days - no days_per_week in the profile "
                     "(runspector profile --days-per-week N)",
                     "long run on sun - no long_run_day in the profile "
                     "(runspector profile --long-run-day sun)"]


def test_two_runs_of_almost_the_same_length_are_no_long_run_day():
    """Review: the margin was taken against the median of the other runs, so
    Sat 60 / Sun 59 with a short Tuesday named Saturday the long-run day."""
    h = habits.derive(_weeks(8, {1: 30, 5: 60, 6: 59}), today=TODAY)
    assert h.long_run_day is None


def test_a_half_in_the_median_of_run_days_rounds_up_either_way():
    """Review: `round` sends 2.5 to 2 and 3.5 to 4."""
    two_three = _weeks(4, {1: 40, 6: 80}) + _weeks(4, {1: 40, 3: 40, 6: 80}, end_weeks_ago=5)
    three_four = _weeks(4, {1: 40, 3: 40, 6: 80}) + _weeks(4, {1: 40, 3: 40, 5: 40, 6: 80},
                                                            end_weeks_ago=5)
    assert habits.derive(two_three, today=TODAY).days_per_week == 3
    assert habits.derive(three_four, today=TODAY).days_per_week == 4
