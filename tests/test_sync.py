"""`sync.run` with a fake Garmin client and a real tmp SQLite store."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from conftest import TODAY, FakeGarmin, make_activity
from runcoach import garmin, sync
from runcoach.models import ScheduledWorkout
from runcoach.sync import SyncReport

ZONES = [{"zoneNumber": n, "secsInZone": 100 * n, "zoneLowBoundary": 90 + 20 * n} for n in range(1, 6)]


def raw_activity(activity_id: int, day: date, *, type_key: str = "running", hour: int = 8) -> dict:
    return {"activityId": activity_id, "startTimeGMT": f"{day.isoformat()} {hour:02d}:00:00",
            "activityType": {"typeKey": type_key}, "activityName": f"act-{activity_id}",
            "distance": 6000, "duration": 2400, "activityTrainingLoad": 100,
            "aerobicTrainingEffect": 3.0}


class SyncClient(FakeGarmin):
    """FakeGarmin plus the three side channels; records the detail calls."""

    def __init__(self, **data):
        data.setdefault("user_summary", {"restingHeartRate": 44, "totalSteps": 9000})
        super().__init__(**data)
        self.detail_calls: list[int] = []
        self.activity_calls: list[tuple] = []

    def get_activities(self, start, limit):
        self.activity_calls.append((start, limit))
        return super().get_activities(start, limit)

    def get_activity_hr_in_timezones(self, activity_id):
        self.detail_calls.append(activity_id)
        return super().get_activity_hr_in_timezones(activity_id)

    def get_lactate_threshold(self, **kw):
        return self.data.get("lactate", {})

    def get_race_predictions(self, **kw):
        return self.data.get("race", {})

    def get_scheduled_workouts(self, year, month):
        return self.data.get("calendar", {"calendarItems": []})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)


def _run(store, client, days=3):
    lines: list[str] = []
    return sync.run(store, client, days, say=lines.append), lines


# ── happy path ───────────────────────────────────────────────────────────────

def test_run_writes_days_activities_detail_and_side_channels(store, today):
    client = SyncClient(
        activities=[raw_activity(1, today - timedelta(days=1))],
        hr_zones=ZONES,
        typed_splits={"splits": [{"type": "RWD_RUN", "distance": 6000, "duration": 2400,
                                  "averageHR": 150, "averageRunCadence": 170}]},
        lactate={"heart_rate": [{"from": (today - timedelta(days=1)).isoformat(), "value": 172}],
                 "speed": [{"from": (today - timedelta(days=1)).isoformat(), "value": 0.35}]},
        race={"time5K": 1400, "time10K": 2950},
        calendar={"calendarItems": [{"id": 5, "itemType": "workout", "workoutId": 9,
                                     "date": (today + timedelta(days=2)).isoformat(),
                                     "title": "Easy 8k", "sportTypeKey": "running"}]})
    rep, lines = _run(store, client)

    assert (rep.days_written, rep.days_empty, rep.activities, rep.details) == (3, 0, 1, 1)
    assert rep.errors == 0 and rep.soft_errors == 0 and rep.exit_code == 0
    assert lines[-1] == rep.summary() and lines[-1].startswith("[OK]")
    assert store.counts() == {"days": 3, "activities": 1}
    assert store.latest_day() == today
    assert store.activities_missing_detail(today - timedelta(days=40), today) == []
    a = store.get_workout_analysis(activity_id=1)
    assert a["zones_s"]["z5"] == 500 and a["avg_cadence"] == 170 and a["split_count"] == 1
    lt = store.latest_lactate_threshold()
    assert lt["lthr_bpm"] == 172 and lt["lt_speed_mps"] == 3.5
    assert lt["lt_measured_on"] == (today - timedelta(days=1)).isoformat()
    rp = store.latest_race_predictions()
    assert rp["day"] == today.isoformat() and rp["race_5k_s"] == 1400 and rp["race_hm_s"] is None
    plan = store.get_scheduled_workouts(today, today + timedelta(days=14))
    assert [(p["schedule_id"], p["title"]) for p in plan] == [(5, "Easy 8k")]


def test_run_is_idempotent(store, today):
    client = SyncClient(activities=[raw_activity(1, today)], hr_zones=ZONES)
    _run(store, client)
    rep, _ = _run(store, client)
    assert store.counts() == {"days": 3, "activities": 1}
    assert rep.details == 0                                  # detail already there → no second fetch
    assert client.detail_calls == [1]


def test_empty_days_are_counted_not_written(store, today):
    rep, lines = _run(store, SyncClient(user_summary={}))
    assert (rep.days_written, rep.days_empty) == (0, 3)
    assert store.counts()["days"] == 0                       # no ghost rows
    assert rep.exit_code == 0
    assert any("no metrics" in line for line in lines)


@pytest.mark.parametrize("asked,fetched", [(0, 1), (-5, 1), (7, 7), (500, 90)])
def test_days_are_clamped(store, today, asked, fetched):
    rep, _ = _run(store, SyncClient(), days=asked)
    assert rep.days_written == fetched


def test_activity_window_is_wider_than_the_day_window(store, today, monkeypatch):
    inside = today - timedelta(days=34)
    edge = today - timedelta(days=35)       # one extra day: an early local run on the previous UTC day
    outside = today - timedelta(days=36)
    client = SyncClient(activities=[raw_activity(1, inside), raw_activity(2, edge),
                                    raw_activity(3, outside)])
    rep, _ = _run(store, client, days=1)
    assert rep.activities == 2
    assert client.activity_calls == [(0, 50)]

    monkeypatch.setenv("RUNCOACH_ACTIVITY_BACKFILL_DAYS", "5")
    client = SyncClient(activities=[raw_activity(4, today - timedelta(days=5)),
                                    raw_activity(5, today - timedelta(days=6))])
    rep, _ = _run(store, client, days=1)
    assert rep.activities == 1


# ── hard errors ──────────────────────────────────────────────────────────────

def test_a_fatal_day_error_aborts_the_whole_sync_at_once(store, today):
    """429 / auth / connection means Garmin told us to stop. Retrying the day and
    then carrying on through 30 more days, the activity fetch and three side
    channels would spend 60+ requests and several minutes of backoff proving the
    point - and deepen the block. One call, then out."""
    class DayDown(SyncClient):
        calls = 0

        def get_user_summary(self, iso):
            DayDown.calls += 1
            raise garmin._FATAL[-1]("429")

    rep, lines = _run(store, DayDown(), days=30)
    assert DayDown.calls == 1                       # no retry, no further days
    assert rep.days_written == 0 and rep.activities == 0 and rep.details == 0
    assert rep.fatal and "429" in rep.fatal
    assert rep.exit_code == 2                       # "Garmin stopped us", not "some days failed"
    assert any("sync aborted" in line for line in lines)


def test_a_soft_activity_fetch_failure_does_not_stop_the_rest(store, today):
    class ActsDown(SyncClient):
        def get_activities(self, start, limit):
            raise RuntimeError("500 Server Error")

    rep, lines = _run(store, ActsDown(race={"time5K": 1400}))
    assert rep.errors == 1 and rep.activities == 0 and rep.exit_code == 3
    assert rep.days_written == 3 and rep.fatal is None
    assert store.latest_race_predictions()["race_5k_s"] == 1400     # side channels still ran
    assert any("workout fetch failed" in line for line in lines)


def test_a_fatal_activity_fetch_failure_is_reported_as_garmin_stopping_us(store, today):
    """Exit 2, not 3: the CLI turns that into "wait, the sync is idempotent"
    instead of sending the athlete through an MFA re-login that cannot help."""
    class ActsDown(SyncClient):
        def get_activities(self, start, limit):
            raise garmin._FATAL[-1]("429 Too Many Requests")

    rep, _ = _run(store, ActsDown())
    assert rep.fatal and "429" in rep.fatal and rep.exit_code == 2


def test_fatal_error_in_detail_backfill_aborts_the_backfill(store, today):
    class Throttled(SyncClient):
        def get_activity_hr_in_timezones(self, activity_id):
            self.detail_calls.append(activity_id)
            raise garmin._FATAL[-1]("429 too many requests")

    client = Throttled(activities=[raw_activity(i, today - timedelta(days=i)) for i in (1, 2, 3)])
    rep, lines = _run(store, client)
    assert client.detail_calls == [1]                        # newest first, then STOP hammering
    assert rep.errors == 1 and rep.details == 0 and rep.exit_code == 3
    assert any("aborted" in line for line in lines)
    assert store.activities_missing_detail(today - timedelta(days=40), today) == [1, 2, 3]
    assert rep.days_written == 3                             # the rest of the run is kept


def test_non_fatal_detail_error_skips_only_that_workout(store, today, monkeypatch):
    real = garmin.fetch_activity_detail

    def flaky(client, activity_id):
        if activity_id == 1:
            raise ValueError("unexpected payload")
        return real(client, activity_id)

    monkeypatch.setattr(garmin, "fetch_activity_detail", flaky)
    client = SyncClient(activities=[raw_activity(i, today - timedelta(days=i)) for i in (1, 2)],
                        hr_zones=ZONES)
    rep, _ = _run(store, client)
    assert rep.errors == 1 and rep.details == 1
    assert store.activities_missing_detail(today - timedelta(days=40), today) == [1]


def test_run_with_empty_detail_is_not_marked_as_synced(store, today):
    """A RUN with completely empty detail is a transient endpoint failure →
    retry next time. Strength without detail is normal and gets marked —
    otherwise every strength session would be retried forever."""
    client = SyncClient(activities=[raw_activity(1, today),
                                    raw_activity(2, today, type_key="strength_training", hour=17)])
    rep, lines = _run(store, client)
    # Documented as benign, so it must not paint the sync red: it is reported as
    # a soft error and the exit code stays 0 for cron.
    assert rep.errors == 0 and rep.soft_errors == 1 and rep.details == 1 and rep.exit_code == 0
    assert store.activities_missing_detail(today - timedelta(days=40), today) == [1]
    assert any("retry next sync" in line for line in lines)


# ── ingest_detail ────────────────────────────────────────────────────────────

def test_ingest_detail_outcomes(store):
    d = date(2026, 6, 8)
    store.upsert_activity(make_activity(1, d))
    store.upsert_activity(make_activity(2, d, activity_type="trail_running"))
    store.upsert_activity(make_activity(3, d, activity_type="strength_training"))

    outcome, det = sync.ingest_detail(store, FakeGarmin(hr_zones=ZONES), 1)
    assert outcome == "written" and det["hr_z3_s"] == 300
    assert store.get_workout_analysis(activity_id=1)["has_detail"] is True

    assert sync.ingest_detail(store, FakeGarmin(), 2)[0] == "soft_fail"      # every running type
    assert store.get_workout_analysis(activity_id=2)["has_detail"] is False
    assert sync.ingest_detail(store, FakeGarmin(), 3)[0] == "written"
    assert store.get_workout_analysis(activity_id=3)["has_detail"] is True


def test_ingest_detail_splits_alone_count_as_detail(store):
    store.upsert_activity(make_activity(1, date(2026, 6, 8)))
    client = FakeGarmin(typed_splits={"splits": [{"type": "RWD_RUN", "duration": 2400}]})
    assert sync.ingest_detail(store, client, 1)[0] == "written"
    assert store.get_workout_analysis(activity_id=1)["split_count"] == 1


# ── soft side channels ───────────────────────────────────────────────────────

def test_soft_side_channel_errors_do_not_set_the_exit_code(store, today, monkeypatch):
    def broken(client):
        raise ValueError("unexpected payload")

    monkeypatch.setattr(garmin, "fetch_race_predictions", broken)
    rep, lines = _run(store, SyncClient())
    assert rep.soft_errors == 1 and rep.errors == 0 and rep.exit_code == 0
    # ... but visible: a permanently failing side channel must not look healthy
    assert lines[-1].startswith("[!]") and "1 soft" in lines[-1]
    assert any("race predictions: ValueError" in line for line in lines)


def test_a_fatal_side_channel_error_keeps_the_report(store, today):
    """It used to re-raise out of `run()`, so the days that WERE written went
    unreported and the summary line never printed — a sync that did most of its
    work looked like a sync that did none."""
    class CalendarThrottled(SyncClient):
        def get_scheduled_workouts(self, year, month):
            raise garmin._FATAL[-1]("429")

    rep, lines = _run(store, CalendarThrottled())
    assert rep.days_written == 3                    # the work that succeeded is reported
    assert rep.fatal and "429" in rep.fatal and rep.exit_code == 2
    assert lines[-1].startswith("[!]") and "Aborted by Garmin" in lines[-1]


def test_incomplete_calendar_does_not_replace_the_mirror(store, today):
    """A full replace on half a source deletes real entries — and the app would
    then actively claim "nothing planned"."""
    store.replace_scheduled_workouts(
        [ScheduledWorkout(77, today + timedelta(days=1), title="Threshold", sport="running")],
        today - timedelta(days=7), today + timedelta(days=14))

    class CalendarBroken(SyncClient):
        def get_scheduled_workouts(self, year, month):
            raise ValueError("500 from the calendar")

    rep, lines = _run(store, CalendarBroken())
    assert rep.soft_errors == 1 and rep.exit_code == 0
    assert [p["schedule_id"] for p in store.get_scheduled_workouts(today, today + timedelta(days=14))] == [77]
    assert any("mirror left as is" in line for line in lines)

    # A complete (even if empty) calendar DOES replace it.
    rep, _ = _run(store, SyncClient())
    assert rep.soft_errors == 0
    assert store.get_scheduled_workouts(today, today + timedelta(days=14)) == []


def test_calendar_window_is_minus_7_to_plus_14_days(store, today):
    items = [{"id": i, "itemType": "workout", "date": (today + timedelta(days=off)).isoformat(),
              "title": f"w{off}"} for i, off in enumerate((-8, -7, 14, 15), start=1)]
    _run(store, SyncClient(calendar={"calendarItems": items}))
    got = store.get_scheduled_workouts(today - timedelta(days=30), today + timedelta(days=30))
    assert [g["title"] for g in got] == ["w-7", "w14"]


def test_threshold_point_without_a_day_row_creates_no_ghost_row(store, today):
    old = today - timedelta(days=30)
    client = SyncClient(lactate={"heart_rate": [{"from": old.isoformat(), "value": 170}]})
    rep, _ = _run(store, client)
    assert rep.soft_errors == 0
    assert store.get_day(old) is None and store.latest_lactate_threshold() is None


def test_predictions_are_not_written_when_today_has_no_row(store, today):
    rep, _ = _run(store, SyncClient(user_summary={}, race={"time5K": 1400}))
    assert store.latest_race_predictions() is None and store.counts()["days"] == 0
    assert rep.exit_code == 0


# ── SyncReport + helpers ─────────────────────────────────────────────────────

def test_sync_report_exit_code_and_summary():
    clean = SyncReport(days_written=7, activities=3, details=2)
    assert clean.exit_code == 0
    assert clean.summary() == "[OK] 7 day(s), 0 empty, 3 workout(s), 2 with detail, 0 error(s)"
    assert "soft" not in clean.summary()

    # Hard errors 0 but side channels fail → "!" and the number is visible;
    # otherwise a frozen calendar mirror cannot be told from a healthy sync.
    soft = SyncReport(days_written=7, soft_errors=2)
    assert soft.exit_code == 0
    assert soft.summary().startswith("[!]") and "0 error(s), 2 soft (side channels)" in soft.summary()

    hard = SyncReport(days_written=6, days_empty=1, errors=1)
    assert hard.exit_code == 3
    assert hard.summary().startswith("[!]") and "1 error(s)" in hard.summary() and "1 empty" in hard.summary()


@pytest.mark.parametrize("raw,expected", [(None, 180), ("   ", 180), ("abc", 180), ("42", 42), ("", 180)])
def test_env_int(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("RUNCOACH_LT_HISTORY_DAYS", raising=False)
    else:
        monkeypatch.setenv("RUNCOACH_LT_HISTORY_DAYS", raw)
    assert sync._env_int("RUNCOACH_LT_HISTORY_DAYS", 180) == expected


def test_today_fixture_is_what_sync_uses(store, today):
    _run(store, SyncClient(), days=1)
    assert store.latest_day() == TODAY
