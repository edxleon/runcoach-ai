"""Layer 1 of the athlete's memory: facts computed from the data, with their
evidence, and a change line only when a new value has held."""

from __future__ import annotations

from datetime import timedelta

from conftest import make_activity
from runcoach import facts


def test_a_change_is_reported_only_when_the_new_value_held():
    assert facts._change(["sat", "sat", "sat", "sun", "sun", "sun"]) == \
        "since about 2 weeks ago; before: sun"
    # New this week: not yet a habit.
    assert facts._change(["sat", "sun", "sun", "sun"]) is None
    # One odd week in the past is not a "before".
    assert facts._change(["sat", "sat", "sun", "sat", "sat"]) is None
    assert facts._change(["sat"] * 9) is None
    assert facts._change([None, None, "sun"]) is None


def test_the_text_for_a_prompt_is_cut_at_a_whole_fact():
    fs = [facts.Fact(f"k{i}", f"Label {i}", "x" * 40, "evidence", "your history", "2026-06-10")
          for i in range(10)]
    text = facts.render_text(fs, 300)
    assert text.endswith("more on the You tab)")
    assert all(line.startswith(("- Label", "(+")) for line in text.splitlines())


def test_a_new_athlete_is_told_what_is_not_known_yet(store, today):
    fs = facts.learned(store, today)
    days = next(f for f in fs if f.key == "days")
    assert days.value == "not known yet"


def test_the_facts_carry_the_athletes_days_and_long_run(store, today):
    monday = today - timedelta(days=today.weekday())
    aid = 7_000_000_000
    for back in range(1, 9):
        week = monday - timedelta(weeks=back)
        for wd, minutes in ((1, 40), (3, 40), (5, 90)):
            aid += 1
            store.upsert_activity(make_activity(aid, week + timedelta(days=wd),
                                                duration_s=minutes * 60, load=None))
    fs = {f.key: f for f in facts.learned(store, today)}
    assert fs["long_run"].value == "sat"
    assert fs["weekdays"].value == "tue, thu, sat"
    assert fs["consistency"].value == "8 of the last 8 weeks with a run"


def test_days_that_were_not_fixed_before_say_so(store, today):
    """A change from a rotating rhythm to fixed days showed "before: " with nothing."""
    monday = today - timedelta(days=today.weekday())
    aid = 7_100_000_000
    d = monday - timedelta(weeks=20)
    while d < monday - timedelta(weeks=8):                  # every other day: no fixed weekdays
        aid += 1
        store.upsert_activity(make_activity(aid, d, duration_s=2700, load=None))
        d += timedelta(days=2)
    for back in range(1, 9):
        week = monday - timedelta(weeks=back)
        for wd, minutes in ((1, 40), (3, 40), (5, 90)):
            aid += 1
            store.upsert_activity(make_activity(aid, week + timedelta(days=wd),
                                                duration_s=minutes * 60, load=None))
    change = {f.key: f for f in facts.learned(store, today)}["weekdays"].change
    assert change and change.endswith("before: no fixed weekdays")


def test_the_fingerprint_moves_with_a_value_not_with_the_evidence():
    a = [facts.Fact("long_run", "Long run", "sat", "8 of 8", "your history", "2026-06-10")]
    b = [facts.Fact("long_run", "Long run", "sat", "7 of 8", "your history", "2026-06-17")]
    c = [facts.Fact("long_run", "Long run", "sun", "5 of 8", "your history", "2026-06-17")]
    assert facts.fingerprint(a) == facts.fingerprint(b) != facts.fingerprint(c)
