"""Tool handlers — logic separated from the MCP transport. Each takes the
store explicitly, so it is testable without a running server.

They return compact, LLM-friendly text: aggregates and weekly buckets, never
day-by-day rows for a period. Facts first; where a handler adds a judgement it
is a rule-based one with the rule spelled out.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import date, timedelta

from . import paths
from .logic import run_kind
from .store import Store

_LIGHT = {"GO": "[GO]", "EASY": "[EASY]", "REST": "[REST]"}

_AVG_LABELS = [
    ("sleep_seconds", "Sleep avg", "duration"),
    ("sleep_score", "Sleep score avg", "/100"),
    ("hrv_avg_ms", "HRV avg", " ms"),
    ("resting_hr", "Resting HR avg", " bpm"),
    ("stress_avg", "Stress avg", "/100"),
    ("body_battery_high", "Body Battery (peak) avg", "/100"),
    ("steps", "Steps avg", ""),
]
_UNITS = {k: u for k, _, u in _AVG_LABELS}

# Thresholds for the interval reading in `analyze_workout`.
VO2MAX_REP_MIN_S = 150        # reps below barely reach VO2max
VO2MAX_REP_MAX_S = 360        # above: threshold/tempo endurance rather than VO2max
REP_SPEED_S = 90              # below: clearly speed/anaerobic
RECOVERY_HOT_DELTA_BPM = 12   # recovery HR closer than this to work HR = no real recovery


def _round_half_up(value: float) -> int:
    """Round half away from zero, the way `Math.round` does in the browser."""
    return int(math.floor(value + 0.5))


def _hm(seconds) -> str:
    """Seconds as "1h 07m" / "42m" / "35s".

    Minutes are ROUNDED, not truncated. Truncating made the same five 235 s reps
    read as "5x ~3m" one line above "rep length (~4 min) suits VO2max work", and
    as 5x4' in the app - three numbers for one quantity, from one source that
    calls itself the single source.

    HALF ROUNDS UP, not to even. Python's `round()` is banker's rounding and
    JavaScript's `Math.round` is not, so `ui.js: fmtHours` and this function
    disagreed on every value ~30 s from a minute boundary - 25110 s printed as
    "6h 58m" on the coach card and "6 h 59 min" on the Today tab. Roughly one
    arbitrary value in sixty, and `build_sleep`'s 14-night mean is an arbitrary
    value. `tests/test_js_python_contract.py` pins the two against each other."""
    if not seconds:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    h, m = divmod(_round_half_up(seconds / 60), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _fmt(value, unit: str) -> str:
    if value is None:
        return "-"
    if unit == "duration":
        return _hm(value)
    if unit == "":
        return f"{round(value):,}"
    return f"{value}{unit}"


def _km(meters) -> str:
    return f"{meters / 1000:.1f} km" if meters else "-"


def _window(store: Store, days: int, lo: int, hi: int) -> tuple[date, date]:
    """Every window ends on the store's analysis anchor, not on today. With a
    stale sync a window that runs to today measures the gap, not the athlete —
    and two tools with two different end days report two different ACWRs."""
    end = store.analysis_anchor()
    return end - timedelta(days=max(lo, min(days, hi)) - 1), end


def _parse_day(day: str | None) -> tuple[date | None, str | None]:
    if not day:
        return None, None
    try:
        return date.fromisoformat(day), None
    except ValueError:
        return None, f"Invalid date '{day}' - expected ISO (YYYY-MM-DD)."


def get_recovery_summary(store: Store, period_days: int = 7) -> str:
    start, end = _window(store, period_days, 1, 90)
    s = store.get_recovery_summary(start, end)
    if s["days_with_data"] == 0:
        return f"No data for {start} .. {end}. Sync pending? Call sync_garmin."
    lines = [f"Recovery {start} .. {end} ({s['days_with_data']} days with data)", ""]
    lines += [f"- {label}: {_fmt(s['averages'].get(key), unit)}" for key, label, unit in _AVG_LABELS]
    if s["latest_hrv_status"]:
        lines.append(f"- HRV status (latest): {s['latest_hrv_status']}")
    d = s["latest_day"]
    if d:
        lines += ["", f"Latest day ({d['day']}): sleep {_hm(d.get('sleep_seconds'))} "
                      f"(score {d.get('sleep_score') or '-'}), HRV {d.get('hrv_avg_ms') or '-'} ms, "
                      f"resting HR {d.get('resting_hr') or '-'} bpm, Body Battery "
                      f"{d.get('body_battery_low') or '-'}-{d.get('body_battery_high') or '-'}."]
    return "\n".join(lines)


def get_daily_metrics(store: Store, day: str | None = None) -> str:
    target, err = _parse_day(day)
    if err:
        return err
    target = target or store.latest_day()
    if target is None:
        return "No data in the database yet."
    row = store.get_day(target)
    if row is None:
        return f"No data for {target}."
    return "\n".join([
        f"{target}",
        f"- Sleep: {_hm(row.get('sleep_seconds'))} (score {row.get('sleep_score') or '-'}) - "
        f"deep {_hm(row.get('deep_sleep_seconds'))}, light {_hm(row.get('light_sleep_seconds'))}, "
        f"REM {_hm(row.get('rem_sleep_seconds'))}",
        f"- HRV: {row.get('hrv_avg_ms') or '-'} ms ({row.get('hrv_status') or '-'})",
        f"- Resting HR: {row.get('resting_hr') or '-'} bpm",
        f"- Stress avg/max: {row.get('stress_avg') or '-'}/{row.get('stress_max') or '-'}",
        f"- Body Battery: {row.get('body_battery_low') or '-'}-{row.get('body_battery_high') or '-'}",
        f"- Steps: {row.get('steps') or '-'}",
    ])


def get_trend(store: Store, metric: str, period_days: int = 28) -> str:
    start, end = _window(store, period_days, 7, 365)
    try:
        buckets = store.get_trend(metric, start, end)
    except ValueError as exc:
        return str(exc)
    if not buckets:
        return f"No data for '{metric}' in {start} .. {end}."
    unit = _UNITS.get(metric, "")
    return "\n".join([f"Trend `{metric}` (weekly avg, {start} .. {end}):", ""] + [
        f"- week of {b['week_start']}: {_fmt(b['avg_value'], unit)} ({b['days']} days)"
        for b in buckets])


def get_training_load(store: Store, period_days: int = 28) -> str:
    period_days = max(14, min(period_days, 90))
    t = store.get_training_load(store.analysis_anchor(), period_days)
    if t["acwr"] is None and not t["weekly_load"]:
        return f"No training data for {t['window_start']} .. {t['window_end']} (workouts synced?)."
    lines = [f"Training load {t['window_start']} .. {t['window_end']}", ""]
    if t["training_status"]:
        lines.append(f"- Status: {t['training_status']}")
    if t["vo2max"]:
        chg = t["vo2max_change"]
        lines.append(f"- VO2max: {t['vo2max']}" + (f" ({chg:+} in window)" if chg else ""))
    if t["acwr"] is not None:
        status = f" / {t['acwr_status']}" if t["acwr_status"] else ""
        lines.append(f"- ACWR (acute:chronic): {t['acwr']} [{t['acwr_source']}]{status} - "
                     f"acute 7d {t['acute_load_7d']}, chronic ~{t['chronic_load_weekly']}/week, "
                     f"{t['workouts_7d']} workouts/7d")
    lt = store.latest_lactate_threshold()
    if lt:
        pace = round(1000 / lt["lt_speed_mps"]) if lt.get("lt_speed_mps") else None
        lines.append(f"- Lactate threshold (Garmin, measured {lt.get('lt_measured_on') or '?'}): "
                     f"{lt['lthr_bpm']} bpm" + (f" / {pace // 60}:{pace % 60:02d} per km" if pace else ""))
    if t["weekly_load"]:
        # The oldest bucket is cut off by the window start and the newest one by
        # the window end - the running week. Saying so is the whole point: read as
        # full weeks they turn a steady block into an invented ramp and an
        # invented deload, and a ramp is exactly what this block is consulted for.
        lines += ["", "Weekly load:"] + [
            f"- week of {w['week_start']}: load {w['load']} ({w['workouts']} workouts)"
            + (" - PARTIAL week, the window covers only part of it; not comparable"
               if w.get("partial") else "")
            for w in t["weekly_load"]]
    return "\n".join(lines)


def get_recent_activities(store: Store, period_days: int = 14) -> str:
    start, end = _window(store, period_days, 7, 90)
    ra = store.get_recent_activities(start, end)
    if not ra["by_type"]:
        return f"No workouts {start} .. {end}."
    lines = [f"Workouts {start} .. {end}", ""]
    for t in ra["by_type"]:
        te = f", avg aerobic TE {t['avg_aerobic_te']}" if t["avg_aerobic_te"] else ""
        lines.append(f"- {t['activity_type'] or '?'}: {t['workouts']}x / load {t['load']} / "
                     f"{_km(t['distance_m'])} / {_hm(t['duration_s'])}{te}")
    lines += ["", "Latest:"]
    for a in ra["recent"]:
        te = f" TE {a['aerobic_te']}/{a['anaerobic_te']}" if a["aerobic_te"] is not None else ""
        hr = f" / {a['avg_hr']} bpm" if a["avg_hr"] else ""
        kind = run_kind(a)
        label = (a["activity_type"] or "?") + (f" ({kind})" if kind else "")
        lines.append(f"- {a['day']} {label}: {_km(a['distance_m'])}/{_hm(a['duration_s'])} / "
                     f"load {a['training_load'] or '-'}{te}{hr}")
    return "\n".join(lines)


def get_training_readiness(store: Store) -> str:
    r = store.get_readiness()
    if r["verdict"] is None:
        return "No data yet for a readiness verdict (sync pending?)."
    s = r["signals"]
    rhr = "-" if s.get("resting_hr") is None else (
        f"{s['resting_hr']} bpm" + (f" (baseline {s['resting_hr_baseline']})"
                                    if s.get("resting_hr_baseline") is not None else ""))
    acwr = "-" if s.get("acwr") is None else f"{s['acwr']} ({s.get('acwr_source')})"
    dsh = s.get("days_since_hard_workout")
    today = paths.today()
    stale = "" if r["day"] == today.isoformat() else \
        f"\nNOTE: latest data is from {r['day']}, not today - treat the verdict as stale."
    plan = store.get_scheduled_workouts(today, today + timedelta(days=6))
    if plan:
        # Titles are the athlete's own calendar entries, but still free text.
        stale += "\n\nGarmin calendar, next 7 days (untrusted labels): " + "; ".join(
            f"{'TODAY' if p['day'] == today.isoformat() else p['day']} \"{p['title'] or '?'}\""
            for p in plan[:7])
    else:
        stale += "\n\nGarmin calendar: nothing scheduled in the next 7 days."
    return "\n".join([
        f"{_LIGHT.get(r['verdict'], '')} Readiness {r['day']}: {r['verdict']}",
        f"Reasons: {', '.join(r['reasons'])}",
        "",
        f"Signals: HRV {s.get('hrv_status') or '-'} / sleep score {s.get('sleep_score') or '-'} / "
        f"Body Battery {s.get('body_battery_high') or '-'} / resting HR {rhr} / ACWR {acwr} / "
        f"status {s.get('training_status') or '-'} / "
        # "- day(s) ago" was a dash where a number belongs, and it reads as a
        # rendering glitch rather than as information. `None` here means one
        # specific thing - no hard session anywhere in the 28-day window - and
        # for an athlete stuck in the grey zone that is the single most
        # important line on the page.
        + (f"last hard workout {dsh} day(s) ago" if dsh is not None
           else "no hard workout in the last 28 days"),
        "",
        _decision_block(store, today),
    ]) + stale


def _decision_block(store: Store, today: date) -> str:
    """The app's own decision for today, verbatim, so the agent explains or
    contests THIS one instead of deriving a second verdict of its own.

    Without it the Today tab showed the rule's decision and, directly beneath it,
    a coach card that had reached its conclusion independently — two verdicts on
    one screen with nothing making them agree."""
    from . import snapshot

    weeks = store.get_weekly_volume(today - timedelta(weeks=snapshot.WEEKS), today)
    r = store.get_readiness()
    d = snapshot.build_decision({k: r.get(k) for k in ("day", "verdict", "signals")}, weeks, today)
    w = d["week"]
    # Both numbers, because the decision reads both. "40 of 32 min above easy" on
    # its own looks like a finished week even when every one of those minutes was
    # grey-zone - and that is precisely the case the rule now calls out.
    quality = (f", of which {w['quality_min']} above threshold"
               if w.get("quality_min") is not None else "")
    return (f"The app's decision for today: {d['decision'].upper()} - \"{d['sentence']}\" "
            f"(rule: {d['reason']}; this week {w['hard_min']} of {w['target_min']} min above the "
            f"easy zones{quality}). This is the decision the athlete sees. Explain it, or disagree "
            f"with it using numbers - do not silently replace it with your own.")


def get_intensity_distribution(store: Store, period_days: int = 28) -> str:
    start, end = _window(store, period_days, 7, 90)
    d = store.get_intensity_distribution(start, end)
    if d["total_s"] == 0:
        if d["total_runs"] == 0:
            return f"No runs {start} .. {end}."
        return (f"No zone detail yet for {start} .. {end} ({d['total_runs']} run(s) without "
                f"detail - the next sync picks them up).")
    lines = [
        f"Intensity distribution {start} .. {end} ({d['with_detail']}/{d['total_runs']} runs with detail)",
        "",
        f"- Easy (Z1-2):     {_hm(d['easy_s'])} ({d['easy_pct']}%)",
        f"- Moderate (Z3):   {_hm(d['moderate_s'])} ({d['moderate_pct']}%)",
        f"- Hard (Z4-5):     {_hm(d['hard_s'])} ({d['hard_pct']}%)",
        f"    of which Z5:   {_hm(d['vo2max_s'])} ({d['vo2max_pct']}%)",
    ]
    # Only runs WITH zone detail carry seconds, so the percentages describe those
    # runs and not the training. Nineteen easy runs awaiting detail plus one
    # interval session used to produce a flat "little true easy running (17.5%)"
    # — a verdict on the backfill, dressed as a verdict on the athlete. No
    # judgement below the sample gate; the numbers above still stand.
    covered = d["with_detail"] >= max(5, round(0.6 * d["total_runs"]))
    if not covered:
        lines += ["", f"No judgement on the distribution: only {d['with_detail']} of "
                      f"{d['total_runs']} runs carry zone detail, so these shares describe "
                      f"those runs, not the training."]
        return "\n".join(lines)
    notes = []
    if d["moderate_pct"] >= 35:
        notes.append(f"a lot of grey zone (Z3, {d['moderate_pct']}%) - weakly polarised")
    # The polarised model wants ~80 % easy; complaining only below 60 % named one
    # target and applied another twenty points away.
    if d["easy_pct"] < 75:
        notes.append(f"little true easy running ({d['easy_pct']}% vs ~80%)")
    if d["vo2max_pct"] < 3:
        notes.append(f"hardly any time in Z5 ({d['vo2max_pct']}%)")
    lines += ["", ("Rule-based notes: " + "; ".join(notes) + ".") if notes
              else "Looks polarised (mostly easy, focused hard work)."]
    return "\n".join(lines)


def analyze_workout(store: Store, day: str | None = None, activity_id: int | None = None) -> str:
    target, err = _parse_day(day)
    if err:
        return err
    a = store.get_workout_analysis(activity_id=activity_id, day=target)
    if a is None:
        return f"No run with detail data found{f' for {day}' if day else ''} (detail not synced yet?)."

    z = a["zones_s"]
    z5 = f" (>= {a['hr_z5_low']} bpm)" if a["hr_z5_low"] else ""
    lines = [
        f"Analysis {a['day']} - {a['activity_type'] or 'run'} {_km(a['distance_m'])}/{_hm(a['duration_s'])}"
        + (f" - \"{a['name']}\" (untrusted label)" if a.get("name") else ""),
        "",
        f"- HR avg/max: {a['avg_hr'] or '-'}/{a['max_hr'] or '-'} bpm"
        + (f" / cadence {a['avg_cadence']}" if a["avg_cadence"] else ""),
        f"- Time in zone: Z1 {_hm(z['z1'])} / Z2 {_hm(z['z2'])} / Z3 {_hm(z['z3'])} / "
        f"Z4 {_hm(z['z4'])} / Z5 {_hm(z['z5'])}",
        f"- VO2max range{z5}: {_hm(a['vo2max_s'])}",
    ]
    if a["hr_z4_low"] or a["hr_z5_low"]:
        lines.append(f"- Garmin zone bounds on this run: Z4 from {a['hr_z4_low'] or '-'} bpm, "
                     f"Z5 from {a['hr_z5_low'] or '-'} bpm")
    ctx = []
    if a.get("temperature_c") is not None:
        hum = f"/{a['humidity_pct']}% rh" if a.get("humidity_pct") is not None else ""
        ctx.append(f"{a['temperature_c']} C{hum}")
    if a.get("performance_condition") is not None:
        # "median": this is the per-run median, NOT the momentary value the watch shows.
        ctx.append(f"performance condition (median) {a['performance_condition']:+}")
    if ctx:
        lines.append("- Context: " + " / ".join(ctx))

    notes: list[str] = []
    if a["has_intervals"]:
        rep_s = a["avg_rep_duration_s"]
        rep = "-" if rep_s is None else (f"{round(rep_s)}s" if rep_s < 90 else _hm(rep_s))
        lines += ["",
                  # Rep count/length = Garmin's auto-segmentation, NOT necessarily the
                  # planned workout. Labelled explicitly so the coach never plays it as
                  # fact against the structure the athlete reports.
                  f"Intervals (Garmin auto-detection, not necessarily the planned structure): "
                  f"{a['rep_count']}x ~{rep} (avg work HR {a['avg_active_hr'] or '-'}, "
                  f"avg recovery HR {a['avg_recovery_hr'] or '-'})"]
        if rep_s is not None:
            if rep_s < REP_SPEED_S:
                notes.append(f"auto-detected reps short (~{round(rep_s)}s): "
                             f"speed/anaerobic, not VO2max-optimal")
            elif rep_s < VO2MAX_REP_MIN_S:
                notes.append(f"auto-detected reps borderline short (~{round(rep_s)}s) "
                             f"for VO2max - aim for 3-5 min")
            elif rep_s <= VO2MAX_REP_MAX_S:
                notes.append(f"auto-detected rep length (~{round(rep_s / 60)} min) suits VO2max work")
            else:
                notes.append(f"auto-detected reps long (~{round(rep_s / 60)} min): "
                             f"threshold/tempo rather than VO2max")
        ah, rh = a["avg_active_hr"], a["avg_recovery_hr"]
        if ah and rh and rh >= ah - RECOVERY_HOT_DELTA_BPM:
            notes.append(f"recovery HR ({rh}) barely drops below work HR ({ah}): recoveries too hot")
    elif a["structure_kind"] == "steady":
        lines += ["", "Steady run (no interval structure)."]

    if a["vo2max_s"] is not None and a["vo2max_s"] < 120 and (a["high_s"] or 0) > 600:
        notes.append(f"a lot of hard time but only {_hm(a['vo2max_s'])} truly in Z5")
    if notes:
        lines += ["", "Rule-based notes: " + "; ".join(notes) + "."]
    return "\n".join(lines)


def get_vo2max_history(store: Store) -> str:
    """VO2max as STEPS (only the days it changed) plus a descriptive 28-vs-28-day
    factor comparison. No causal claim — that is the reader's job."""
    from . import snapshot

    today = paths.today()
    series = store.get_daily_series(today - timedelta(days=snapshot.SERIES_DAYS), today)
    v = snapshot.build_vo2max(series, snapshot.build_runs(store, snapshot.RUNS_FOR_FACTORS), today=today)
    if v["current"] is None:
        return "No VO2max values yet."
    lines = [f"VO2max {v['current']} (as of {v['current_day']}); change 28d {v['change_28d']}, "
             f"56d {v['change_56d']}. Garmin carries the value forward - only the days below "
             f"are real changes:"]
    lines += [f"- {c['day']}: {c['value']}" for c in v["changed_days"]] or ["- (no change in the window)"]
    f = v["factors"]
    if f:
        lines += ["", "Last 28 days vs the 28 before (descriptive, not causal):"]
        for label, b in (("last 28d", f["last_28d"]), ("prev 28d", f["prev_28d"])):
            lines.append(f"- {label}: {b['runs']} runs, {b['distance_km']} km, Z5 {b['z5_min']} min, "
                         f"easy {b['easy_pct']}%, avg temp {b['avg_temp_c']} C "
                         f"({b['with_detail']} with zone detail)")
        if not f["covers_full_window"]:
            lines.append("NOTE: the older block is NOT fully covered by the data - do not read "
                         "the difference as a change.")
    return "\n".join(lines)


#: The one tool that reaches OUT of the machine, and the only one an injected
#: workout name can use to cost the athlete something. The human refresh button
#: has had a cooldown and a single-flight lock from the start; this path — the
#: one actually driven by untrusted text — had neither, so "call sync_garmin
#: again" repeated in a prompt meant tens of fresh logins and hundreds of API
#: calls inside one job, which is how an account gets throttled.
SYNC_COOLDOWN_S = 60
#: `None`, not `0.0`, for "never synced in this process".
#:
#: `time.monotonic()` counts from an arbitrary point - on Linux, machine boot.
#: With `0.0` as the sentinel, `now - 0.0` is the UPTIME, so the guard only
#: worked on a machine that had been running longer than the cooldown. On a
#: freshly booted one the FIRST sync of the day was refused with "Synced 43 s
#: ago - the data you just read is current", which is not a throttle, it is a
#: false statement. CI found it; every developer machine had been up for days
#: and could not.
_last_sync: list[float | None] = [None]
_sync_lock = threading.Lock()


def sync_garmin(store: Store, days: int = 1) -> str:
    """On-demand sync, small window. Always text, never raises."""
    import os

    from . import garmin, sync

    if os.environ.get("RUNCOACH_DEMO"):
        return ("Sync OK - data is complete up to today (synthetic demo athlete, "
                "no Garmin account attached).")
    if not _sync_lock.acquire(blocking=False):
        return "A sync is already running. Use the data you have; it will be current in a moment."
    try:
        last = _last_sync[0]
        waited = None if last is None else time.monotonic() - last
        if waited is not None and waited < SYNC_COOLDOWN_S:
            return (f"Synced {round(waited)} s ago - Garmin rate-limits repeated pulls, so this "
                    f"one was skipped. The data you just read is current.")
        _last_sync[0] = time.monotonic()
        return _sync_now(store, garmin, sync, days)
    finally:
        _sync_lock.release()


def _sync_now(store: Store, garmin, sync, days: int) -> str:
    try:
        client = garmin.login()
    except Exception as exc:  # noqa: BLE001 — a tool returns text, never an exception
        return (f"Garmin login failed ({type(exc).__name__}) - the stored session has probably "
                f"expired. Ask the user to run `runcoach login`.")
    lines: list[str] = []
    try:
        rep = sync.run(store, client, max(1, min(days, 7)), say=lines.append)
    except Exception as exc:  # noqa: BLE001 — a tool returns text, never an exception
        return f"Garmin fetch not possible right now ({type(exc).__name__}, rate limit?) - retry later."
    return rep.summary()


# ── planning: propose, then (after a human's yes) apply ─────────────────────

def propose_workout(store: Store, kind: str, distance_km: float | None = None,
                    duration_min: int | None = None, day: str | None = None,
                    name: str | None = None) -> str:
    """Build a session from the athlete's own zones and file it as a proposal.
    Text for the model AND the athlete; nothing touches Garmin here."""
    import os

    from . import plan

    try:
        target = date.fromisoformat(day) if day else None
    except ValueError:
        return f"day must be YYYY-MM-DD, got {day!r}"
    if os.environ.get("RUNCOACH_DEMO"):
        # The demo athlete has zones, so the proposal is real; only applying
        # would need an account. Say so up front rather than at the click.
        note = "\n(demo data: the proposal is real, applying needs a Garmin login)"
    else:
        note = ""
    try:
        p = plan.propose(store, kind, distance_km=distance_km, duration_min=duration_min,
                         day=target, name=name, today=paths.today())
    except ValueError as exc:
        return f"Cannot build that session: {exc}"
    return (f"{p['preview']}\n"
            f"planned for {p['day']} - proposal {p['id']}\n"
            f"NOT on Garmin yet. Show this to the athlete; if they say yes, call "
            f"apply_workout(proposal_id=\"{p['id']}\"). If they want changes, propose again."
            f"{note}")


def apply_workout(store: Store, proposal_id: str) -> str:
    """Write ONE proposal to Garmin. The only tool that changes the account."""
    import os

    from . import garmin, plan

    if os.environ.get("RUNCOACH_DEMO"):
        return "Demo mode has no Garmin account to write to - run with real data and a login."
    if plan.read(proposal_id) is None:
        return plan.apply(store, None, proposal_id)["error"]
    try:
        client = garmin.login()
    except Exception as exc:  # noqa: BLE001 — a tool returns text, never an exception
        return (f"Garmin login failed ({type(exc).__name__}) - nothing was written. "
                f"Ask the athlete to run `runcoach login`, then apply again.")
    return plan.describe_result(plan.apply(store, client, proposal_id, today=paths.today()))
