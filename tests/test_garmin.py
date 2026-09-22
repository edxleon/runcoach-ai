"""Garmin parsing layer against a fake client — no real API.

This is where the drift risk sits (Garmin's unstable, deeply nested JSON), so
the tests use realistic dicts: missing fields, -1 sentinels, the device-keyed
training-status structure, status suffixes, free-text sanitising and fatal
error propagation.
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest import FakeGarmin
from runcoach import garmin
from runcoach.garmin import (
    _clean_text,
    _coerce,
    _f_to_c,
    _parse_gmt,
    _performance_condition,
    _round_te,
    _status_prefix,
    fetch_activities,
    fetch_day,
)
from runcoach.models import DailyMetrics

DAY = date(2026, 6, 8)


# ── fetch_day ────────────────────────────────────────────────────────────────

def test_fetch_day_full_parse():
    c = FakeGarmin(
        user_summary={"totalSteps": 16213, "restingHeartRate": 43, "averageStressLevel": 39,
                      "maxStressLevel": 88, "restStressDuration": 3600,
                      "bodyBatteryHighestValue": 95, "bodyBatteryLowestValue": 12},
        sleep={"dailySleepDTO": {"sleepTimeSeconds": 24060, "deepSleepSeconds": 3600,
                                 "lightSleepSeconds": 14000, "remSleepSeconds": 6000,
                                 "awakeSleepSeconds": 460,
                                 "sleepScores": {"overall": {"value": 81}}}},
        hrv={"hrvSummary": {"lastNightAvg": 58, "status": "unbalanced"}},
        training_status={"mostRecentTrainingStatus": {
            "latestTrainingStatusData": {"1234567890": {
                "trainingStatusFeedbackPhrase": "MAINTAINING_2",
                "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 714,
                                         "dailyTrainingLoadChronic": 572,
                                         "dailyAcuteChronicWorkloadRatio": 1.2,
                                         "acwrStatus": "OPTIMAL"}}},
            "mostRecentVO2Max": {"generic": {"vo2MaxValue": 48}}}},
        intensity={"moderateMinutes": 16, "vigorousMinutes": 36},
    )
    m = fetch_day(c, DAY)
    assert m.day == DAY
    assert (m.steps, m.resting_hr, m.sleep_score) == (16213, 43, 81)
    assert (m.stress_avg, m.stress_max) == (39, 88)
    assert (m.body_battery_high, m.body_battery_low) == (95, 12)
    assert (m.sleep_seconds, m.deep_sleep_seconds, m.awake_seconds) == (24060, 3600, 460)
    assert (m.hrv_avg_ms, m.hrv_status) == (58, "UNBALANCED")
    assert (m.training_status, m.acwr_ratio, m.acwr_status) == ("MAINTAINING", 1.2, "OPTIMAL")
    assert (m.acute_load, m.chronic_load, m.vo2max) == (714, 572, 48)
    assert (m.rest_stress_minutes, m.intensity_moderate_min, m.intensity_vigorous_min) == (60, 16, 36)


def test_fetch_day_empty_no_crash():
    m = fetch_day(FakeGarmin(), DAY)
    assert not m.has_any_metric()


def test_fetch_day_sentinels_to_none():
    c = FakeGarmin(user_summary={"totalSteps": -1, "restingHeartRate": -1, "averageStressLevel": -1})
    m = fetch_day(c, DAY)
    assert (m.steps, m.resting_hr, m.stress_avg) == (None, None, None)


def test_fetch_day_unknown_hrv_status_becomes_none():
    c = FakeGarmin(hrv={"hrvSummary": {"lastNightAvg": 50, "status": "SOMETHING_NEW"}})
    m = fetch_day(c, DAY)
    assert m.hrv_avg_ms == 50 and m.hrv_status is None


def test_fetch_day_vo2max_from_max_metrics():
    c = FakeGarmin(max_metrics=[{"generic": {"vo2MaxValue": 47}}])  # training status empty
    assert fetch_day(c, DAY).vo2max == 47


def test_fetch_day_vo2max_keeps_decimal():
    """The precise daily value (47.9) wins and is NOT rounded to 48 — otherwise
    the day-to-day tick is invisible."""
    c = FakeGarmin(max_metrics=[{"generic": {"vo2MaxPreciseValue": 47.9, "vo2MaxValue": 48.0}}])
    assert fetch_day(c, DAY).vo2max == 47.9


def test_fetch_day_vo2max_max_metrics_beats_training_status():
    """get_max_metrics (per day) wins over mostRecentVO2Max (last valid, not per
    day) — otherwise every day would be stamped flat with the latest value."""
    c = FakeGarmin(
        max_metrics=[{"generic": {"vo2MaxPreciseValue": 47.9}}],
        training_status={"mostRecentTrainingStatus": {
            "latestTrainingStatusData": {"1234567890": {"acuteTrainingLoadDTO": {}}},
            "mostRecentVO2Max": {"generic": {"vo2MaxValue": 49}}}})
    assert fetch_day(c, DAY).vo2max == 47.9


def test_fetch_day_vo2max_falls_back_to_training_status():
    c = FakeGarmin(training_status={"mostRecentTrainingStatus": {
        "mostRecentVO2Max": {"generic": {"vo2MaxValue": 49}}}})
    assert fetch_day(c, DAY).vo2max == 49


def test_fetch_day_multi_device_skips_empty():
    # First device empty ({}), the second carries the real data → the real one wins.
    c = FakeGarmin(training_status={"mostRecentTrainingStatus": {
        "latestTrainingStatusData": {
            "old_device": {},
            "111": {"trainingStatusFeedbackPhrase": "PRODUCTIVE_1",
                    "acuteTrainingLoadDTO": {"dailyAcuteChronicWorkloadRatio": 1.0,
                                             "acwrStatus": "OPTIMAL"}}}}})
    m = fetch_day(c, DAY)
    assert m.training_status == "PRODUCTIVE"
    assert m.acwr_ratio == 1.0


def test_fetch_day_bad_rest_stress_type_no_crash():
    c = FakeGarmin(user_summary={"restStressDuration": "nope", "totalSteps": 100})
    m = fetch_day(c, DAY)
    assert m.rest_stress_minutes is None and m.steps == 100


def test_fetch_day_soft_endpoint_failure_keeps_other_metrics():
    class HrvDown(FakeGarmin):
        def get_hrv_data(self, iso):
            raise ValueError("unexpected payload")

    m = fetch_day(HrvDown(user_summary={"restingHeartRate": 44}), DAY)
    assert m.resting_hr == 44 and m.hrv_avg_ms is None


def test_fetch_day_propagates_fatal():
    class Boom(FakeGarmin):
        def get_user_summary(self, iso):
            raise garmin._FATAL[-1]("429 too many requests")

    with pytest.raises(garmin._FATAL):
        fetch_day(Boom(), DAY)


def test_fetch_day_writes_no_profile_values():
    """Profile values (threshold, race times) do not belong in fetch_day: they
    would be stamped onto EVERY fetched day, so a 7-day backfill writes
    "measured today" onto older rows. They are written once per sync run."""
    class C(FakeGarmin):
        def get_lactate_threshold(self, **kw):
            raise AssertionError("fetch_day must not call the threshold endpoint")

        def get_race_predictions(self, **kw):
            raise AssertionError("fetch_day must not call the predictions endpoint")

    m = fetch_day(C(), date(2026, 9, 7))
    assert all(getattr(m, f) is None for f in DailyMetrics.PROFILE_FIELDS)


def test_profile_fields_do_not_make_a_measured_day():
    m = DailyMetrics(day=DAY, lthr_bpm=170, lt_speed_mps=3.5, lt_measured_on=DAY, race_5k_s=1400)
    assert not m.has_any_metric()
    m.steps = 1
    assert m.has_any_metric()


# ── is_fatal ─────────────────────────────────────────────────────────────────

def test_is_fatal_by_class_and_by_name():
    assert garmin.is_fatal(garmin._FATAL[0]("auth dead"))

    class GarminConnectTooManyRequestsError(Exception):   # same NAME, foreign class
        pass

    assert garmin.is_fatal(GarminConnectTooManyRequestsError("429"))
    assert not garmin.is_fatal(ValueError("broken payload"))
    assert not garmin.is_fatal(KeyError("x"))


# ── fetch_activities ─────────────────────────────────────────────────────────

def test_fetch_activities_parse_and_filter():
    acts = [
        {"activityId": 1, "startTimeGMT": "2026-06-08 06:25:57",
         "activityType": {"typeKey": "running"}, "activityName": "Morning Run",
         "distance": 6116.6, "duration": 2380.8, "averageHR": 153, "maxHR": 178,
         "calories": 487, "activityTrainingLoad": 205, "aerobicTrainingEffect": 3.1,
         "anaerobicTrainingEffect": 3.4, "trainingEffectLabel": "TEMPO", "vO2MaxValue": 48},
        {"activityId": 2, "startTimeGMT": "2026-05-01 06:00:00",
         "activityType": {"typeKey": "running"}},     # before since_date → dropped
        {"startTimeGMT": "2026-06-07 06:00:00"},       # no id → skipped
        {"activityId": 4, "startTimeGMT": "broken"},   # unparsable time → skipped
        "not-a-dict",
    ]
    out = fetch_activities(FakeGarmin(activities=acts), date(2026, 6, 5))
    assert len(out) == 1
    a = out[0]
    assert (a.activity_id, a.activity_type, a.name) == (1, "running", "Morning Run")
    assert (a.distance_m, a.duration_s, a.aerobic_te, a.te_label) == (6117, 2381, 3.1, "TEMPO")
    assert a.start_time.isoformat() == "2026-06-08T06:25:57+00:00"
    assert (a.avg_hr, a.max_hr, a.training_load, a.anaerobic_te) == (153, 178, 205, 3.4)


def test_fetch_activities_non_list_response():
    """An unreadable body is a FAILED endpoint, not a quiet week.

    This used to assert `== []`, which is the value `sync.run` writes down as
    "Garmin answered, there were no workouts" - zero errors, exit 0, and a coach
    that tells the athlete they did not run. `None` is the failure channel and
    the docstring of `fetch_activities` promised it; only the exception path
    actually used it."""
    assert fetch_activities(FakeGarmin(activities={"error": "nope"}), date(2026, 6, 1)) is None
    assert fetch_activities(FakeGarmin(activities=None), date(2026, 6, 1)) is None
    # ...and a real empty list still means a real empty week.
    assert fetch_activities(FakeGarmin(activities=[]), date(2026, 6, 1)) == []


def test_fetch_activities_sanitizes_freetext():
    acts = [{"activityId": 9, "startTimeGMT": "2026-06-08 06:00:00",
             "activityName": "x" * 200 + "\n[link](u)",
             "activityType": {"typeKey": "run\x00ning"},
             "trainingEffectLabel": "lbl\x07"}]
    a = fetch_activities(FakeGarmin(activities=acts), date(2026, 6, 1))[0]
    assert len(a.name) <= 80
    assert "\n" not in a.name
    assert "\x00" not in a.activity_type
    assert "\x07" not in a.te_label


def test_fetch_activities_propagates_fatal():
    class Boom(FakeGarmin):
        def get_activities(self, start, limit):
            raise garmin._FATAL[0]("auth dead")

    with pytest.raises(garmin._FATAL):
        fetch_activities(Boom(), date(2026, 6, 1))


# ── helpers ──────────────────────────────────────────────────────────────────

def test_status_prefix():
    assert _status_prefix("MAINTAINING_2") == "MAINTAINING"
    assert _status_prefix("NO_STATUS") == "NO_STATUS"
    assert _status_prefix("productive") == "PRODUCTIVE"
    assert _status_prefix(None) is None
    assert _status_prefix("  ") is None


def test_parse_gmt():
    assert _parse_gmt("2026-06-08 06:25:57").isoformat() == "2026-06-08T06:25:57+00:00"
    assert _parse_gmt("2026-06-08T06:25:57").tzinfo is not None
    assert _parse_gmt("garbage") is None
    assert _parse_gmt(None) is None


def test_round_te():
    assert _round_te(3.14) == 3.1
    assert _round_te(7) is None      # > 6
    assert _round_te(-1) is None     # < 0
    assert _round_te("x") is None


def test_clean_text():
    assert _clean_text("ab\x00cd", 80) == "ab cd"
    assert _clean_text("  x  ", 80) == "x"
    assert _clean_text("y" * 100, 10) == "y" * 10
    assert _clean_text(None, 80) is None
    assert _clean_text("   ", 80) is None


@pytest.mark.parametrize(
    "value,lo,hi,expected",
    [
        (None, 0, 100, None),
        (-1, 0, 100, None),             # Garmin sentinel
        (43.0, 20, 220, 43),            # float → int
        (43.7, 0, 100, 44),             # rounded, not truncated
        (250, 20, 220, None),           # out of range
        ("not_a_number", 0, 100, None),
        (0, 0, 100, 0),                 # lower bound is valid
    ],
)
def test_coerce_clamps(value, lo, hi, expected):
    assert _coerce(value, lo, hi) == expected


def test_f_to_c_edge_branches():
    assert _f_to_c(59) == 15
    assert _f_to_c(32) == 0
    assert _f_to_c(200) is None       # 93 C is beyond the 55 C clamp → None, no junk value
    assert _f_to_c(-100) is None      # -73 C is below the -50 C clamp
    assert _f_to_c("x") is None
    assert _f_to_c(None) is None


def test_performance_condition_edge_branches():
    assert _performance_condition(None) is None
    assert _performance_condition({}) is None
    # descriptor missing
    assert _performance_condition({"metricDescriptors": [{"metricsIndex": 0, "key": "directHeartRate"}],
                                   "activityDetailMetrics": [{"metrics": [140]}]}) is None
    # zeros only (all warm-up / undetermined)
    assert _performance_condition({"metricDescriptors": [{"metricsIndex": 0,
            "key": "directPerformanceCondition"}],
                                   "activityDetailMetrics": [{"metrics": [0]}, {"metrics": [0]}]}) is None
    # a point that is too short for the index is skipped, valid ones count
    assert _performance_condition({"metricDescriptors": [{"metricsIndex": 1,
            "key": "directPerformanceCondition"}],
                                   "activityDetailMetrics": [{"metrics": [140]},
                                                             {"metrics": [140, -5.0]}]}) == -5


# ── activity detail: HR zones, splits, run context ───────────────────────────

_ZONES = [
    {"zoneNumber": 1, "secsInZone": 18.0, "zoneLowBoundary": 98},
    {"zoneNumber": 2, "secsInZone": 361.0, "zoneLowBoundary": 117},
    {"zoneNumber": 3, "secsInZone": 687.0, "zoneLowBoundary": 137},
    {"zoneNumber": 4, "secsInZone": 1275.9, "zoneLowBoundary": 156},
    {"zoneNumber": 5, "secsInZone": 31.0, "zoneLowBoundary": 176},
]


def test_detail_keys_match_the_store_columns():
    from runcoach import store

    assert tuple(garmin.DETAIL_KEYS) == tuple(store._DETAIL_COLS)


def test_fetch_activity_detail_zones():
    d = garmin.fetch_activity_detail(FakeGarmin(hr_zones=_ZONES, typed_splits={}), 111)
    assert d["hr_z1_s"] == 18 and d["hr_z4_s"] == 1276 and d["hr_z5_s"] == 31
    assert d["hr_z4_low"] == 156 and d["hr_z5_low"] == 176
    assert d["splits"] == []
    assert set(d) == {*garmin.DETAIL_KEYS, "splits", "unknown"}
    assert d["unknown"] == set(), "every endpoint answered, nothing is unknown"


def test_fetch_activity_detail_prefers_interval_splits():
    # Raw laps (RWD_*) AND structured INTERVAL_* present → INTERVAL_ wins, cadence
    # comes from the overall RWD_RUN lap.
    splits = {"splits": [
        {"type": "RWD_RUN", "distance": 5870, "duration": 2196, "averageHR": 154,
         "maxHR": 178, "elevationGain": 165, "averageRunCadence": 170},
        {"type": "INTERVAL_WARMUP", "distance": 1500, "duration": 643, "averageHR": 137, "maxHR": 151},
        {"type": "INTERVAL_ACTIVE", "distance": 200, "duration": 40, "averageHR": 151, "maxHR": 153},
        {"type": "INTERVAL_RECOVERY", "distance": 400, "duration": 165, "averageHR": 154, "maxHR": 160},
        {"type": "INTERVAL_COOLDOWN", "distance": 416, "duration": 255, "averageHR": 147, "maxHR": 171},
    ]}
    d = garmin.fetch_activity_detail(FakeGarmin(hr_zones=_ZONES, typed_splits=splits), 222)
    assert [s.split_type for s in d["splits"]] == [
        "INTERVAL_WARMUP", "INTERVAL_ACTIVE", "INTERVAL_RECOVERY", "INTERVAL_COOLDOWN"]
    assert [s.split_index for s in d["splits"]] == [0, 1, 2, 3]   # renumbered
    assert all(s.activity_id == 222 for s in d["splits"])
    assert d["splits"][1].avg_hr == 151 and d["splits"][1].distance_m == 200
    assert d["avg_cadence"] == 170   # from RWD_RUN, not from a single rep


def test_fetch_activity_detail_lap_fallback():
    # No INTERVAL_* → RWD_RUN/RWD_WALK laps as fallback.
    splits = {"splits": [
        {"type": "RWD_RUN", "distance": 6000, "duration": 2400, "averageHR": 150, "maxHR": 170,
         "averageRunCadence": 168},
        {"type": "RWD_STAND", "distance": 4, "duration": 6, "averageHR": 95},   # dropped
    ]}
    d = garmin.fetch_activity_detail(FakeGarmin(hr_zones=[], typed_splits=splits), 333)
    assert [s.split_type for s in d["splits"]] == ["RWD_RUN"]
    assert d["hr_z1_s"] is None


def test_fetch_activity_detail_empty():
    d = garmin.fetch_activity_detail(FakeGarmin(hr_zones=[], typed_splits={}), 444)
    assert all(d[k] is None for k in garmin.DETAIL_KEYS)
    assert d["splits"] == []


def test_fetch_activity_detail_run_context():
    """Ambient temperature (F→C), humidity and the performance-condition median."""
    weather = {"temp": 59, "relativeHumidity": 72}
    details = {
        "metricDescriptors": [
            {"metricsIndex": 0, "key": "directHeartRate"},
            {"metricsIndex": 1, "key": "directPerformanceCondition"},
        ],
        "activityDetailMetrics": [
            {"metrics": [140, 0]},      # warm-up: undetermined (0) → excluded
            {"metrics": [150, -1.0]},
            {"metrics": [152, -3.0]},
            {"metrics": [151, -4.0]},   # median of the non-zero values = -3
        ],
    }
    c = FakeGarmin(hr_zones=[], typed_splits={}, weather=weather, activity_details=details)
    d = garmin.fetch_activity_detail(c, 555)
    assert d["temperature_c"] == 15
    assert d["humidity_pct"] == 72
    assert d["performance_condition"] == -3


def test_fetch_activity_detail_propagates_fatal():
    class Boom(FakeGarmin):
        def get_activity_typed_splits(self, activity_id):
            raise garmin._FATAL[-1]("429")

    with pytest.raises(garmin._FATAL):
        garmin.fetch_activity_detail(Boom(hr_zones=_ZONES), 1)


# ── lactate threshold ────────────────────────────────────────────────────────

def test_lt_speed_norm_scales_the_too_small_raw_field():
    """Garmin delivers `speed` a factor of 10 too small (0.3444 instead of
    3.4444) — read as documented that would be 48 min/km."""
    assert garmin._lt_speed_norm(0.34444348) == 3.44
    assert garmin._lt_speed_norm(3.4444348) == 3.44      # already correct → not scaled again


def test_lt_speed_norm_drops_implausible_pace():
    """Should Garmin ever switch to the documented unit, the value must NOT be
    stored ten times too large — the range check drops it."""
    for broken in (34.4, 0, "broken", None, 99, -3.4):
        assert garmin._lt_speed_norm(broken) is None


def test_lt_speed_norm_warns_loudly_on_a_unit_break(caplog):
    """A dropped value must show up in the log; otherwise a unit change on
    Garmin's side is invisible (the upsert keeps the old value via COALESCE)."""
    with caplog.at_level("WARNING", logger=garmin.log.name):
        assert garmin._lt_speed_norm(34.4) is None
    assert any("change the unit" in r.getMessage() for r in caplog.records)
    # The empty/broken case is NOT a unit break and must not warn, otherwise
    # normal operation (days without a measurement) drowns the real signal.
    caplog.clear()
    with caplog.at_level("WARNING", logger=garmin.log.name):
        assert garmin._lt_speed_norm(None) is None
        assert garmin._lt_speed_norm(0) is None
    assert not caplog.records


def test_fetch_lactate_history_joins_both_series_on_the_day():
    class C(FakeGarmin):
        def __init__(self):
            super().__init__()
            self.kwargs = None

        def get_lactate_threshold(self, **kw):
            self.kwargs = kw
            return {
                "heart_rate": [{"from": "2026-07-16T00:00:00.0", "value": 176},
                               {"from": "2026-07-13", "value": 178},
                               {"from": "2026-07-20", "value": 20},     # implausible HR
                               {"from": "bad", "value": 170},
                               {"value": 171}],
                "speed": [{"from": "2026-07-13", "value": 0.353},
                          {"from": "2026-07-30", "value": 0.344},       # pace without HR stays
                          {"from": "2026-07-20", "value": 99}],          # implausible pace
            }

    c = C()
    points = garmin.fetch_lactate_history(c, date(2026, 7, 1), date(2026, 8, 1))
    assert c.kwargs == {"latest": False, "start_date": "2026-07-01", "end_date": "2026-08-01"}
    assert points == [
        {"day": date(2026, 7, 13), "lthr_bpm": 178, "lt_speed_mps": 3.53},
        {"day": date(2026, 7, 16), "lthr_bpm": 176, "lt_speed_mps": None},
        {"day": date(2026, 7, 30), "lthr_bpm": None, "lt_speed_mps": 3.44},
    ]   # sorted by day; 2026-07-20 had nothing plausible and is gone


def test_fetch_lactate_history_empty_and_soft_failure():
    assert garmin.fetch_lactate_history(FakeGarmin(), date(2026, 7, 1), date(2026, 8, 1)) == []

    class ListResponse(FakeGarmin):
        def get_lactate_threshold(self, **kw):
            return [1, 2, 3]

    assert garmin.fetch_lactate_history(ListResponse(), date(2026, 7, 1), date(2026, 8, 1)) == []


# ── race predictions + calendar ──────────────────────────────────────────────

def test_fetch_race_predictions_parses_and_range_checks():
    class C(FakeGarmin):
        def get_race_predictions(self, **kw):
            return {"calendarDate": "2026-09-07", "time5K": 1450, "time10K": 3065,
                    "timeHalfMarathon": 6915, "timeMarathon": 15424}

    assert garmin.fetch_race_predictions(C()) == {
        "race_5k_s": 1450, "race_10k_s": 3065, "race_hm_s": 6915, "race_m_s": 15424}

    class Nonsense(FakeGarmin):
        def get_race_predictions(self, **kw):
            return {"time5K": 5, "time10K": None, "timeHalfMarathon": "x", "timeMarathon": 99999999}

    assert garmin.fetch_race_predictions(Nonsense()) == {}   # all implausible → empty

    class Empty(FakeGarmin):
        def get_race_predictions(self, **kw):
            return {}

    assert garmin.fetch_race_predictions(Empty()) == {}


def test_fetch_scheduled_workouts_filters_and_dedupes():
    """The calendar returns ALL items per month (weight, activities, events);
    only itemType=workout counts, only inside the window, each schedule id once."""
    class C(FakeGarmin):
        def __init__(self):
            super().__init__()
            self.calls = []

        def get_scheduled_workouts(self, year, month):
            self.calls.append((year, month))
            if (year, month) == (2026, 8):
                return {"calendarItems": [
                    {"id": 1, "itemType": "weight", "date": "2026-08-30"},
                    {"id": 2, "itemType": "activity", "date": "2026-08-30", "title": "done"},
                    {"id": 3, "itemType": "workout", "date": "2026-08-31", "workoutId": 10,
                     "title": "Easy Z2 45min", "sportTypeKey": "running"},
                    {"id": 4, "itemType": "workout", "date": "2026-08-20", "workoutId": 11,
                     "title": "before the window"},
                ]}
            return {"calendarItems": [
                {"id": 3, "itemType": "workout", "date": "2026-08-31", "workoutId": 10,
                 "title": "Easy Z2 45min", "sportTypeKey": "running"},   # duplicate
                {"id": 5, "itemType": "workout", "date": "2026-09-07", "workoutId": 12,
                 "title": "Threshold 35", "sportTypeKey": "running"},
                {"id": "broken", "itemType": "workout", "date": "2026-09-08"},
                {"id": 6, "itemType": "workout", "date": "nope", "workoutId": 1},
            ]}

    c = C()
    got, complete = garmin.fetch_scheduled_workouts(c, date(2026, 8, 31), date(2026, 9, 21))
    assert c.calls == [(2026, 8), (2026, 9)]
    assert complete is True
    assert [(s.schedule_id, s.day.isoformat(), s.workout_id, s.title, s.sport) for s in got] == [
        (3, "2026-08-31", 10, "Easy Z2 45min", "running"),
        (5, "2026-09-07", 12, "Threshold 35", "running")]


def test_fetch_scheduled_workouts_client_without_method_is_incomplete():
    # `_safe` turns the AttributeError into {} → NOT complete. This flag is the
    # only brake against a full replace on half a source. A bare double here:
    # `FakeGarmin` grew a calendar with the write path and would answer.
    class NoCalendar:
        pass

    got, complete = garmin.fetch_scheduled_workouts(NoCalendar(), date(2026, 9, 1), date(2026, 9, 2))
    assert got == [] and complete is False


def test_fetch_scheduled_workouts_reports_partial_failure():
    """A soft error for ONE month must not wipe the mirror."""
    class Half(FakeGarmin):
        def get_scheduled_workouts(self, year, month):
            if month == 9:
                raise ValueError("500 from the calendar")     # soft → caught by _safe
            return {"calendarItems": [
                {"id": 7, "itemType": "workout", "date": "2026-08-31",
                 "workoutId": 1, "title": "Easy", "sportTypeKey": "running"}]}

    got, complete = garmin.fetch_scheduled_workouts(Half(), date(2026, 8, 31), date(2026, 9, 21))
    assert [s.schedule_id for s in got] == [7]
    assert complete is False


def test_fetch_scheduled_workouts_spans_a_year_boundary():
    class C(FakeGarmin):
        def __init__(self):
            super().__init__()
            self.calls = []

        def get_scheduled_workouts(self, year, month):
            self.calls.append((year, month))
            return {"calendarItems": []}

    c = C()
    got, complete = garmin.fetch_scheduled_workouts(c, date(2026, 12, 28), date(2027, 1, 10))
    assert c.calls == [(2026, 12), (2027, 1)] and got == [] and complete is True
