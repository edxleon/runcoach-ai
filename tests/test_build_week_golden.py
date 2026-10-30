"""`build_week` as it plans today, frozen before the habits change it.

The planner learns the athlete's days from their history (habits.py). Without
habits it must plan exactly the week it planned before - the ~100 planner
tests and the pinned eval fixtures stand on that. This file records the week
for every combination of start weekday, days, long-run day, rep variant,
quality, budget and session cap; a change shows up as a line diff.

    RUNSPECTOR_REGEN_GOLDEN=1 uv run pytest -q tests/test_build_week_golden.py
regenerates it - only when a change to the planner is meant to move weeks.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date
from itertools import product
from pathlib import Path

from runspector import planning

GOLDEN = Path(__file__).parent / "golden" / "build_week.txt"

STARTS = (date(2026, 6, 15), date(2026, 6, 17))          # a Monday, a Wednesday
QUALITY = {"default": None, "measure": ("vo2max", "steady"), "steady": ("steady",), "none": ()}
BUDGETS = ((None, None), (70, None), (180, None), (320, None), (180, 45))


def _line(start, days, long_day, variant, qkey, budget, cap, long_run=True) -> str:
    sessions, assumptions = planning.build_week(
        start, days_per_week=days, long_run_day=long_day, quality=QUALITY[qkey],
        variant=variant, total_minutes=budget, max_session_min=cap, long_run=long_run)
    # The name only where it says more than kind and minutes: the rep structure.
    week = " ; ".join(f"{d:%a} {s.kind} {round(s.estimated_seconds / 60)}"
                      + (f" {s.name!r}" if s.kind in planning.HARD_KINDS else "")
                      for d, s in sessions)
    # In blocks of four: a hex digest can start with eleven digits, and the
    # PII gate rightly takes a long digit run for an account id.
    h = hashlib.sha1("\n".join(assumptions).encode()).hexdigest()
    digest = f"{h[:4]}-{h[4:8]}-{h[8:12]}"
    key = f"{start:%a}|{days}|{long_day}|v{variant}|{qkey}|{budget}|{cap}|{'L' if long_run else 'E'}"
    return f"{key} => {week} # {digest}"


def render() -> str:
    lines = []
    for start, days, long_day, variant, qkey, (budget, cap) in product(
            STARTS, range(3, 8), planning.WEEKDAYS, (0, 1), QUALITY, BUDGETS):
        lines.append(_line(start, days, long_day, variant, qkey, budget, cap))
    for start, days, long_day, (budget, cap) in product(
            STARTS, range(3, 8), planning.WEEKDAYS, BUDGETS):
        lines.append(_line(start, days, long_day, 0, "none", budget, cap, long_run=False))
    return "\n".join(lines) + "\n"


def test_without_habits_the_week_is_the_week_it_always_was():
    text = render()
    if os.environ.get("RUNSPECTOR_REGEN_GOLDEN"):
        GOLDEN.write_text(text, encoding="utf-8")
    want = GOLDEN.read_text(encoding="utf-8")
    if text != want:
        import difflib

        diff = list(difflib.unified_diff(want.splitlines(), text.splitlines(), "golden",
                                         "now", lineterm="", n=0))
        raise AssertionError(f"{sum(1 for x in diff if x.startswith('+') and not x.startswith('+++'))}"
                             " weeks changed:\n" + "\n".join(diff[:40]))
