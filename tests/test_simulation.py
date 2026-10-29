"""Synthetic athletes run 16 weeks of the app, and a runner's expectations of
a coach must hold over the whole course (`evals/sim.py`).

Each persona runs on its own fixed seed in every test run. Set
RUNCOACH_SIM_SEEDS=N to run N more seeds per persona with the events moved
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

_EXTRA = int(os.environ.get("RUNCOACH_SIM_SEEDS") or 0)
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
    for expected in ("ill (HRV low", "fourth rest call", "plans 400 min", "more hard sessions"):
        assert expected in broken, (expected, broken)
