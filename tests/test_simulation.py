"""Synthetic athletes run 16 weeks of the app, and a runner's expectations of
a coach must hold over the whole course (`evals/sim.py`).

Each persona runs on its own fixed seed in every test run. Set
RUNSPECTOR_SIM_SEEDS=N to run N more seeds per persona with the events moved
around - a pause in another week, of another length: that is the bug hunt,
not a gate. A course it breaks becomes a fixed scenario in
`test_what_comes_next.py`, red before the fix.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import sim  # noqa: E402

_EXTRA = int(os.environ.get("RUNSPECTOR_SIM_SEEDS") or 0)
CASES = [(name, p.seed) for name, p in sim.PERSONAS.items()] + \
    [(name, 1000 + i) for name in sim.PERSONAS for i in range(_EXTRA)]


@pytest.mark.parametrize(("persona", "seed"), CASES, ids=[f"{n}-{s}" for n, s in CASES])
def test_a_runner_s_expectations_hold_over_the_whole_course(persona, seed):
    course = sim.simulate(sim.jittered(sim.PERSONAS[persona], seed), weeks=16)
    broken = sim.check(course)
    assert not broken, "\n".join(broken) + "\n\n" + sim.table(course)


def test_the_expectations_can_fail():
    """A check that never fires proves nothing: a course in which the app
    calls a hard day while the athlete is ill, plans more than was ever run
    and keeps the athlete off their feet must be caught on every count."""
    course = sim.simulate(sim.PERSONAS["follower"], weeks=4)
    day = course.days[10]
    day.ill, day.decision = True, "hard"
    for r in course.days[12:16]:
        r.decision, r.ill = "rest", False
    sunday = next(r for r in course.days if r.week)
    sunday.week = {**sunday.week, "sessions": [(sunday.day, "vo2max", 400)]}
    broken = "\n".join(sim.check(course))
    course.days[3].morning = f"{course.days[3].day:%a %d %b}: no values for today yet"
    for expected in ("ill (HRV low", "fourth rest call", "plans 400 min", "more hard sessions",
                     "no sound easier option", "the morning text says"):
        assert expected in broken, (expected, broken)


def test_the_pain_expectations_can_fail():
    """The `pain_after_quality` checks fire on a day it hurts and the app said
    hard, and on a hard day right after the pain ended."""
    course = sim.simulate(sim.PERSONAS["pain_after_quality"], weeks=16)
    k = next(i for i, r in enumerate(course.days) if r.hurts and not course.days[i + 1].hurts)
    course.days[k].decision = "hard"
    course.days[k + 1].decision = "hard"
    broken = "\n".join(sim.check(course))
    assert "the runner said it hurts and the app said hard" in broken, broken
    assert "days of pain ended, and the app said hard" in broken, broken


def test_a_word_the_app_never_lets_run_out_is_caught(monkeypatch):
    """Review: the rest-streak check once trusted the app's own label for a
    check-in day, so an app that kept a pain open for 40 days passed. Keyed on
    the runner's own pain, the streak of rest days without it is caught."""
    from runspector import logic

    monkeypatch.setitem(logic.CHECK_IN_LAPSE_DAYS, "pain", 40)
    course = sim.simulate(sim.PERSONAS["pain_after_quality"], weeks=16)
    assert "fourth rest call in a row" in "\n".join(sim.check(course))


def test_the_even_week_expectation_can_fail():
    """The `daily_even` check fires when the coach steps up after seven alike,
    heavy days - checked on its own course with the last Sunday's plan turned
    into a step."""
    course = sim.simulate(sim.PERSONAS["daily_even"], weeks=4)
    sunday = [r for r in course.days if r.week][-1]
    sunday.week = {**sunday.week, "mode": "step"}
    assert "seven alike days" in "\n".join(sim.check(course))


def test_the_short_day_expectation_can_fail():
    """The `short_tuesday` check fires on a week that puts a long hard session
    on the runner's short day while another day holds a hard one."""
    course = sim.simulate(sim.PERSONAS["short_tuesday"], weeks=2)
    sunday = [r for r in course.days if r.week][-1]
    tuesday = sunday.day + sim.timedelta(days=2)
    sunday.week = {**sunday.week, "sessions": [(tuesday, "vo2max", 50),
                                               (tuesday + sim.timedelta(days=2), "threshold", 50)]}
    broken = "\n".join(sim.check(course))
    assert "a day with 30 min" in broken and "the short day" in broken, broken


def test_the_profile_and_late_yes_expectations_can_fail():
    """The contradictory profile's check fires on a session on a day off; the
    late yes's check fires on an entry scheduled into the past and on a skipped
    day the result does not name."""
    from datetime import timedelta

    course = sim.simulate(sim.PERSONAS["contradictory"], weeks=2)
    sunday = [r for r in course.days if r.week][-1]
    monday = sunday.day + timedelta(days=1)
    sunday.week = {**sunday.week, "sessions": [(monday, "easy", 40)]}
    assert "sessions on the athlete's days off" in "\n".join(sim.check(course))

    course = sim.simulate(sim.PERSONAS["late_yes"], weeks=2)
    yes = next(r for r in course.days if r.applied)
    sessions, result, schedule = yes.applied
    earlier = (yes.day - timedelta(days=2)).isoformat()
    yes.applied = (sessions, {**result, "warnings": []}, {**schedule, 1: {"date": earlier}})
    broken = "\n".join(sim.check(course))
    assert "scheduled into the past" in broken and "does not say" in broken, broken
