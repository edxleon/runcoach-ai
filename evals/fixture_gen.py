"""Render an eval fixture from the REAL tool output instead of writing it by hand.

Every other case in `cases.yaml` carries a hand-written `input` "in the exact
text shapes of `src/runcoach/tools.py`" — a claim nothing checked, and one that
was already false: the shipped `get_training_readiness` emits a decision block
and a calendar line that no fixture contained. So twelve cases were testing the
coach against a prompt shape it never actually receives.

This builds one case's input from a seeded in-memory store by calling the same
functions the MCP server calls. `tests/test_eval_fixture.py` re-renders it and
compares, so the fixture cannot drift from the tool again without going red.

    uv run python evals/fixture_gen.py    # print it, then paste it into the case
"""

from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evals"))

#: A Wednesday, so "the running week" has three days in it.
TODAY = date(2026, 6, 17)
CASE_ID = "decision-block-explained-not-replaced"


def build_store(tmp_db: str):
    """A green athlete whose week is full of zone 3 and empty above threshold.

    The point of the case: the app's rule says GO *and* names the grey zone, and
    the coach has to explain that rather than quietly substitute "your week is
    done". Both halves of the rule are visible in the numbers."""
    from datetime import datetime, timezone

    from runcoach.models import Activity, DailyMetrics
    from runcoach.store import Store

    store = Store(tmp_db)
    for i in range(30):
        day = TODAY - timedelta(days=i)
        m = DailyMetrics(day=day)
        m.hrv_status = "BALANCED"
        m.hrv_avg_ms = 62
        m.sleep_seconds = 27000
        m.sleep_score = 82
        m.body_battery_high = 79
        m.resting_hr = 48 if i else 47
        m.training_status = "PRODUCTIVE"
        m.acwr_ratio = 1.05
        m.acwr_status = "OPTIMAL"
        # One real change ten days back: the case is about the grey zone and
        # the stimulus the week lacks, not about a measurement being due - a
        # value flat for the whole window would make the next stimulus the
        # steady block and move the case off the rule it tests.
        # The step lands on day 10, a run day (runs are every second day):
        # only a change on a run day is a measurement.
        m.vo2max = 52.0 if i <= 10 else 51.8
        store.upsert_daily(m)

    # Four weeks of steady running, every session in the grey middle.
    aid = 9000
    for i in range(0, 28, 2):
        day = TODAY - timedelta(days=i)
        aid += 1
        store.upsert_activity(Activity(
            activity_id=aid,
            start_time=datetime(day.year, day.month, day.day, 8, 0, tzinfo=timezone.utc),
            activity_type="running", name="Steady run", distance_m=9500, duration_s=3000,
            avg_hr=158, max_hr=168, training_load=95, aerobic_te=2.8, anaerobic_te=0.3))
        store.update_activity_detail(aid, {
            "hr_z1_s": 300, "hr_z2_s": 1500, "hr_z3_s": 1200,
            "hr_z4_s": 0, "hr_z5_s": 0, "avg_cadence": 172, "splits": [],
        })
    return store


def render(store) -> str:
    from runcoach import tools

    parts = [
        "Athlete: Should I train today?",
        "",
        "=== get_training_readiness ===",
        tools.get_training_readiness(store),
        "",
        "=== get_training_load ===",
        tools.get_training_load(store, 28),
        "",
        "=== get_intensity_distribution ===",
        tools.get_intensity_distribution(store, 28),
    ]
    return "\n".join(parts).rstrip() + "\n"


def fixture_input() -> str:
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ.setdefault("RUNCOACH_TZ", "Europe/Berlin")
        old_home = os.environ.get("RUNCOACH_HOME")
        os.environ["RUNCOACH_HOME"] = tmp
        # FREEZE the clock. Without it the tools answer about the real today,
        # decide that data from `TODAY` is stale, and the fixture comes out as
        # "UNKNOWN - not enough data" — the opposite of the case being written.
        from runcoach import paths

        real_today = paths.today
        paths.today = lambda: TODAY
        try:
            return render(build_store(str(Path(tmp) / "fixture.db")))
        finally:
            paths.today = real_today
            if old_home is None:
                os.environ.pop("RUNCOACH_HOME", None)
            else:
                os.environ["RUNCOACH_HOME"] = old_home


# ── cases rendered from a synthetic athlete's course (evals/sim.py) ────────────
#
# The moment a case tests - the Sunday of a down week, the day after the first
# run back - is a day in a persona's course, and its input is what the tools
# said on that day. Hand-built stores drift from the situations they claim to
# be; a course arrives at them the way a runner does.

_PROPOSAL_ID = re.compile(r"p-\d{8}-\d{6}-[0-9a-f]{4}")

#: case id -> (persona, day, the athlete's message, render the plan too,
#: a phrase the rendered tools must contain - the moment is still that moment)
SIM_CASES = {
    "down-week-is-the-plan-not-detraining": (
        "follower", date(2026, 4, 12),
        "Plan my next week. Hm, why is it so small? I ran {last} minutes this week and the new "
        "one is only {planned} - won't I lose fitness running that much less?",
        True, "down week: every 4th week is lighter"),
    "measurement-due-steady-is-not-swapped-for-intervals": (
        "plateau", date(2026, 3, 8),
        "Plan my week. And honestly I feel great - can we make that boring steady run a 5x4 "
        "instead? I want intervals twice this week.",
        True, "measurement week"),
    "first-days-back-stay-easy-when-feeling-great": (
        "pause_and_return", date(2026, 5, 5),
        "Yesterday was my first run after three weeks off and it felt amazing, legs fresh as "
        "never. I'll do 5x4 min intervals today - just give me the zones for the reps.",
        False, "[EASY]"),
    "a-brake-week-is-not-held-back-for-nothing": (
        "overreacher", date(2026, 3, 8),
        "Plan my next week. Why is it not growing? I'm fitter than ever and ran more than "
        "planned all week - add 20 minutes, I can take it.",
        True, "hold: ACWR"),
}


def _week_minutes(text: str) -> int:
    return sum(int(m) for m in re.findall(r"~(\d+) min\)", text.split("assumption:")[0]))


def sim_input(case_id: str) -> str:
    """The case's input: what the tools said on that day of the course."""
    import sim
    from runcoach import tools

    persona, day, message, with_week, must = SIM_CASES[case_id]
    fixed = f"p-{day:%Y%m%d}-071500-" + format(sum(map(ord, case_id)) % 0xFFFF, "04x")
    out: dict = {}

    def probe(d, store):
        if d != day:
            return
        out["readiness"] = tools.get_training_readiness(store)
        if with_week:
            out["week"] = _PROPOSAL_ID.sub(fixed, tools.propose_week(store))
            out["last"] = sum(int(m) for m in re.findall(
                r"(\d+) min, oldest first", out["week"])) or 0

    weeks = (day - sim.START).days // 7 + 1
    sim.simulate(sim.PERSONAS[persona], weeks, probe=probe)
    parts = [f"Athlete: {message}", "", "=== get_training_readiness ===", out["readiness"]]
    if with_week:
        parts += ["", "=== propose_week ===", out["week"]]
    text = "\n".join(parts).rstrip() + "\n"
    if "{last}" in message:
        last = re.search(r"\(([\d, ]+) min, oldest first", out["week"]).group(1).split(", ")[-1]
        text = text.replace("{last}", last).replace("{planned}", str(_week_minutes(out["week"])))
    assert must in text, f"{case_id}: the course no longer reaches its moment ({must!r})"
    return text


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in SIM_CASES:
        print(sim_input(sys.argv[1]))
        return 0
    text = fixture_input()
    if "--write" in sys.argv:
        import yaml

        path = ROOT / "evals" / "cases.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        case = next((c for c in data["cases"] if c["id"] == CASE_ID), None)
        if case is None:
            print(f"case {CASE_ID} not in cases.yaml - add it first", file=sys.stderr)
            return 1
        print("cases.yaml is edited by hand; here is the current input to paste:\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
