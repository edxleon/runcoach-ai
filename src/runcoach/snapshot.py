"""The web app's data: store → ONE JSON document, assembled on demand.

Everything the UI shows is computed here, once. The frontend renders; it does
not re-derive thresholds or classifications (two independent computations of
the same run on one screen will eventually disagree).

Descriptive numbers only. Interpretation is the coach's job, not the snapshot's.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone

from . import paths
from .logic import decide_today
from .store import Store

SCHEMA = 1
SERIES_DAYS = 56
WEEKS = 8
RUNS = 30               # shown in the app
RUNS_FOR_FACTORS = 90   # READ. The VO2max factor comparison puts 28 days against
                        # the 28 before; with only 30 runs read, the older block was
                        # cut off and reported "181 km vs 8.5 km" — an artefact of
                        # the list, not a training change.

#: The hard SHARE from the 80/20 rule. A share, not a fixed number of minutes:
#: fixed minutes are volume-blind (20 % of a 55 km week is ~66 min, of a 21 km
#: week ~26), so a deload week would report "target missed" although the share
#: was right. Every week derives its own target from its own volume.
HARD_SHARE_TARGET = 0.20

_NAME_MAX = 80


def _clean_name(v) -> str | None:
    """Activity names come from Garmin and may contain anything.

    ONE implementation, `garmin._clean_text`. This used to be a second one built
    from a literal character class - exactly the shape that function's own
    comment condemns ("one careless edit put the invisible characters themselves
    into this file") - and it was the WEAKER of the two: it passed U+202E, the
    bidi isolates, U+061C, the soft hyphen, U+2060, U+180E and the whole tag
    block that the ingest strips.

    A second guard that catches less than the first adds no protection, only the
    appearance of one. It still runs, because rows written by an older ingest
    reach this path without having passed the other."""
    from .garmin import _clean_text

    return _clean_text(v, _NAME_MAX) if isinstance(v, str) else None


def _pace(distance_m, duration_s) -> float | None:
    """Seconds per kilometre; None rather than a division-by-zero fantasy."""
    if not distance_m or not duration_s or distance_m < 100:
        return None
    return round(duration_s / (distance_m / 1000.0), 1)


def lt_pace_s_per_km(mps: float | None) -> int | None:
    return round(1000 / mps) if mps and mps > 0 else None


def band(zones_s: dict | None) -> str | None:
    """Intensity band of one session — the ONE source: hard from 40 % Z4–5,
    moderate from 30 % Z3 or 20 % hard, else easy."""
    if not isinstance(zones_s, dict):
        return None
    z = {k: float(zones_s.get(k) or 0) for k in ("z1", "z2", "z3", "z4", "z5")}
    total = sum(z.values())
    if total <= 0:
        return None
    hard = (z["z4"] + z["z5"]) / total
    if hard >= 0.4:
        return "hard"
    if z["z3"] / total >= 0.3 or hard >= 0.2:
        return "moderate"
    return "easy"


def load_profile() -> dict:
    """Optional athlete profile (`profile.json`): max_hr, aerobic_ref_hr, goals."""
    try:
        return json.loads(paths.profile_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _zone_s(w: dict) -> int:
    return (w.get("easy_s") or 0) + (w.get("moderate_s") or 0) + (w.get("hard_s") or 0)


def annotate_weeks(weeks: list[dict], today: date) -> None:
    """Add `above_easy_min`, `quality_min` and `target_min` to every week, in
    place. THE ONE PLACE these three are computed.

    They used to be computed twice: here for the Today tab's sentence, and again
    in `logic.js: intensitySeries` for the Trend chart. Iteration 3 unified the
    numerator and left the TARGET duplicated — so on the demo data the same week
    read "17 of 43 min, below its share" in the sentence and showed a bar three
    times over its line in the chart, under identical words. A quantity that
    exists twice drifts; the fix is not to synchronise the second copy but to
    delete it, so the page reads numbers instead of deriving them.

    The target is a SHARE (`HARD_SHARE_TARGET`) of measured zone time — not of
    `duration_s`, or a strength session would raise the bar and a run without
    zone detail would make it unreachable.

    One correction, and it is why the two formulas differed in the first place:
    an INCOMPLETE week is measured against what a normal week looks like (the
    mean zone time of the last four full weeks) rather than against its own
    stunted total. Without it, one hard session on a Tuesday puts the running
    week at a ~30 % share and "share reached" cancels Thursday's intervals."""
    full: list[int] = []
    for w in weeks:
        above_easy_s = (w.get("moderate_s") or 0) + (w.get("hard_s") or 0)
        w["above_easy_min"] = round(above_easy_s / 60)
        # Rounded for DISPLAY, but `decide_today` asks "is there any quality at
        # all?", and 29 s above threshold rounds to 0 - the rule would then say
        # "none above threshold" about a week that had some. So the minutes are
        # the label and `quality_s` is what the rule reads.
        w["quality_s"] = int(w.get("hard_s") or 0)
        w["quality_min"] = round((w.get("hard_s") or 0) / 60)
        own = _zone_s(w)
        incomplete = bool(w.get("partial")) or w.get("week_start") == _monday(today)
        typical = sum(full) / len(full) if full else 0
        base = max(own, typical) if incomplete else own
        w["target_min"] = round(base * HARD_SHARE_TARGET / 60)
        if not incomplete:
            full = (full + [own])[-4:]


def _monday(day: date) -> str:
    return (day - timedelta(days=day.weekday())).isoformat()


def build_decision(today_block: dict, weeks: list[dict], today: date) -> dict:
    """The ONE sentence for the Today tab (rule: `logic.decide_today`).

    TWO numerators, both handed to `decide_today`: `hard_min` counts everything
    ABOVE THE EASY ZONES (Z3+Z4+Z5) as the LOAD side, `quality_min` counts Z4+Z5
    as the STIMULUS side. A full load bucket with an empty stimulus bucket is a
    week of grey-zone running, and must not read as "share reached".

    The load numerator counts everything ABOVE THE EASY ZONES — Z3, Z4 and Z5 — against
    a target of 20 % of the measured zone time. The polarised model splits training
    at the first ventilatory threshold, and Z3 sits above it: counting Z3 only in
    the denominator meant that the athlete stuck in the grey zone (the classic
    mistake `zones.md` warns about) was the one most likely to be told "the week is
    below its hard share - today is the day for the hard session". In an app whose
    stance is "rather easy than overtrained", the one rule that can prescribe hard
    work must not lean towards prescribing it.

    Denominator is MEASURED zone time, not `duration_s` — otherwise a strength
    session would raise the bar and a run without zone detail would make it
    unreachable.

    The verdict has to be from TODAY. If the sync is stale, a decision about
    today backed by yesterday's verdict would be a lie; "unknown" is then the
    only true statement."""
    monday = _monday(today)
    annotate_weeks(weeks, today)
    week = next((w for w in weeks if w.get("week_start") == monday), None)
    # Read, do not recompute. Same three fields the Trend chart draws.
    hard_min = week["above_easy_min"] if week else 0
    target_min = week["target_min"] if week else 0
    quality_min = week["quality_min"] if week else None
    quality_s = week["quality_s"] if week else None
    fresh = today_block.get("day") == today.isoformat()
    signals = today_block.get("signals") or {}
    return decide_today(
        today_block.get("verdict") if fresh else None,
        days_since_hard=signals.get("days_since_hard_workout") if fresh else None,
        hard_min=hard_min, target_min=target_min, quality_min=quality_min,
        quality_s=quality_s,
        week_start=monday if week else None)


def build_sleep(series: list[dict]) -> dict:
    """Last night + 14-day average over the measurements that exist — missing
    nights do not count as 0."""
    with_sleep = [r for r in series if r.get("sleep_seconds")]
    if not with_sleep:
        return {"day": None, "seconds": None, "score": None, "phases": {},
                "avg_14d_seconds": None, "avg_14d_score": None, "nights_14d": 0}
    latest = with_sleep[-1]
    last14 = [r["sleep_seconds"] for r in with_sleep[-14:]]
    scores14 = [r["sleep_score"] for r in with_sleep[-14:] if r.get("sleep_score") is not None]
    return {
        "day": latest["day"],
        "seconds": latest.get("sleep_seconds"),
        "score": latest.get("sleep_score"),
        "phases": {k: latest.get("awake_seconds" if k == "awake" else f"{k}_sleep_seconds")
                   for k in ("deep", "light", "rem", "awake")},
        "avg_14d_seconds": round(sum(last14) / len(last14)),
        "avg_14d_score": round(sum(scores14) / len(scores14), 1) if scores14 else None,
        "nights_14d": len(last14),
    }


def build_series(rows: list[dict]) -> dict:
    """Column-wise series for drawing. Days WITH gaps."""
    keys = ("resting_hr", "hrv_avg_ms", "sleep_score", "sleep_seconds",
            "body_battery_high", "stress_avg", "vo2max", "acwr_ratio", "steps")
    return {"days": [r["day"] for r in rows], **{k: [r.get(k) for r in rows] for k in keys}}


def build_runs(store: Store, limit: int = RUNS) -> list[dict]:
    """Only the fields the app shows (the raw row has ~35 columns)."""
    out = []
    for a in store.get_recent_runs_detail(limit=limit):
        out.append({
            "activity_id": a["activity_id"],
            "day": a.get("day"),
            "start_time": a.get("start_time"),
            "type": a.get("activity_type"),
            "name": _clean_name(a.get("name")),
            "distance_m": a.get("distance_m"),
            "duration_s": a.get("duration_s"),
            "pace_s_per_km": _pace(a.get("distance_m"), a.get("duration_s")),
            "avg_hr": a.get("avg_hr"),
            "max_hr": a.get("max_hr"),
            "training_load": a.get("training_load"),
            "aerobic_te": a.get("aerobic_te"),
            "anaerobic_te": a.get("anaerobic_te"),
            "te_label": a.get("te_label"),
            "vo2max": a.get("vo2max"),
            "performance_condition": a.get("performance_condition"),
            "temperature_c": a.get("temperature_c"),
            "avg_cadence": a.get("avg_cadence"),
            "has_detail": a.get("has_detail"),
            "zones_s": a.get("zones_s") or {},
            "band": band(a.get("zones_s")) if a.get("has_detail") else None,
            "hr_z4_low": a.get("hr_z4_low"),
            "hr_z5_low": a.get("hr_z5_low"),
            "structure": a.get("structure") or {},
        })
    return out


def build_vo2max(series: list[dict], runs: list[dict], *, today: date) -> dict:
    """VO2max is carry-forward: Garmin repeats the value until a measurement
    changes it. Hence steps instead of interpolation — and `changed_days`, so the
    UI can mark the real jumps instead of drawing noise."""
    pts = [(date.fromisoformat(r["day"]), r["vo2max"]) for r in series
           if r.get("vo2max") is not None]
    if not pts:
        return {"current": None, "change_28d": None, "change_56d": None,
                "carry_forward": True, "changed_days": [], "days_with_value": 0, "factors": {}}

    def delta(days: int):
        ref = pts[-1][0] - timedelta(days=days)
        past = [v for d, v in pts if d <= ref]
        return round(pts[-1][1] - past[-1], 1) if past else None

    changed = [{"day": d.isoformat(), "value": round(v, 1)}
               for (_, pv), (d, v) in zip(pts, pts[1:], strict=False) if v != pv]
    return {
        "current": round(pts[-1][1], 1),
        "current_day": pts[-1][0].isoformat(),
        "change_28d": delta(28),
        "change_56d": delta(56),
        "carry_forward": True,
        # The list is CAPPED for the chart's markers; the count is not, because
        # the sentence under the chart says "only the N marked jumps are new
        # measurements" and N has to be the number of jumps that exist. Above 12
        # the text and the dots disagreed, and the text was the one that was
        # wrong about the athlete's own data.
        "changed_days": changed[-12:],
        "changed_total": len(changed),
        "days_with_value": len(pts),
        # Factors that plausibly play a role — as NUMBERS, without a causal claim.
        "factors": vo2max_factors(runs, today=today),
    }


def vo2max_factors(all_sessions: list[dict], *, today: date) -> dict:
    """4-week block against the 4 weeks before — volume, Z5 minutes, easy share,
    temperature. `covers_full_window` says whether the older block was read in
    full; if not, the UI must not sell the comparison as a change."""
    # RUNS only. The session list feeds the Runs tab and holds every sport, so a
    # weekly 80 km bike ride used to double the "running volume" the coach is
    # handed as the factor behind a VO2max change.
    runs = [r for r in all_sessions if "running" in str(r.get("type") or "").lower()]
    if not runs:
        return {}
    cur_from, prev_from = today - timedelta(days=28), today - timedelta(days=56)

    def block(lo: date, hi: date) -> dict:
        sel = [r for r in runs if r.get("day") and lo < date.fromisoformat(r["day"]) <= hi]
        z = [(r.get("zones_s") or {}) for r in sel if r.get("has_detail")]
        easy = sum((s.get("z1") or 0) + (s.get("z2") or 0) for s in z)
        hard = sum((s.get("z4") or 0) + (s.get("z5") or 0) for s in z)
        total = easy + hard + sum((s.get("z3") or 0) for s in z)
        temps = [r["temperature_c"] for r in sel if r.get("temperature_c") is not None]
        return {
            "runs": len(sel),
            "distance_km": round(sum(r.get("distance_m") or 0 for r in sel) / 1000, 1),
            "z5_min": round(sum((s.get("z5") or 0) for s in z) / 60, 1),
            "easy_pct": round(100 * easy / total, 1) if total else None,
            "avg_temp_c": round(sum(temps) / len(temps), 1) if temps else None,
            "with_detail": len(z),
        }

    oldest = min((r["day"] for r in runs if r.get("day")), default=None)
    return {"last_28d": block(cur_from, today), "prev_28d": block(prev_from, cur_from),
            "covers_full_window": bool(oldest and date.fromisoformat(oldest) <= prev_from)}


def build_plan(scheduled: list[dict], runs: list[dict], today: date) -> dict:
    """Week strip: per day Mon–Sun what was planned and what was done — the bridge
    between the coach's advice and what is actually on the watch."""
    monday = today - timedelta(days=today.weekday())
    lo, hi = monday.isoformat(), (monday + timedelta(days=6)).isoformat()
    planned: dict[str, list] = {}
    upcoming: list[dict] = []
    for g in scheduled or []:
        entry = {"title": g.get("title"), "sport": g.get("sport"), "workout_id": g.get("workout_id")}
        if lo <= g["day"] <= hi:
            planned.setdefault(g["day"], []).append(entry)
        elif g["day"] > hi:
            upcoming.append({"day": g["day"], **entry})
    done: dict[str, list] = {}
    for r in runs or []:
        if r.get("day") and lo <= r["day"] <= hi:
            done.setdefault(r["day"], []).append({
                "activity_id": r.get("activity_id"), "name": r.get("name"), "type": r.get("type"),
                "training_load": r.get("training_load"), "band": r.get("band")})
    days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    return {"week_start": lo, "today": today.isoformat(),
            "days": [{"day": d, "planned": planned.get(d, []), "done": done.get(d, [])} for d in days],
            "upcoming": sorted(upcoming, key=lambda x: x["day"])[:6]}


def build_aerobic(runs_wide: list[dict], *, today: date, ref_hr: int, weeks: int = WEEKS) -> dict:
    """Easy pace at a fixed heart rate: the most honest base-fitness signal. Each
    easy run's pace is scaled to the reference HR (pace × HR/ref — roughly linear
    within zone 2), median per week. Runs under 3 km are dropped.

    NO endpoint difference as a headline: a series with a standard deviation of
    ~8 s and weekly jumps of up to 21 s turns "first vs last" into "-20 s/km, the
    base is getting faster" when the first point merely happened to be the
    maximum. Reported instead: the SPREAD and a least-squares trend — the UI may
    only speak once the trend exceeds the spread."""
    start = today - timedelta(weeks=weeks)
    start = (start - timedelta(days=start.weekday())).isoformat()
    per_week: dict[str, list] = {}
    temp_per_week: dict[str, list] = {}
    for r in runs_wide or []:
        if "running" not in str(r.get("type") or "").lower():
            continue
        d = r.get("day") or ""
        hr, pace = r.get("avg_hr"), r.get("pace_s_per_km")
        if d < start or not hr or not pace or (r.get("distance_m") or 0) < 3000:
            continue
        b = band(r.get("zones_s")) if r.get("has_detail") else None
        if not (b == "easy" if b else hr <= ref_hr + 6):
            continue
        wk = date.fromisoformat(d)
        wk = (wk - timedelta(days=wk.weekday())).isoformat()
        per_week.setdefault(wk, []).append(float(pace) * float(hr) / ref_hr)
        if r.get("temperature_c") is not None:
            temp_per_week.setdefault(wk, []).append(float(r["temperature_c"]))
    points = []
    for wk in sorted(per_week):
        vals = sorted(per_week[wk])
        mid = len(vals) // 2
        med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
        temps = temp_per_week.get(wk)
        points.append({"week_start": wk, "pace_s_per_km": round(med), "n": len(vals),
                       "temp_c": round(sum(temps) / len(temps), 1) if temps else None})
    values = [p["pace_s_per_km"] for p in points]
    n = len(values)
    mean = sum(values) / n if n else None
    sd = ((sum((v - mean) ** 2 for v in values) / (n - 1)) ** 0.5) if n >= 2 else None
    slope = None
    if n >= 3:
        # Regress against CALENDAR weeks, not list positions. Weeks without a
        # qualifying easy run are simply absent from `points`, so counting
        # positions compressed the x-axis: an 8-week span with a 6-week gap
        # reported the same "per week" rate as three consecutive weeks.
        first = date.fromisoformat(points[0]["week_start"])
        xs = [(date.fromisoformat(p["week_start"]) - first).days / 7 for p in points]
        xm = sum(xs) / n
        denom = sum((x - xm) ** 2 for x in xs)
        cov = sum((x - xm) * (v - mean) for x, v in zip(xs, values, strict=True))
        slope = (cov / denom) if denom else None
    return {"ref_hr": ref_hr, "points": points,
            "current": values[-1] if values else None,
            "mean_s": round(mean) if mean is not None else None,
            "spread_s": round(sd, 1) if sd is not None else None,
            # seconds per kilometre PER WEEK; negative = getting faster
            "trend_s_per_week": round(slope, 1) if slope is not None else None,
            "weeks": weeks}


def build_predictions(rp: dict | None) -> dict:
    rp = rp or {}
    return {"day": rp.get("day"), "k5_s": rp.get("race_5k_s"), "k10_s": rp.get("race_10k_s"),
            "hm_s": rp.get("race_hm_s"), "m_s": rp.get("race_m_s")}


def build_zones(runs: list[dict], lt: dict | None, lt_history: list[dict] | None,
                max_hr: dict | None) -> dict:
    """ONE number per boundary, with date and origin. The source is the zone
    bounds Garmin attaches to the run (`hr_z4_low`/`hr_z5_low`) — NOT a formula
    recomputed here (that would just add another competing number).

    The lactate threshold carries its own MEASUREMENT date, deliberately separate
    from `as_of_day`: bounds come from the last run, the threshold from the last
    Firstbeat measurement; these can be weeks apart and the card must not blur it."""
    lt, max_hr = lt or {}, max_hr or {}
    block = {
        "lthr_bpm": lt.get("lthr_bpm"),
        "lt_speed_mps": lt.get("lt_speed_mps"),
        "lt_pace_s_per_km": lt_pace_s_per_km(lt.get("lt_speed_mps")),
        "lt_measured_on": lt.get("lt_measured_on"),
        "lt_history": [{"day": p["day"], "lthr_bpm": p.get("lthr_bpm"),
                        "lt_pace_s_per_km": lt_pace_s_per_km(p.get("lt_speed_mps"))}
                       for p in (lt_history or []) if p.get("day")],
        # Highest measured HR of the last 90 days: context for the threshold. A
        # threshold at ~94 % of it is unusually high — either the threshold is
        # optimistic or max HR has not been hit for a long time. That belongs next
        # to the number, not in a chat.
        "max_hr_observed": max_hr.get("max_hr"),
        "max_hr_day": max_hr.get("day"),
        "lthr_pct_max_hr": (round(100 * lt["lthr_bpm"] / max_hr["max_hr"])
                            if lt.get("lthr_bpm") and max_hr.get("max_hr") else None),
    }
    for r in runs:
        if r.get("hr_z4_low") or r.get("hr_z5_low"):
            return {"z4_low": r.get("hr_z4_low"), "z5_low": r.get("hr_z5_low"),
                    "as_of_day": r.get("day"), "source": "Garmin zones on the run", **block,
                    "note": ("Zone bounds from the last run, threshold from Garmin's last "
                             "measurement - two values, two dates." if block["lthr_bpm"] else
                             "Garmin measures the threshold on hard runs; no measurement yet.")}
    return {"z4_low": None, "z5_low": None, "as_of_day": None, "source": None, **block,
            "note": "No zone bounds in the recent runs."}


def aerobic_ref_hr(profile: dict, lt: dict | None) -> int:
    """Reference HR for the aerobic-efficiency card: explicit profile value, else
    ~80 % of the measured threshold HR (mid zone 2), else 140."""
    if isinstance(profile.get("aerobic_ref_hr"), int):
        return profile["aerobic_ref_hr"]
    if lt and lt.get("lthr_bpm"):
        return round(0.80 * lt["lthr_bpm"])
    return 140


#: When the data counts as stale. ONE number, emitted in the snapshot so the
#: page reads it instead of carrying its own copy: the banner used to appear at
#: 2 days while `runcoach doctor` still said `[ok]` until 3 - and the banner
#: points the athlete at `doctor` to explain itself.
STALE_AFTER_DAYS = 3

#: What a failed read of these two looks like. Shaped like the real thing, so a
#: degraded snapshot renders as "no data" rather than crashing the consumer that
#: was promised a dict.
_EMPTY_LOAD = {"window_start": None, "window_end": None, "training_status": None,
               "acwr": None, "acwr_source": None, "acwr_status": None,
               "acute_load_7d": 0, "chronic_load_weekly": 0, "workouts_7d": 0,
               "vo2max": None, "vo2max_change": None, "weekly_load": []}
_EMPTY_INTENSITY = {"easy_s": 0, "moderate_s": 0, "hard_s": 0, "z5_s": 0, "total_s": 0,
                    "total_runs": 0, "with_detail": 0}
_EMPTY_READINESS = {"day": None, "verdict": None, "reasons": ["Recovery data unreadable."],
                    "reason_flags": [], "signals": {}}


def assemble(store: Store, *, today: date | None = None) -> dict:
    today = today or paths.today()
    degraded: list[str] = []

    def soft(fn, *args, default=None):
        """A read that must not take the whole snapshot down. The UI shows "block
        unreadable" for these instead of a confident "nothing planned"."""
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            degraded.append(fn.__name__)
            print(f"  ! {fn.__name__}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return default

    # A FILE-LEVEL probe first, deliberately outside `soft`. If the database
    # itself cannot be opened ("file is not a database", a malformed image), the
    # honest answer is a 500 the handler turns into "database unreadable" — not a
    # page of zeros with a long `degraded` list, which reads as "you have no
    # training data" and invites the user to restart instead of restore.
    # Individual block failures below stay soft; this one cannot be.
    latest = store.latest_day()

    # EVERY other block goes through `soft`, not just the one that happened to
    # fail once. The wrapper's whole point is that a single unreadable table degrades
    # one card instead of the page; guarding one of eight reads meant `degraded`
    # could only ever name `latest_lactate_threshold`, and any other failure was
    # still a 500 with an empty screen behind it.
    series_rows = soft(store.get_daily_series, today - timedelta(days=SERIES_DAYS), today,
                       default=[])
    runs_wide = soft(build_runs, store, RUNS_FOR_FACTORS, default=[])
    runs = runs_wide[:RUNS]
    # The SAME anchor the readiness verdict and the MCP tools use. Taking
    # `latest_day()` here put two different ACWRs on one screen as soon as today
    # had a row without any recovery signal — steps alone are enough for sync to
    # write one — and the Load tab then explained a number the verdict never saw.
    anchor = soft(store.analysis_anchor) or today
    tl = soft(store.get_training_load, anchor, 28, default=_EMPTY_LOAD)

    # Align on MONDAY: `today - 8 weeks` lands mid-week, and the first bucket
    # would then only contain the days from the window start on — claiming a rest
    # week that never happened and dragging the weekly average down.
    weeks_start = today - timedelta(weeks=WEEKS)
    weeks_start -= timedelta(days=weeks_start.weekday())
    weeks = soft(store.get_weekly_volume, weeks_start, today, default=[])

    r = soft(store.get_readiness, today, default=_EMPTY_READINESS)
    today_block = {k: r.get(k) for k in ("day", "verdict", "reasons", "reason_flags", "signals")}
    lt = soft(store.latest_lactate_threshold)
    profile = load_profile()

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_through": latest.isoformat() if latest else None,
        "stale_days": (today - latest).days if latest else None,
        "today": today_block,
        "decision_today": build_decision(today_block, weeks, today),
        "sleep": build_sleep(series_rows),
        "load": {
            "acwr": tl["acwr"], "acwr_status": tl["acwr_status"], "acwr_source": tl["acwr_source"],
            "training_status": tl["training_status"], "acute_7d": tl["acute_load_7d"],
            "chronic_weekly": tl["chronic_load_weekly"], "weekly_load": tl["weekly_load"],
        },
        "series": build_series(series_rows),
        "weeks": weeks,
        "runs": runs,
        "intensity": {
            # Inclusive BETWEEN, so `days - 1`, and ending on the anchor: a card
            # labelled "28 days" covered 29 and ended on a day the data does not
            # reach, which is a different question from the one the MCP tool answers.
            "d28": soft(store.get_intensity_distribution, anchor - timedelta(days=27), anchor,
                        default=_EMPTY_INTENSITY),
            "d84": soft(store.get_intensity_distribution, anchor - timedelta(days=83), anchor,
                        default=_EMPTY_INTENSITY),
        },
        "vo2max": build_vo2max(series_rows, runs_wide, today=today),
        "zones": build_zones(runs, lt, soft(store.lactate_threshold_history, default=[]),
                             soft(store.max_hr_since, today - timedelta(days=90))),
        "plan": build_plan(
            soft(store.get_scheduled_workouts, today - timedelta(days=today.weekday()),
                 today + timedelta(days=14), default=[]), runs, today),
        "aerobic": build_aerobic(runs_wide, today=today, ref_hr=aerobic_ref_hr(profile, lt)),
        "predictions": build_predictions(soft(store.latest_race_predictions)),
        "targets": {"hard_share": HARD_SHARE_TARGET},
        "stale_after_days": STALE_AFTER_DAYS,
        "profile": {k: profile.get(k) for k in ("max_hr", "goal") if profile.get(k)},
        "degraded": sorted(set(degraded)),
        "counts": {"days": len(series_rows), "runs": len(runs), "weeks": len(weeks)},
    }
