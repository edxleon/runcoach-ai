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

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

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
        m.vo2max = 52.0
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


def main() -> int:
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
