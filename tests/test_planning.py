"""The session builder: pure arithmetic, then the round trip to Garmin's DTO
and back through a fake client - the path `plan.apply` will drive."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from conftest import FakeGarmin
from runcoach import garmin, planning
from runcoach.planning import (
    HARD_KINDS,
    KINDS,
    WEEKDAYS,
    Repeat,
    Step,
    build_session,
    build_week,
    describe,
    from_json,
    swap_for_readiness,
    to_json,
)

ZONES = {"z4_low": 156, "z5_low": 176, "lthr_bpm": 168, "lt_pace_s_per_km": 285}


# ── shape ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_builds_for_a_10k_route_and_for_45_minutes(kind):
    by_distance = build_session(kind, distance_km=10, zones=ZONES)
    by_time = build_session(kind, duration_min=45, zones=ZONES)
    for spec in (by_distance, by_time):
        assert spec.kind == kind and spec.name and spec.blocks
        assert spec.estimated_seconds > 0 and spec.estimated_meters > 0
        assert not spec.assumptions, "measured zones given - nothing should be assumed"


def test_intervals_are_warmup_repeat_cooldown_and_the_route_adds_up():
    """"Plan me intervals for my 10 km route": the reps are the stimulus, the
    warm-up and cool-down absorb the rest of the distance."""
    spec = build_session("vo2max", distance_km=10, zones=ZONES)
    kinds = [type(b).__name__ if isinstance(b, Repeat) else b.kind for b in spec.blocks]
    assert kinds == ["warmup", "Repeat", "cooldown"]
    rep = spec.blocks[1]
    assert rep.iterations >= 3
    assert [s.kind for s in rep.steps] == ["interval", "recovery"]
    assert rep.steps[0].target == {"type": "hr_zone", "zone": 5}
    assert rep.steps[1].target is None, "a recovery step never carries a target"
    # Warm-up + cool-down in metres plus reps at their paces = the route.
    wu, cd = spec.blocks[0].meters, spec.blocks[2].meters
    assert wu >= planning.MIN_WARMUP_M and cd >= planning.MIN_COOLDOWN_M
    assert abs(spec.estimated_meters - 10_000) <= 300, spec.estimated_meters
    assert wu + cd < 10_000


def test_threshold_reps_are_longer_and_target_zone_4():
    spec = build_session("threshold", distance_km=12, zones=ZONES)
    rep = next(b for b in spec.blocks if isinstance(b, Repeat))
    assert rep.steps[0].seconds >= 480 and rep.steps[0].target == {"type": "hr_zone", "zone": 4}


def test_an_easy_run_is_one_capped_step_from_the_athletes_own_bounds():
    spec = build_session("easy", distance_km=8, zones=ZONES)
    assert len(spec.blocks) == 1 and spec.blocks[0].meters == 8000
    cap = spec.blocks[0].target
    assert cap["type"] == "hr_bpm" and cap["hi"] == round(0.83 * ZONES["z5_low"])
    assert cap["lo"] < cap["hi"]


def test_missing_zones_become_stated_assumptions_not_silence():
    spec = build_session("easy", distance_km=8, zones={})
    assert spec.blocks[0].target is None
    assert any("no HR bounds" in a for a in spec.assumptions)
    spec = build_session("vo2max", distance_km=10, zones={})
    assert any("estimated at" in a for a in spec.assumptions)


def test_a_route_too_short_for_intervals_says_so():
    with pytest.raises(ValueError, match="too short"):
        build_session("vo2max", distance_km=2.5, zones=ZONES)


def test_steady_is_a_single_even_block_of_at_least_twelve_minutes():
    """The one shape Garmin re-measures VO2max from - a spiky session leaves
    the old estimate standing."""
    spec = build_session("steady", duration_min=40, zones=ZONES)
    work = [b for b in spec.blocks if isinstance(b, Step) and b.kind == "interval"]
    assert len(work) == 1 and work[0].seconds >= 12 * 60
    assert work[0].target["type"] == "hr_bpm" and work[0].target["lo"] < ZONES["lthr_bpm"]


@pytest.mark.parametrize("bad", [dict(distance_km=0.5), dict(duration_min=5),
                                  dict(distance_km=5, duration_min=30), dict()])
def test_inputs_are_checked(bad):
    with pytest.raises(ValueError):
        build_session("easy", zones=ZONES, **bad)


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="unknown session kind"):
        build_session("fartlek", distance_km=5, zones=ZONES)


def test_json_round_trip_is_lossless():
    spec = build_session("vo2max", distance_km=10, zones=ZONES)
    assert to_json(from_json(to_json(spec))) == to_json(spec)


def test_describe_is_readable_and_names_the_assumptions():
    text = describe(build_session("vo2max", distance_km=10, zones={}))
    assert "warmup" in text and "recovery" in text and "HR zone 5" in text
    assert "assumption:" in text


# ── to Garmin and back ───────────────────────────────────────────────────────

def test_the_dto_carries_the_targets_on_the_step_and_none_on_the_recovery():
    spec = build_session("vo2max", distance_km=10, zones=ZONES)
    dto = garmin.to_running_workout(spec).to_dict()
    steps = dto["workoutSegments"][0]["workoutSteps"]
    assert [s["stepType"]["stepTypeKey"] for s in steps] == ["warmup", "repeat", "cooldown"]
    work, rec = steps[1]["workoutSteps"]
    assert work["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone" and work["zoneNumber"] == 5
    assert rec["targetType"]["workoutTargetTypeKey"] == "no.target" and "zoneNumber" not in rec
    assert steps[1]["numberOfIterations"] == spec.blocks[1].iterations
    assert dto["estimatedDurationInSecs"] == spec.estimated_seconds


def test_pace_targets_are_metres_per_second_slow_bound_first():
    spec = planning.SessionSpec("steady", "p", [Step("interval", seconds=900,
                                                    target=planning.pace(290, 280))], 900, 3000)
    step = garmin.to_running_workout(spec).to_dict()["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetValueOne"] == pytest.approx(1000 / 290, abs=1e-3)
    assert step["targetValueTwo"] == pytest.approx(1000 / 280, abs=1e-3)
    assert step["targetValueOne"] < step["targetValueTwo"]


def test_upload_schedule_readback_and_verify_through_the_fake():
    """The whole write path against the double: what comes back is what was
    sent, `verify` agrees, and the calendar mirror sees the scheduled day."""
    from datetime import date

    client = FakeGarmin()
    spec = build_session("threshold", distance_km=10, zones=ZONES)
    wid = garmin.upload_workout(client, spec)
    assert wid >= 900_000
    assert garmin.verify(spec, garmin.read_back(client, wid)) == []
    sid = garmin.schedule(client, wid, date(2026, 9, 24))
    assert sid
    entries, complete = garmin.fetch_scheduled_workouts(client, date(2026, 9, 20), date(2026, 9, 30))
    assert complete and [e.workout_id for e in entries] == [wid]
    garmin.push_to_device(client, wid)
    assert client.data["pushed"] == [wid]
    garmin.unschedule(client, sid)
    entries, _ = garmin.fetch_scheduled_workouts(client, date(2026, 9, 20), date(2026, 9, 30))
    assert entries == []


def test_verify_catches_the_three_real_failures():
    spec = build_session("vo2max", distance_km=10, zones=ZONES)
    good = garmin.to_running_workout(spec).to_dict()

    import copy

    # (1) a target on the recovery step
    bad = copy.deepcopy(good)
    bad["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][1]["targetType"] = {
        "workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}
    assert any("recovery carries a target" in p for p in garmin.verify(spec, bad))
    # (2) the rep count is not the confirmed one
    bad = copy.deepcopy(good)
    bad["workoutSegments"][0]["workoutSteps"][1]["numberOfIterations"] += 1
    assert any("repeat count differs" in p for p in garmin.verify(spec, bad))
    # (3) the work step lost its target
    bad = copy.deepcopy(good)
    bad["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][0]["targetType"] = {
        "workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
    assert any("lost its target" in p for p in garmin.verify(spec, bad))


# ── a week ───────────────────────────────────────────────────────────────────

MONDAY = date(2026, 9, 21)


def _gap(a: date, b: date) -> int:
    """Days between two sessions of a WEEKLY rhythm: Tuesday is two days after
    Sunday, whichever of the two the window holds first."""
    d = abs((a - b).days)
    return min(d, 7 - d)


@pytest.mark.parametrize("n", [3, 4, 5, 6, 7])
def test_a_week_is_polarised_spaced_and_keeps_a_rest_day(n):
    sessions, _ = build_week(MONDAY, days_per_week=n, long_run_day="sun", zones=ZONES)
    days = [d for d, _ in sessions]
    kinds = [s.kind for _, s in sessions]
    assert len(sessions) == min(n, 6), "seven days asked still leaves one rest day"
    assert days == sorted(days) and len(set(days)) == len(days)
    assert all(MONDAY <= d <= MONDAY + timedelta(days=6) for d in days)
    assert kinds.count("long") == 1
    long_day = next(d for d, s in sessions if s.kind == "long")
    assert WEEKDAYS[long_day.weekday()] == "sun"
    hard = [d for d, s in sessions if s.kind in HARD_KINDS]
    assert len(hard) == (1 if n == 3 else 2)
    if n >= 4:
        assert {s.kind for _, s in sessions if s.kind in HARD_KINDS} == {"vo2max", "threshold"}
    for a in hard:
        assert _gap(a, long_day) >= 2, "nothing hard next to the long run"
        for b in hard:
            assert a == b or _gap(a, b) >= 2, "48 h between hard stimuli"
    assert all(s.kind == "easy" for _, s in sessions if s.kind not in HARD_KINDS + ("long",))


def test_seven_days_say_why_only_six_are_planned():
    _, notes = build_week(MONDAY, days_per_week=7, long_run_day="sat", zones=ZONES)
    assert any("one rest day" in a for a in notes)


def test_the_long_run_lands_on_the_named_weekday_whatever_the_start():
    sessions, _ = build_week(date(2026, 9, 23), days_per_week=4, long_run_day="sat", zones=ZONES)
    long_day = next(d for d, s in sessions if s.kind == "long")
    assert long_day == date(2026, 9, 26)
    hard = [d for d, s in sessions if s.kind in HARD_KINDS]
    assert all(_gap(a, long_day) >= 2 for a in hard)


@pytest.mark.parametrize("bad", [{"days_per_week": 2}, {"days_per_week": 8},
                                 {"long_run_day": "sunday"}])
def test_week_inputs_are_checked(bad):
    with pytest.raises(ValueError):
        build_week(MONDAY, **{"days_per_week": 4, "long_run_day": "sun", **bad}, zones=ZONES)


def test_zone_assumptions_are_said_once_for_the_week_not_per_session():
    sessions, notes = build_week(MONDAY, days_per_week=5, long_run_day="sun", zones=None)
    assert sum("threshold pace" in a for a in notes) == 1
    assert all(s.assumptions == [] for _, s in sessions)


# ── the readiness swap ───────────────────────────────────────────────────────

CALENDAR = [{"schedule_id": 1, "workout_id": 10, "title": "Tuesday run"},
            {"schedule_id": 2, "workout_id": 20, "title": "VO2max 5x3"},
            {"schedule_id": 3, "workout_id": 30, "title": "Easy 40"}]


def test_an_easy_day_replaces_the_hard_entries_and_flags_the_guessed_one():
    out = swap_for_readiness("easy", CALENDAR, {10: "threshold", 30: "easy"})
    assert [e["schedule_id"] for e in out["replace"]] == [1, 2]
    assert [e["schedule_id"] for e in out["guessed"]] == [2], "judged by its title only"
    assert out["kind"] == "easy"
    rest = swap_for_readiness("rest", CALENDAR, {10: "threshold"})
    assert [e["schedule_id"] for e in rest["replace"]] == [1, 2] and rest["kind"] is None


@pytest.mark.parametrize("decision", ["hard", "unknown"])
def test_a_green_or_unknown_day_swaps_nothing(decision):
    assert swap_for_readiness(decision, CALENDAR, {10: "threshold"})["replace"] == []


def test_own_workouts_are_judged_by_the_record_not_by_their_name():
    """The app knows what it created; a name is data. An own EASY run called
    "VO2max" stays, an own vo2max session called "Tuesday run" goes."""
    out = swap_for_readiness("easy", [{"schedule_id": 9, "workout_id": 90, "title": "VO2max 5x3"}],
                             {90: "easy"})
    assert out["replace"] == []
