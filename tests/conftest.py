"""Shared fixtures and builders.

Everything runs against a real SQLite file in a tmp dir: no server, no Docker,
no network, no Garmin account. Garmin is replaced by small fake client classes.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

# Make the tests runnable from a plain checkout (no editable install needed).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from runcoach import paths  # noqa: E402
from runcoach.models import Activity, ActivitySplit, DailyMetrics  # noqa: E402
from runcoach.store import Store  # noqa: E402

#: A fixed "today" for tests that need one. A Wednesday; its week starts Monday 2026-06-08.
TODAY = date(2026, 6, 10)

#: A complete detail payload as `garmin.fetch_activity_detail` delivers it (minus splits).
DETAIL = {"hr_z1_s": 18, "hr_z2_s": 361, "hr_z3_s": 687, "hr_z4_s": 1276,
          "hr_z5_s": 31, "hr_z4_low": 156, "hr_z5_low": 176, "avg_cadence": 170,
          "temperature_c": 15, "humidity_pct": 72, "performance_condition": -3}


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Deterministic timezone on any machine, and nothing touches the real home."""
    monkeypatch.setenv("RUNCOACH_TZ", "Europe/Berlin")
    monkeypatch.setenv("RUNCOACH_HOME", str(tmp_path / "home"))
    for name in ("RUNCOACH_DB", "RUNCOACH_ACTIVITY_BACKFILL_DAYS", "RUNCOACH_LT_HISTORY_DAYS",
                 "RUNCOACH_GARMIN_TOKENS", "RUNCOACH_DEMO", "RUNCOACH_TOKEN",
                 "RUNCOACH_CLAUDE_CMD", "RUNCOACH_MODEL", "RUNCOACH_LOG"):
        monkeypatch.delenv(name, raising=False)

@pytest.fixture(autouse=True)
def _no_worker_outlives_its_test():
    """A job worker started by one test and left running is invisible to that
    test and lethal to a later one: it keeps polling `paths.jobs_dir()`, which
    reads RUNCOACH_HOME on every call, so it consumes the jobs of whichever
    home the next test sets up - with the real `agent.run`, which fails a job
    in milliseconds and frees a queue slot that test was counting on. This is
    what "queue_full expected, got 200" in test_web was, once in many CI runs
    and never in isolation.

    The worker names its own thread, whoever started it, so the check needs
    no cooperation from the test: after every test, no `runcoach-worker` may
    still be alive. A test that starts one has to stop it (`app.worker_stop`)."""
    import threading
    yield
    leaked = [t for t in threading.enumerate() if t.name == "runcoach-worker" and t.is_alive()]
    assert not leaked, ("a job worker thread outlived this test - set app.worker_stop "
                        "and join it, or every later test's queue is at its mercy")


@pytest.fixture()
def store(tmp_path) -> Store:
    return Store(tmp_path / "t.db")


@pytest.fixture()
def today(monkeypatch) -> date:
    """Freeze `paths.today()` — every module calls it through the `paths` module."""
    monkeypatch.setattr(paths, "today", lambda: TODAY)
    return TODAY


def make_day(day: date, **metrics) -> DailyMetrics:
    return DailyMetrics(day=day, **metrics)


def make_activity(activity_id: int, day: date, *, hour: int = 8, activity_type: str = "running",
                  load: int | None = 100, aerobic_te: float | None = 3.0,
                  distance_m: int | None = 6000, duration_s: int | None = 2400,
                  **extra) -> Activity:
    """A workout starting at `hour`:00 UTC on `day` (08:00 UTC = 10:00 Berlin, same day)."""
    return Activity(
        activity_id=activity_id,
        start_time=datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc),
        activity_type=activity_type, name=extra.pop("name", f"act-{activity_id}"),
        distance_m=distance_m, duration_s=duration_s,
        training_load=load, aerobic_te=aerobic_te, **extra)


def make_split(activity_id: int, index: int, split_type: str, duration_s: int | None = None,
               **extra) -> ActivitySplit:
    return ActivitySplit(activity_id=activity_id, split_index=index, split_type=split_type,
                         duration_s=duration_s, **extra)


def split_dict(index: int, split_type: str, duration_s: int | None, **extra) -> dict:
    """Split as a plain dict — the shape `logic.interval_facts` consumes."""
    return {"split_index": index, "split_type": split_type, "duration_s": duration_s,
            "distance_m": extra.get("distance_m"), "avg_hr": extra.get("avg_hr"),
            "max_hr": extra.get("max_hr")}


def seed_week(store: Store, end: date, *, resting=(40, 41, 42, 43, 44, 45, 46)) -> None:
    """Seven consecutive days ending on `end` (walking backwards through `resting`)."""
    from datetime import timedelta

    for i, rhr in enumerate(resting):
        store.upsert_daily(DailyMetrics(
            day=end - timedelta(days=i), resting_hr=rhr, sleep_seconds=25200, sleep_score=80,
            hrv_avg_ms=55 + i, stress_avg=30, body_battery_high=90, steps=10000))


class FakeGarmin:
    """Garmin client double: every endpoint answers from `data`, default empty."""

    def __init__(self, **data):
        self.data = data

    def get_user_summary(self, iso):
        return self.data.get("user_summary", {})

    def get_sleep_data(self, iso):
        return self.data.get("sleep", {})

    def get_hrv_data(self, iso):
        return self.data.get("hrv", {})

    def get_training_status(self, iso):
        return self.data.get("training_status", {})

    def get_max_metrics(self, iso):
        return self.data.get("max_metrics", [])

    def get_intensity_minutes_data(self, iso):
        return self.data.get("intensity", {})

    def get_activities(self, start, limit):
        return self.data.get("activities", [])

    def get_activity_hr_in_timezones(self, activity_id):
        return self.data.get("hr_zones", [])

    def get_activity_typed_splits(self, activity_id):
        return self.data.get("typed_splits", {})

    def get_activity_weather(self, activity_id):
        return self.data.get("weather", {})

    def get_activity_details(self, activity_id, **kwargs):
        return self.data.get("activity_details", {})
