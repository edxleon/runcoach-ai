"""`runspector today`: the morning in a few lines, for a terminal or a cron
with any notifier. Exit 0 with a call for today, 3 without values for today
(said in the text, so "not synced" never reads like a rest day), 2 when the
data cannot be read."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

from conftest import make_activity, make_day
from runspector import cli, planning, tools
from runspector.models import ScheduledWorkout


def _today(capsys, sync=False, store=None):
    if store is not None:
        import runspector.store as store_mod

        real = store_mod.Store
        store_mod.Store = lambda *a, **k: store
    try:
        code = cli.cmd_today(SimpleNamespace(sync=sync))
    finally:
        if store is not None:
            store_mod.Store = real
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_fresh_morning_is_the_call_its_sentence_and_its_signals(store, today, capsys):
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=82, body_battery_high=80,
                                resting_hr=48))
    code, out, _ = _today(capsys, store=store)
    assert code == 0
    first = out.splitlines()[0]
    assert first.startswith(f"{today:%a %d %b}: ") and any(w in first for w in ("GO", "EASY", "REST"))
    assert " - " in first, "the call comes with its sentence"
    assert "HRV BALANCED · sleep score 82 · Body Battery 80 · resting HR 48" in out


def test_without_values_for_today_it_says_so_and_exits_3(store, today, capsys):
    store.upsert_daily(make_day(today - timedelta(days=2), hrv_status="BALANCED", sleep_score=82))
    code, out, _ = _today(capsys, store=store)
    assert code == 3
    assert "no values for today yet - the latest are from" in out and "--sync" in out
    assert not any(w in out.split(":", 1)[1] for w in (" GO", " REST")), "no call on a day it cannot see"


def test_an_empty_store_is_no_values_not_a_crash(store, capsys):
    code, out, _ = _today(capsys, store=store)
    assert code == 3 and "no values for today yet" in out


def test_todays_session_comes_with_the_plans_own_easier_option(store, today, capsys):
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=82, body_battery_high=80))
    spec = planning.with_fallback(planning.build_session("vo2max", duration_min=58, zones={}))
    store.record_workout(900010, name=spec.name, kind="vo2max",
                         spec_json=json.dumps(planning.to_json(spec)), schedule_id=790,
                         scheduled_day=today)
    store.replace_scheduled_workouts([ScheduledWorkout(790, today, workout_id=900010,
                                                       title=spec.name, sport="running")],
                                     today, today)
    _, out, _ = _today(capsys, store=store)
    assert f'Planned: "{spec.name}" - if too much today: 48 min easy instead' in out


def test_garmins_own_score_is_shown_as_garmins(store, today, capsys):
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=82, body_battery_high=80,
                                garmin_readiness=72, garmin_readiness_level="MODERATE"))
    _, out, _ = _today(capsys, store=store)
    assert "Garmin's own Training Readiness: 72 (moderate) - its score; the call is this app's" in out


def test_the_call_is_the_one_the_today_tab_shows(store, today):
    """One decision, two surfaces: a hard day yesterday makes today EASY on the
    page and in the morning text alike."""
    from runspector import snapshot

    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=82, body_battery_high=80))
    store.upsert_activity(make_activity(9_300_001, today - timedelta(days=1), aerobic_te=3.8))
    text, fresh = tools.today_text(store)
    decision = snapshot.assemble(store, today=today)["decision_today"]
    assert fresh and text.splitlines()[0].split(": ", 1)[1].startswith(
        tools._CALL[decision["decision"]] + " - " + decision["sentence"][:20])


def test_a_failed_sync_still_shows_the_last_data(store, today, capsys, monkeypatch):
    from runspector import sync

    def no_session(days, say=print):
        raise RuntimeError("no Garmin session")

    monkeypatch.setattr(sync, "sync_now", no_session)
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=82))
    code, out, err = _today(capsys, sync=True, store=store)
    assert code == 0 and "Sync failed (RuntimeError)" in err and "showing the last data" in err
    assert out.startswith(f"{today:%a %d %b}: ")


def test_an_unreadable_database_is_exit_2_with_a_cause(capsys, monkeypatch):
    def broken(_store):
        raise OSError("disk I/O error")

    monkeypatch.setattr(tools, "today_text", broken)
    code, _, err = _today(capsys)
    assert code == 2 and "disk I/O error" in err and "runspector doctor" in err


def _app_session_today(store, today):
    spec = planning.with_fallback(planning.build_session("vo2max", duration_min=58, zones={}))
    store.record_workout(900011, name=spec.name, kind="vo2max",
                         spec_json=json.dumps(planning.to_json(spec)), schedule_id=791,
                         scheduled_day=today)
    store.replace_scheduled_workouts([ScheduledWorkout(791, today, workout_id=900011,
                                                       title=spec.name, sport="running")],
                                     today, today)
    return spec


def test_a_rest_day_offers_no_run_at_all(store, today, capsys):
    """Review finding: REST in the first line, "if too much today: 48 min easy"
    under it - a morning notification offering a run on a red day."""
    store.upsert_daily(make_day(today, hrv_status="LOW", sleep_score=40, body_battery_high=30))
    spec = _app_session_today(store, today)
    _, out, _ = _today(capsys, store=store)
    assert out.splitlines()[0].split(": ", 1)[1].startswith("REST"), out
    assert "48 min" not in out and f'Planned: "{spec.name}" - not today' in out, out


def test_an_easy_day_offers_the_plans_option_as_today_s_run(store, today, capsys):
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=82, body_battery_high=80))
    store.upsert_activity(make_activity(9_300_002, today - timedelta(days=1), aerobic_te=3.8))
    spec = _app_session_today(store, today)
    _, out, _ = _today(capsys, store=store)
    assert out.splitlines()[0].split(": ", 1)[1].startswith("EASY"), out
    # Not "moves to another day": nothing moves it - the swap replaces it.
    assert (f'Planned: "{spec.name}" - not on an easy day; instead 48 min easy '
            f"(the plan's own easier option)") in out, out


def test_a_long_run_that_stands_on_an_easy_day_keeps_its_easier_option(store, today, capsys):
    """Round 2: an easy day keeps the long run (it is easy running), and the
    fix for rest days had hidden its option there - a tired athlete got none."""
    store.upsert_daily(make_day(today, hrv_status="BALANCED", sleep_score=82, body_battery_high=80))
    store.upsert_activity(make_activity(9_300_003, today - timedelta(days=1), aerobic_te=3.8))
    spec = planning.with_fallback(planning.build_session("long", duration_min=95, zones={}),
                                  week_easy_min=45)
    store.record_workout(900012, name=spec.name, kind="long",
                         spec_json=json.dumps(planning.to_json(spec)), schedule_id=792,
                         scheduled_day=today)
    store.replace_scheduled_workouts([ScheduledWorkout(792, today, workout_id=900012,
                                                       title=spec.name, sport="running")],
                                     today, today)
    _, out, _ = _today(capsys, store=store)
    assert out.splitlines()[0].split(": ", 1)[1].startswith("EASY"), out
    assert f'Planned: "{spec.name}" - if too much today: 45 min easy instead' in out, out
    assert "(if too much today: 45 min easy" in tools.get_training_readiness(store)
