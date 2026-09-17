"""Synthetic athlete for `runcoach serve --demo`: twelve weeks of plausible
data, deterministic (fixed seed) and anchored on today. Lets anyone see the app
without a Garmin account — and keeps screenshots free of personal data."""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from . import paths
from .models import Activity, ActivitySplit, DailyMetrics, ScheduledWorkout
from .store import Store

WEEKS = 12

# weekday → (kind, km, name)
_WEEK_PLAN = {
    1: ("tempo", 9.0, "Threshold 3x10min"),
    3: ("intervals", 10.5, "VO2max 5x4min"),
    5: ("easy", 7.0, "Recovery jog"),
    6: ("long", 18.0, "Long run"),
}


def _zones(kind: str, duration_s: int, rng: random.Random) -> dict:
    share = {
        "easy": (0.18, 0.70, 0.10, 0.02, 0.0),
        "long": (0.10, 0.66, 0.20, 0.04, 0.0),
        "intervals": (0.12, 0.34, 0.14, 0.22, 0.18),
        "tempo": (0.10, 0.34, 0.12, 0.40, 0.04),
    }[kind]
    raw = [max(0.0, s + rng.uniform(-0.02, 0.02)) for s in share]
    total = sum(raw)
    return {f"hr_z{i + 1}_s": round(duration_s * r / total) for i, r in enumerate(raw)}


#: kind -> (reps, seconds per rep, metres per rep, recovery seconds, work HR)
_STRUCTURE = {"intervals": (5, 240, 900, 150, 172), "tempo": (3, 600, 2150, 120, 164)}

#: kind -> the numbers a session of that type produces
_PACE = {"easy": 345, "long": 338, "intervals": 300, "tempo": 312}
_AVG_HR = {"easy": 139, "long": 144, "intervals": 158, "tempo": 156}
_MAX_HR = {"easy": 154, "long": 161, "intervals": 186, "tempo": 174}
_LOAD = {"easy": 78, "long": 190, "intervals": 210, "tempo": 165}
_AEROBIC_TE = {"easy": 2.6, "long": 3.6, "intervals": 3.9, "tempo": 3.7}
_ANAEROBIC_TE = {"easy": 0.2, "long": 0.4, "intervals": 2.6, "tempo": 1.2}
_TE_LABEL = {"easy": "AEROBIC_BASE", "long": "AEROBIC_BASE",
             "intervals": "VO2MAX", "tempo": "LACTATE_THRESHOLD"}


def _splits(aid: int, kind: str, duration_s: int, distance_m: int) -> list[ActivitySplit]:
    if kind not in ("intervals", "tempo"):
        return [ActivitySplit(aid, 0, "RWD_RUN", distance_m, duration_s, 142, 156, 40)]
    reps, rep_s, rep_m, rest_s, hr = _STRUCTURE[kind]
    out = [ActivitySplit(aid, 0, "INTERVAL_WARMUP", 2300, 780, 138, 149, 8)]
    for rep in range(reps):
        out.append(ActivitySplit(aid, len(out), "INTERVAL_ACTIVE", rep_m, rep_s, hr + rep, hr + 7 + rep, 3))
        out.append(ActivitySplit(aid, len(out), "INTERVAL_RECOVERY", 320, rest_s, hr - 21 + rep, hr - 4, 1))
    out.append(ActivitySplit(aid, len(out), "INTERVAL_COOLDOWN", 1600, 600, 140, 150, 4))
    return out


def seed(path: Path | None = None) -> Store:
    path = path or paths.home() / "demo.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    store = Store(path)
    rng = random.Random(42)
    today = paths.today()
    start = today - timedelta(days=WEEKS * 7)
    aid = 9_000_000_000

    for offset in range(WEEKS * 7 + 1):
        day = start + timedelta(days=offset)
        week = offset // 7
        fitness = week / WEEKS                      # slow upward drift
        deload = week % 4 == 3                      # every fourth week is lighter
        kind, km, name = _WEEK_PLAN.get(day.weekday(), (None, 0, ""))
        hard_yesterday = _WEEK_PLAN.get((day.weekday() - 1) % 7, ("",))[0] in ("intervals", "long", "tempo")

        sleep = int(rng.gauss(7.2, 0.6) * 3600)
        hrv = round(rng.gauss(52 + 6 * fitness - (7 if hard_yesterday else 0), 3))
        m = DailyMetrics(
            day=day,
            sleep_seconds=sleep, deep_sleep_seconds=int(sleep * 0.19),
            light_sleep_seconds=int(sleep * 0.56), rem_sleep_seconds=int(sleep * 0.22),
            awake_seconds=int(sleep * 0.03),
            sleep_score=max(40, min(96, round(rng.gauss(78 - (8 if hard_yesterday else 0), 7)))),
            hrv_avg_ms=hrv,
            hrv_status="UNBALANCED" if hrv < 45 else "BALANCED",
            stress_avg=round(rng.gauss(31, 6)), stress_max=round(rng.gauss(82, 6)),
            rest_stress_minutes=round(rng.gauss(520, 40)),
            body_battery_high=max(35, min(100, round(rng.gauss(82 - (14 if hard_yesterday else 0), 8)))),
            body_battery_low=round(rng.gauss(18, 5)),
            resting_hr=round(rng.gauss(49 - 2 * fitness + (3 if hard_yesterday else 0), 1.2)),
            steps=round(rng.gauss(9500 + (6000 if kind else 0), 1500)),
            vo2max=round(51.0 + 2.4 * fitness + (0.2 if week % 3 == 0 else 0.0), 1),
            training_status="RECOVERY" if deload else "PRODUCTIVE",
            acute_load=round(520 * (0.6 if deload else 1.0) + rng.uniform(-40, 40)),
            chronic_load=round(470 + 60 * fitness),
            acwr_ratio=round((0.8 if deload else 1.12) + rng.uniform(-0.08, 0.1), 2),
            acwr_status="LOW" if deload else "OPTIMAL",
            intensity_moderate_min=20 if kind == "easy" else 0,
            intensity_vigorous_min=45 if kind in ("intervals", "long", "tempo") else 0,
        )
        store.upsert_daily(m)

        if not kind or day >= today:   # today's session is planned, not done yet
            continue
        aid += 1
        km *= 0.7 if deload else 1.0
        pace = _PACE[kind] - 14 * fitness + rng.uniform(-5, 5)
        distance_m, duration_s = round(km * 1000), round(km * pace)
        store.upsert_activity(Activity(
            activity_id=aid,
            start_time=datetime.combine(day, time(12, 0), tzinfo=paths.local_tz() or
                                        datetime.now().astimezone().tzinfo).astimezone(timezone.utc),
            activity_type="running", name=name, distance_m=distance_m, duration_s=duration_s,
            avg_hr=_AVG_HR[kind] + round(rng.uniform(-3, 3)),
            max_hr=_MAX_HR[kind],
            calories=round(km * 68),
            training_load=_LOAD[kind] + round(rng.uniform(-12, 12)),
            aerobic_te=_AEROBIC_TE[kind],
            anaerobic_te=_ANAEROBIC_TE[kind],
            te_label=_TE_LABEL[kind],
            vo2max=m.vo2max,
        ))
        store.update_activity_detail(aid, {
            **_zones(kind, duration_s, rng), "hr_z4_low": 165, "hr_z5_low": 178,
            "avg_cadence": 172, "temperature_c": round(rng.gauss(14, 5)),
            "humidity_pct": round(rng.gauss(64, 10)),
            "performance_condition": round(rng.gauss(2, 2)),
        })
        store.upsert_activity_splits(aid, _splits(aid, kind, duration_s, distance_m))
        if kind == "intervals" and week % 3 == 2:   # Garmin re-measures on hard runs
            store.upsert_lactate_history([{
                "day": day, "lthr_bpm": 171 + week // 4, "lt_speed_mps": round(3.55 + 0.2 * fitness, 2)}])

    store.update_daily_fields(today, {"race_5k_s": 1298, "race_10k_s": 2712,
                                      "race_hm_s": 6020, "race_m_s": 12780})
    monday = today - timedelta(days=today.weekday())
    plan = [ScheduledWorkout(7000 + i, monday + timedelta(days=wd + 7 * w), 100 + wd, name, "running")
            for w in (0, 1) for i, (wd, (_, _, name)) in enumerate(_WEEK_PLAN.items(), start=10 * w)]
    store.replace_scheduled_workouts(plan, monday, monday + timedelta(days=13))
    return store
