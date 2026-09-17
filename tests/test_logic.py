"""The deterministic core: pure functions, no database."""

from __future__ import annotations

import json

import pytest

from conftest import split_dict
from runcoach.logic import QUALITY_FLOOR_SECONDS, decide_today, interval_facts, readiness_verdict, run_kind

GREEN = dict(hrv_status="BALANCED", sleep_score=85, body_battery_high=95, acwr=1.1,
             resting_hr=44, resting_hr_baseline=45.0)


# ── readiness verdict ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "override,expected,key",
    [
        ({}, "GO", None),
        ({"hrv_status": "UNBALANCED"}, "EASY", "hrv"),
        ({"hrv_status": "LOW"}, "REST", "hrv"),
        ({"hrv_status": "POOR"}, "REST", "hrv"),
        ({"acwr": 1.7, "acwr_source": "garmin"}, "REST", "acwr"),      # Garmin ACWR > 1.5
        ({"acwr": 1.4, "acwr_source": "garmin"}, "EASY", "acwr"),      # elevated
        ({"sleep_score": 40}, "REST", "sleep_score"),                  # sleep < 50
        ({"sleep_score": 60}, "EASY", "sleep_score"),                  # sleep < 65
        ({"body_battery_high": 35}, "REST", "body_battery"),
        ({"body_battery_high": 55}, "EASY", "body_battery"),
        ({"resting_hr": 53}, "REST", "resting_hr"),                    # +8 above baseline
        ({"resting_hr": 49}, "EASY", "resting_hr"),                    # +4 above baseline
    ],
)
def test_readiness_verdict_rules(override, expected, key):
    verdict, reasons, flags = readiness_verdict(**{**GREEN, **override})
    assert verdict == expected
    assert reasons
    if key is None:
        assert flags == []
    else:
        assert [f["key"] for f in flags] == [key]
        assert flags[0]["level"] == expected.lower()
        assert flags[0]["text"] == reasons[0]


def test_readiness_verdict_boundaries_are_exclusive():
    # Exactly on the threshold is still green: 50/65 sleep, 40/60 battery, 1.3/1.5 ACWR.
    assert readiness_verdict(**{**GREEN, "sleep_score": 65})[0] == "GO"
    assert readiness_verdict(**{**GREEN, "sleep_score": 50})[0] == "EASY"
    assert readiness_verdict(**{**GREEN, "body_battery_high": 60})[0] == "GO"
    assert readiness_verdict(**{**GREEN, "body_battery_high": 40})[0] == "EASY"
    assert readiness_verdict(**{**GREEN, "acwr": 1.3})[0] == "GO"
    assert readiness_verdict(**{**GREEN, "acwr": 1.5, "acwr_source": "garmin"})[0] == "EASY"
    assert readiness_verdict(**{**GREEN, "acwr": 0.8})[0] == "GO"


def test_readiness_verdict_rest_beats_easy_and_keeps_all_flags():
    verdict, reasons, flags = readiness_verdict(
        **{**GREEN, "hrv_status": "UNBALANCED", "sleep_score": 45})
    assert verdict == "REST"
    assert [(f["level"], f["key"]) for f in flags] == [("easy", "hrv"), ("rest", "sleep_score")]
    assert len(reasons) == 2


def test_readiness_verdict_sparse_downgrade():
    # Only one axis (resting HR) present → no confident GO despite "green".
    verdict, reasons, flags = readiness_verdict(
        hrv_status=None, sleep_score=None, body_battery_high=None, acwr=None,
        resting_hr=44, resting_hr_baseline=45.0)
    assert verdict == "EASY"
    assert any("thin data" in r for r in reasons)
    assert flags == [{"level": "easy", "key": "data", "text": reasons[0]}]


def test_readiness_verdict_two_signals_are_enough_for_go():
    verdict, _, _ = readiness_verdict(
        hrv_status="BALANCED", sleep_score=80, body_battery_high=None, acwr=None,
        resting_hr=None, resting_hr_baseline=None)
    assert verdict == "GO"


def test_readiness_verdict_sparse_rest_is_not_softened():
    # The confidence guard only downgrades GO; a REST from one signal stays REST.
    verdict, _, _ = readiness_verdict(
        hrv_status="LOW", sleep_score=None, body_battery_high=None, acwr=None,
        resting_hr=None, resting_hr_baseline=None)
    assert verdict == "REST"


def test_readiness_verdict_computed_acwr_not_rest():
    # Computed ACWR > 1.5 → EASY only (the scale is not risk-equivalent to Garmin's EWMA).
    verdict, reasons, _ = readiness_verdict(**{**GREEN, "acwr": 1.8, "acwr_source": "computed"})
    assert verdict == "EASY"
    assert "computed" in reasons[0]


def test_readiness_verdict_detraining_suppressed_after_hard():
    common = {**GREEN, "acwr": 0.6, "acwr_source": "garmin"}
    # Right after a hard workout: no "detraining" flag → GO
    assert readiness_verdict(**common, days_since_hard=0)[0] == "GO"
    assert readiness_verdict(**common, days_since_hard=2)[0] == "GO"
    # No hard stimulus for a long time (or unknown): the flag applies → EASY
    assert readiness_verdict(**common, days_since_hard=3)[0] == "EASY"
    assert readiness_verdict(**common, days_since_hard=10)[0] == "EASY"
    assert readiness_verdict(**common, days_since_hard=None)[0] == "EASY"


def test_readiness_verdict_resting_hr_needs_a_baseline():
    verdict, _, flags = readiness_verdict(**{**GREEN, "resting_hr": 70, "resting_hr_baseline": None})
    assert verdict == "GO" and flags == []


# ── decide_today: readiness light vs weekly hard share ───────────────────────

@pytest.mark.parametrize(
    "verdict,kwargs,decision,reason",
    [
        # Rule 1: REST beats everything — even a week far below its share.
        ("REST", dict(days_since_hard=5, hard_min=0, target_min=26), "rest", "verdict REST"),
        # Rule 2: EASY → easy, hard minutes stay open.
        ("EASY", dict(days_since_hard=5, hard_min=0, target_min=26), "easy", "verdict EASY"),
        # Rule 3a: hard yesterday → 48-hour rule, although GO AND week below target.
        ("GO", dict(days_since_hard=1, hard_min=12, target_min=26), "easy", "48-hour rule"),
        ("GO", dict(days_since_hard=0, hard_min=12, target_min=26), "easy", "48-hour rule"),
        # Rule 3a also applies before 3d (target 0 + hard today → easy, not hard).
        ("GO", dict(days_since_hard=0, hard_min=0, target_min=0), "easy", "48-hour rule"),
        # Rule 3b: GO, week below its share → hard.
        ("GO", dict(days_since_hard=3, hard_min=12, target_min=26), "hard", "GO, week below share"),
        # Rule 3c: GO, share reached → easy.
        ("GO", dict(days_since_hard=3, hard_min=26, target_min=26), "easy", "GO, share reached"),
        # Rule 3d: GO without measured zone time in the week → hard (first session).
        ("GO", dict(days_since_hard=3, hard_min=0, target_min=0), "hard", "GO, first session of the week"),
        # ... also without a known "days since hard" (fresh database).
        ("GO", dict(days_since_hard=None, hard_min=0, target_min=0), "hard", "GO, first session of the week"),
        # Rule 4: no light → no verdict.
        (None, dict(days_since_hard=3, hard_min=0, target_min=26), "unknown", "no verdict"),
        ("WHATEVER", dict(days_since_hard=3, hard_min=0, target_min=26), "unknown", "no verdict"),
    ],
)
def test_decide_today_rules(verdict, kwargs, decision, reason):
    r = decide_today(verdict, week_start="2026-09-07", **kwargs)
    assert r["decision"] == decision and r["reason"] == reason
    assert r["sentence"] and r["sentence"][0].isupper()
    assert r["week"] == {"hard_min": kwargs["hard_min"], "target_min": kwargs["target_min"],
                         "week_start": "2026-09-07"}


def test_decide_today_is_json_serialisable_and_number_lives_only_in_week():
    """The UI appends "12 of 26 min" itself — inside the sentence the number
    would appear twice."""
    r = decide_today("GO", days_since_hard=4, hard_min=12, target_min=26)
    json.dumps(r)
    assert set(r) == {"decision", "sentence", "reason", "week"}
    assert "12" not in r["sentence"] and "26" not in r["sentence"]
    assert r["week"] == {"hard_min": 12, "target_min": 26, "week_start": None}


def test_decide_today_sentence_never_contradicts_the_numbers():
    """Whether the week is behind is decided by the numbers, not the rule branch."""
    behind = decide_today("REST", days_since_hard=5, hard_min=0, target_min=26)["sentence"]
    reached = decide_today("REST", days_since_hard=5, hard_min=30, target_min=26)["sentence"]
    assert "below its hard share" in behind
    assert "below its hard share" not in reached

    easy_behind = decide_today("EASY", days_since_hard=5, hard_min=0, target_min=26)["sentence"]
    easy_reached = decide_today("EASY", days_since_hard=5, hard_min=26, target_min=26)["sentence"]
    easy_no_zone_time = decide_today("EASY", days_since_hard=5, hard_min=0, target_min=0)["sentence"]
    assert "stay open" in easy_behind
    assert "already reached" in easy_reached
    assert "reached" not in easy_no_zone_time and "open" not in easy_no_zone_time


def test_decide_today_48_hour_sentence_names_the_day():
    assert "today" in decide_today("GO", days_since_hard=0, hard_min=0, target_min=0)["sentence"]
    assert "yesterday" in decide_today("GO", days_since_hard=1, hard_min=0, target_min=0)["sentence"]


# ── interval_facts ───────────────────────────────────────────────────────────

def test_interval_facts_labels():
    """The structure label is the reason this function exists: splits must
    become "5×4′", not "5 minutes"."""
    reps = []
    for i in range(5):
        reps.append(split_dict(2 * i, "INTERVAL_ACTIVE", 240, avg_hr=170, max_hr=180, distance_m=900))
        reps.append(split_dict(2 * i + 1, "INTERVAL_RECOVERY", 120, avg_hr=140))
    f = interval_facts(reps)
    assert f["kind"] == "intervals" and f["label"] == "5×4′"
    assert f["has_intervals"] is True
    assert f["rep_count"] == 5 and f["avg_active_hr"] == 170.0
    assert f["avg_rep_duration_s"] == 240.0 and f["avg_rep_distance_m"] == 900.0
    assert f["avg_recovery_hr"] == 140.0 and f["max_active_hr"] == 180
    assert f["split_count"] == 10


def test_interval_facts_odd_durations():
    # Odd duration → m:ss rather than a rounded-minutes lie and rather than "348″".
    assert interval_facts([split_dict(i, "INTERVAL_ACTIVE", 348, avg_hr=175)
                           for i in range(5)])["label"] == "5×5:48"
    short = [split_dict(i, "INTERVAL_ACTIVE", 45, avg_hr=175) for i in range(4)]
    assert interval_facts(short)["label"] == "4×45″"
    one_minute = [split_dict(i, "INTERVAL_ACTIVE", 60) for i in range(8)]
    assert interval_facts(one_minute)["label"] == "8×1′"


def test_interval_facts_without_durations_counts_reps():
    f = interval_facts([split_dict(i, "INTERVAL_ACTIVE", None) for i in range(3)])
    assert f["kind"] == "intervals" and f["label"] == "3 reps"
    assert f["avg_rep_duration_s"] is None


def test_interval_facts_steady_and_unknown():
    # Splits without an active one = steady run; NO splits at all = no statement.
    steady = interval_facts([split_dict(0, "RWD_RUN", 1800, avg_hr=135)])
    assert steady["kind"] == "steady" and steady["label"] == "Steady run"
    assert steady["has_intervals"] is False and steady["rep_count"] == 0
    empty = interval_facts([])
    assert empty["kind"] == "unknown" and empty["label"] is None
    assert empty["split_count"] == 0 and empty["max_active_hr"] is None


def test_interval_facts_single_active_split_is_not_an_interval():
    """Garmin likes to mark a plain steady run ENTIRELY as ACTIVE. That used to
    become "1×42′" for a 42-minute easy run. A single block only counts when
    something else really surrounds it (warm-up/cool-down)."""
    whole_run = interval_facts([split_dict(0, "INTERVAL_ACTIVE", 2520, avg_hr=150)])
    assert whole_run["kind"] == "steady" and whole_run["label"] == "Steady run"
    assert whole_run["has_intervals"] is False and whole_run["rep_count"] == 0
    assert whole_run["avg_rep_duration_s"] is None

    # 20-minute threshold block inside a 44-minute run: real structure
    block = interval_facts([split_dict(0, "INTERVAL_WARMUP", 720),
                            split_dict(1, "INTERVAL_ACTIVE", 1200),
                            split_dict(2, "INTERVAL_COOLDOWN", 725)])
    assert block["kind"] == "intervals" and block["label"] == "1×20′"
    assert block["rep_count"] == 1


def test_interval_facts_split_type_is_case_insensitive_and_none_safe():
    f = interval_facts([{"split_type": "interval_active", "duration_s": 180},
                        {"split_type": "Interval_Active", "duration_s": 180},
                        {"split_type": None, "duration_s": 300}])
    assert f["kind"] == "intervals" and f["label"] == "2×3′"


# ── run_kind ─────────────────────────────────────────────────────────────────

def test_run_kind_classification():
    # 80-min Z2 steady run (anaerobic 0) = Long Run, NOT Easy
    assert run_kind({"activity_type": "running", "duration_s": 4800, "anaerobic_te": 0.0}) == "Long Run"
    # 45 min relaxed = Easy
    assert run_kind({"activity_type": "running", "duration_s": 2700, "anaerobic_te": 0.0}) == "Easy"
    # Intervals with a real anaerobic stimulus = Quality (even if short)
    assert run_kind({"activity_type": "running", "duration_s": 3120, "anaerobic_te": 3.0}) == "Quality"
    # A long but hard run → Quality beats Long Run (anaerobic first)
    assert run_kind({"activity_type": "running", "duration_s": 5400, "anaerobic_te": 2.6}) == "Quality"


def test_run_kind_covers_every_running_type_and_nothing_else():
    # trail_/treadmill_running MUST be classified too (congruent with the store's
    # LIKE '%running%') — otherwise they are invisible to the 48-hour rule.
    assert run_kind({"activity_type": "trail_running", "duration_s": 3120, "anaerobic_te": 3.0}) == "Quality"
    assert run_kind({"activity_type": "treadmill_running", "duration_s": 4800,
            "anaerobic_te": 0.0}) == "Long Run"
    assert run_kind({"activity_type": "strength_training", "duration_s": 3120, "anaerobic_te": 3.0}) is None
    assert run_kind({"activity_type": None, "duration_s": 3120}) is None
    assert run_kind({}) is None


def test_run_kind_thresholds():
    # VO2max 5x3min session: anaerobic 2.2 → MUST be Quality
    assert run_kind({"activity_type": "running", "duration_s": 3120, "aerobic_te": 3.5,
            "anaerobic_te": 2.2}) == "Quality"
    # Tempo run: anaerobic 2.1 → Quality
    assert run_kind({"activity_type": "running", "duration_s": 3840, "aerobic_te": 4.3,
            "anaerobic_te": 2.1}) == "Quality"
    # Aerobic backstop: short, hard-aerobic session with low anaerobic TE → Quality
    assert run_kind({"activity_type": "running", "duration_s": 3000, "aerobic_te": 3.5,
            "anaerobic_te": 1.5}) == "Quality"
    # Just below: anaerobic 1.9 + moderate aerobic, short → stays Easy (no false alarm)
    assert run_kind({"activity_type": "running", "duration_s": 2700, "aerobic_te": 2.8,
            "anaerobic_te": 1.9}) == "Easy"
    # Aerobically demanding long run (3.4 >= backstop, but >= 70 min) → Long Run, NOT Quality
    assert run_kind({"activity_type": "running", "duration_s": 5400, "aerobic_te": 3.4,
            "anaerobic_te": 0.0}) == "Long Run"
    # None values are treated as 0
    assert run_kind({"activity_type": "running", "duration_s": None, "aerobic_te": None,
            "anaerobic_te": None}) == "Easy"


# ── the two axes: load and stimulus ──────────────────────────────────────────

def test_a_grey_zone_week_far_over_its_target_is_not_told_to_train_hard():
    """`decide_today` may prescribe hard work, so it needs the same restraint as
    every other rule that can. The "no quality" branch was added without a
    ceiling: an athlete five times over their week's non-easy target, every
    minute of it in zone 3, was told "today is the day for the hard session" —
    with a sentence ("the week HAS its minutes above easy") that reads like
    *just about there*. At that point the answer is not more stimulus, it is
    less grey-zone volume."""
    over = decide_today("GO", days_since_hard=4, hard_min=200, target_min=40,
                        quality_min=0, quality_s=0)
    assert over["decision"] == "easy"
    assert over["reason"] == "GO, far over share, all grey zone"
    assert "easy days need to get easier" in over["sentence"]

    # Just under the ceiling the quality branch still speaks.
    under = decide_today("GO", days_since_hard=4, hard_min=79, target_min=40,
                         quality_min=0, quality_s=0)
    assert under["decision"] == "hard" and under["reason"] == "GO, share reached but no quality"
    # ...and EXACTLY at it, it does not: a threshold that only fires above itself
    # leaves the boundary case prescribing a hard session on pure zone 3.
    at = decide_today("GO", days_since_hard=4, hard_min=80, target_min=40,
                      quality_min=0, quality_s=0)
    assert at["decision"] == "easy" and at["reason"] == "GO, far over share, all grey zone"


def test_an_amber_light_still_names_a_week_without_any_quality():
    """The stimulus axis used to speak only on green days, so the same athlete —
    60 min of zone 3, nothing above threshold — was told on an amber day that
    "the week's hard share is already reached anyway": verbatim the sentence the
    axis was added to abolish. The light still decides what happens TODAY; the
    note is about the week."""
    note = decide_today("EASY", days_since_hard=4, hard_min=60, target_min=32,
                        quality_min=0, quality_s=0)
    assert note["decision"] == "easy", "the light outranks the week, always"
    assert "already reached" not in note["sentence"]
    assert "zone 3" in note["sentence"] and "none above threshold" in note["sentence"]

    # With real quality in the week the old sentence is correct and comes back.
    done = decide_today("EASY", days_since_hard=4, hard_min=60, target_min=32,
                        quality_min=15, quality_s=900)
    assert "already reached" in done["sentence"]

    # REST outranks both, and says nothing about missing quality: a red light is
    # never a reason to think about stimulus.
    rest = decide_today("REST", days_since_hard=4, hard_min=60, target_min=32,
                        quality_min=0, quality_s=0)
    assert rest["decision"] == "rest" and "zone 3" not in rest["sentence"]


def test_the_quality_test_is_a_floor_not_a_cliff_at_one_second():
    """Both extremes were wrong once. Asking the ROUNDED MINUTE made 29 s of HR
    drift read as "none above threshold". Asking for strictly zero seconds then
    put a cliff at ONE second: a single beat on a hill switched the grey-zone
    correction off while the sentence the athlete reads — "of which 0 above
    threshold" — stayed byte-identical.

    A minute is the smallest amount that is a session rather than noise, and it
    is the unit every surface displays."""
    def rule(quality_s):
        return decide_today("GO", days_since_hard=4, hard_min=40, target_min=30,
                            quality_min=round(quality_s / 60), quality_s=quality_s)["reason"]

    assert rule(0) == "GO, share reached but no quality"
    assert rule(1) == "GO, share reached but no quality", "one second is not a session"
    assert rule(59) == "GO, share reached but no quality"
    assert rule(60) == "GO, share reached", "...one minute is"
    assert rule(900) == "GO, share reached"
    assert QUALITY_FLOOR_SECONDS == 60


def test_the_week_dict_carries_both_axes_for_every_surface():
    """The Today tab, the Trend chart and `tools._decision_block` all read this
    dict. A surface that had only one of the two numbers would be back to
    refereeing polarisation with a single scalar."""
    r = decide_today("GO", days_since_hard=4, hard_min=60, target_min=32,
                     quality_min=12, quality_s=720, week_start="2026-09-14")
    assert r["week"] == {"hard_min": 60, "target_min": 32, "quality_min": 12,
                         "week_start": "2026-09-14"}
    json.dumps(r)


# ── every sentence the athlete can be shown ──────────────────────────────────

#: The SENTENCE, keyed by the state that produces it. Not the `reason` key:
#: five different EASY sentences share `reason == "verdict EASY"`, so a suite
#: asserting reason keys cannot tell them apart — and the sentence is what the
#: athlete reads. Two of those five, and both 48-hour variants, were pinned by
#: nothing at all, which is how a whole branch of `decide_today` was added,
#: commented at length, and left untested.
DECISION_SENTENCES = {
    "Not enough data for a verdict.",
    # REST — the light outranks the week, and says so when the week is open.
    "No hard session today - the light says rest.",
    "No hard session today - the light says rest, even though the week is below its hard share.",
    # EASY — four readings of the week, under one amber light.
    "Easy only today - the light says take it easy.",
    "Easy only today - the light says take it easy; the week's hard share is already reached anyway.",
    "Easy only today - the week's hard minutes stay open and are made up once the light is green again.",
    ("Easy only today - the light says take it easy. Note for the week: the minutes above easy"
     " are there, but all of them are in zone 3 and none above threshold - the quality session"
     " is still open, for a day when the light is green."),
    ("Easy only today - the light says take it easy. Note for the week: it is far over its "
     "share of non-easy minutes and every one of them is in zone 3. What that asks for is "
     "easier easy days, not another session."),
    # GO — spacing first, then the two axes of the week.
    "Easy today - the last hard session was today; hard stimuli are 48 hours apart.",
    "Easy today - the last hard session was yesterday; hard stimuli are 48 hours apart.",
    "Today is the day for the hard session - first session of the week, light is green.",
    "Today is the day for the hard session - the week is below its hard share.",
    ("Today is the day for the hard session - the week has its minutes above easy, but all of "
     "them are in zone 3 and none above threshold. What is missing is quality, not volume."),
    ("Easy today - the week is far over its share of non-easy minutes and every one of them is"
     " in zone 3. Adding a hard session on top is not the fix; the easy days need to get "
     "easier first."),
    "Easy today - the week's hard share is reached, no further hard stimulus needed.",
}


def _grid(target_min: int) -> list[int]:
    """The `hard_min` values worth trying, DERIVED from the rule's own thresholds.

    Round numbers were the first attempt (`0, 20, 40, 80`) and they leave holes:
    a branch keyed on `hard_min == 30` sat between two sample points and the
    sweep could not see it, while the docstring claimed it covered "the whole
    input space" — which stops a reader from looking. The rule compares against
    `target_min` and `2 * target_min`, so those two boundaries and their
    neighbours are the points that can change an answer."""
    edges = {0, 1, target_min, 2 * target_min}
    return sorted({v for e in edges for v in (e - 1, e, e + 1) if v >= 0})


def _all_decisions():
    """Every `decide_today` output reachable from a grid built out of its own
    thresholds — not a sample of round numbers.

    It is still a grid, not a proof. A branch comparing `target_min` itself
    against some new constant would need that constant added here — verified:
    `hard_min == 30` and `hard_min == 61` are both caught, `target_min == 29` is
    not. That is the honest statement, and it is why `_grid` is derived from the
    rule's thresholds rather than written down as round numbers."""
    out = {}
    for verdict in (None, "GO", "EASY", "REST"):
        # 0 and 1 are the 48-hour rule's boundary, 2 its first day off.
        for days_since_hard in (None, 0, 1, 2, 5):
            # 0 is "no measured zone time yet", 1 the smallest real target.
            for target_min in (0, 1, 30):
                for hard_min in _grid(target_min):
                    # ...and the quality floor, from both sides.
                    for quality_s in (None, 0, 1, QUALITY_FLOOR_SECONDS - 1,
                                      QUALITY_FLOOR_SECONDS, QUALITY_FLOOR_SECONDS + 1):
                        r = decide_today(
                            verdict, days_since_hard=days_since_hard, hard_min=hard_min,
                            target_min=target_min, quality_s=quality_s,
                            quality_min=None if quality_s is None else round(quality_s / 60))
                        out.setdefault(r["sentence"], r)
    return out


def test_every_sentence_the_rule_can_produce_is_pinned():
    """A MECHANISM, not another hand-picked case.

    The suite grew one test per incident, so a new combination of two
    separately-fixed conditions had no test until it had produced an incident of
    its own — which is exactly where the `EASY` × `far over` arm fell through.
    This sweeps the whole input space instead: a new branch that says something
    new fails here until someone writes the sentence down and reads it.

    It is also the only place the wording itself is reviewed. The sentence is
    the product; the `reason` key is bookkeeping."""
    produced = _all_decisions()
    assert set(produced) == DECISION_SENTENCES, (
        "sentences produced but not pinned: "
        f"{sorted(set(produced) - DECISION_SENTENCES)}\n"
        "sentences pinned but unreachable: "
        f"{sorted(DECISION_SENTENCES - set(produced))}")


def test_no_sentence_contradicts_the_numbers_beside_it():
    """The UI prints "X of Y min" next to the sentence, so a sentence claiming
    the opposite of that fraction costs more credibility than it buys."""
    for sentence, r in _all_decisions().items():
        w = r["week"]
        behind = w["target_min"] > 0 and w["hard_min"] < w["target_min"]
        if "below its hard share" in sentence or "stay open" in sentence:
            assert behind, f"claims the week is open while {w}: {sentence}"
        if "share is reached" in sentence or "already reached" in sentence:
            assert not behind, f"claims the week is done while {w}: {sentence}"
        if "all of them are in zone 3" in sentence or "every one of them is in zone 3" in sentence:
            assert w.get("quality_min") == 0, f"claims no quality while {w}: {sentence}"
        if "far over" in sentence:
            assert w["hard_min"] >= 2 * w["target_min"] > 0, f"claims far over while {w}"


def test_the_recovery_light_outranks_the_week_everywhere():
    """The one rank that may never invert: a red light is never a hard day, and
    an amber light never becomes one because of a weekly target."""
    for _sentence, r in _all_decisions().items():
        if r["reason"].startswith("verdict REST"):
            assert r["decision"] == "rest"
        if r["reason"].startswith("verdict EASY"):
            assert r["decision"] == "easy"
        assert r["decision"] in ("hard", "easy", "rest", "unknown")
