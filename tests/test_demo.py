"""The synthetic athlete must light up every block of the app."""

from __future__ import annotations

import json

import pytest

from conftest import TODAY
from runcoach import demo, paths, snapshot


@pytest.fixture(scope="module")
def demo_snapshot(tmp_path_factory):
    """Seeded ONCE per module (seeding is ~400 single writes) into the default
    location under an isolated RUNCOACH_HOME, with `paths.today()` frozen."""
    home = tmp_path_factory.mktemp("demo-home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("RUNCOACH_HOME", str(home))
        mp.setenv("RUNCOACH_TZ", "Europe/Berlin")
        mp.setattr(paths, "today", lambda: TODAY)
        store = demo.seed()
        assert store.path == str(home / "demo.db")
        return store, snapshot.assemble(store, today=TODAY)


def test_demo_snapshot_is_complete(demo_snapshot, today):
    _, snap = demo_snapshot
    assert snap["today"]["day"] == today.isoformat()
    assert snap["today"]["verdict"] in {"GO", "EASY", "REST"}
    assert snap["decision_today"]["decision"] in {"hard", "easy", "rest"}
    assert snap["stale_days"] == 0
    assert len(snap["weeks"]) >= 8
    assert len(snap["runs"]) >= 30
    assert snap["degraded"] == []
    json.dumps(snap)


def test_demo_has_a_labelled_interval_session(demo_snapshot):
    _, snap = demo_snapshot
    intervals = [r for r in snap["runs"] if r["structure"]["kind"] == "intervals"]
    labels = {r["name"]: r["structure"]["label"] for r in intervals}
    assert labels == {"VO2max 5x4min": "5×4′", "Threshold 3x10min": "3×10′"}
    assert all(r["structure"]["rep_count"] >= 2 for r in intervals)
    assert all(r["has_detail"] and r["band"] for r in snap["runs"])
    assert {r["band"] for r in snap["runs"]} >= {"easy", "hard"}


def test_demo_has_threshold_predictions_and_plan(demo_snapshot, today):
    _, snap = demo_snapshot
    z = snap["zones"]
    assert z["lthr_bpm"] is not None and z["lt_pace_s_per_km"] is not None
    assert z["lt_measured_on"] and len(z["lt_history"]) >= 2
    assert z["z4_low"] == 165 and z["z5_low"] == 178
    assert snap["predictions"]["day"] == today.isoformat()
    assert all(snap["predictions"][k] for k in ("k5_s", "k10_s", "hm_s", "m_s"))
    planned = [p for d in snap["plan"]["days"] for p in d["planned"]]
    assert planned and snap["plan"]["upcoming"]
    assert snap["vo2max"]["current"] is not None and snap["vo2max"]["changed_days"]
    assert snap["vo2max"]["factors"]["covers_full_window"] is True
    assert snap["aerobic"]["points"] and snap["sleep"]["nights_14d"] == 14
    assert snap["load"]["acwr_source"] == "garmin"
    assert snap["intensity"]["d28"]["total_s"] > 0


def test_demo_today_is_planned_not_done(demo_snapshot, today):
    _, snap = demo_snapshot
    assert all(r["day"] < today.isoformat() for r in snap["runs"])


def test_demo_is_deterministic_and_reseedable(demo_snapshot, today):
    store, first = demo_snapshot
    reseeded = demo.seed(store.path)                              # wipes and rebuilds
    second = snapshot.assemble(reseeded, today=today)
    assert {**first, "generated_at": None} == {**second, "generated_at": None}
    assert reseeded.counts()["days"] == demo.WEEKS * 7 + 1
