"""SQLite store. One file, no server, no Docker.

Two kinds of readers live here:

* **Aggregate reads** for the MCP tools — they never return day-by-day rows for
  a period. Daily series bloat an LLM context and get misread; the agent gets
  summaries and weekly buckets.
* **Series reads** for the web app — a frontend draws curves, it needs points.

Dates leave this module as ISO strings. Week buckets are computed in Python
(Monday-based), which keeps the SQL portable and the bucketing testable.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import paths
from .logic import (
    HARD_AEROBIC_TE,
    LONG_RUN_MIN_SECONDS,
    QUALITY_ANAEROBIC_TE,
    interval_facts,
    readiness_verdict,
)
from .models import Activity, ActivitySplit, DailyMetrics, ScheduledWorkout

#: Measured nights required before an average resting HR counts as a baseline.
RHR_BASELINE_MIN_DAYS = 7

#: How long a claim on a proposal's session may sit `in_flight` before it is
#: treated as the remains of a crashed apply. One upload, schedule, push and
#: read-back is seconds; this has to be longer than the longest a LIVE call
#: can hang, because reaping a claim whose apply is still running is the one
#: outcome the claim exists to prevent - the next apply would upload the same
#: session again. Half an hour is past any HTTP timeout the vendor library
#: sets and still short enough that a crashed apply is not a dead end.
CLAIM_STALE_MINUTES = 30


def _is_partial_week(monday: date, start: date, end: date) -> bool:
    """Does the window cover this week only in part?

    ONE definition, because there were two. `get_weekly_volume` checked both
    ends; `get_training_load` checked only the start, so the running week -
    which is cut off by the END, and is the newest and most influential bucket -
    was narrated to the coach as a complete week. The same word then meant two
    different things on two surfaces, which is the failure mode this codebase
    keeps producing whenever a rule exists twice."""
    return monday < start or (monday + timedelta(days=6)) > end

_MIGRATIONS = Path(__file__).parent / "migrations"
_RUNNING = "activity_type LIKE '%running%'"
#: `logic.is_hard` as SQL. Built from the same constants, and
#: `tests/test_invariants.py` asserts the two agree over a grid of 600 activities — a
#: predicate that exists twice must be pinned, not promised in a comment.
_HARD_SQL = (f"(COALESCE(aerobic_te, 0) >= {HARD_AEROBIC_TE} "
             f"OR COALESCE(anaerobic_te, 0) >= {QUALITY_ANAEROBIC_TE} "
             f"OR ({_RUNNING} AND COALESCE(duration_s, 0) >= {LONG_RUN_MIN_SECONDS}))")

#: Numeric metrics that `get_recovery_summary` averages and `get_trend` buckets.
#: `hrv_status` is a label — labels are not averaged.
TREND_METRICS = (
    "sleep_seconds", "sleep_score", "hrv_avg_ms", "stress_avg",
    "body_battery_high", "resting_hr", "steps",
)

_DETAIL_COLS = (
    "hr_z1_s", "hr_z2_s", "hr_z3_s", "hr_z4_s", "hr_z5_s", "hr_z4_low", "hr_z5_low",
    "avg_cadence", "temperature_c", "humidity_pct", "performance_condition",
)
_PROFILE_COLS = frozenset({"race_5k_s", "race_10k_s", "race_hm_s", "race_m_s"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _v(value):
    """Python value → SQLite value (dates/datetimes as ISO text, UTC)."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _d(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path or paths.db_path())
        self.migrate()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = lambda cur, row: {c[0]: row[i] for i, c in enumerate(cur.description)}
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── Migrations ──────────────────────────────────────────

    def migrate(self) -> int:
        """Apply pending `NNNN_*.sql` files in order; `PRAGMA user_version` is the
        schema version. Applied migrations are never edited — only appended to."""
        files = sorted(p for p in _MIGRATIONS.glob("*.sql") if re.match(r"\d{4}_", p.name))
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            current = conn.execute("PRAGMA user_version").fetchone()["user_version"]
            for p in files:
                version = int(p.name[:4])
                if version <= current:
                    continue
                conn.executescript(f"BEGIN;\n{p.read_text(encoding='utf-8')}\n"
                                   f"PRAGMA user_version = {version};\nCOMMIT;")
                current = version
        return current

    def schema_version(self) -> int:
        with self._conn() as conn:
            return conn.execute("PRAGMA user_version").fetchone()["user_version"]

    # ── Ingest ──────────────────────────────────────────────

    def upsert_daily(self, m: DailyMetrics) -> None:
        """Write/refresh the day row. A re-sync overwrites (Garmin corrects values,
        e.g. when a nap is later recognised as sleep) — EXCEPT columns written with
        COALESCE, where a NULL means "I don't know" rather than "delete this":

        * the profile columns, which the daily fetch deliberately never fills;
        * anything in `m.unknown`, i.e. whose Garmin endpoint just failed. One
          500 from the sleep endpoint used to wipe a stored night and still
          report zero errors."""
        cols = DailyMetrics.column_names()
        # Profile columns always, plus every column whose endpoint failed on this
        # fetch: an empty response is "we did not learn", never "there was none".
        keep = set(DailyMetrics.PROFILE_FIELDS) | set(m.unknown)
        set_clause = ", ".join(
            f"{c} = COALESCE(excluded.{c}, daily_metrics.{c})" if c in keep
            else f"{c} = excluded.{c}"
            for c in cols if c != "day")
        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO daily_metrics ({', '.join(cols)}, synced_at) "
                f"VALUES ({', '.join('?' * len(cols))}, ?) "
                f"ON CONFLICT (day) DO UPDATE SET {set_clause}, synced_at = excluded.synced_at",
                [_v(getattr(m, c)) for c in cols] + [_now()],
            )

    def upsert_activity(self, a: Activity) -> None:
        """Idempotent via Garmin's id. Touches summary columns only — detail is a
        separate write path and survives a summary re-sync."""
        cols = Activity.column_names()
        set_clause = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "activity_id")
        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO activities ({', '.join(cols)}, local_day, synced_at) "
                f"VALUES ({', '.join('?' * len(cols))}, ?, ?) "
                f"ON CONFLICT (activity_id) DO UPDATE SET {set_clause}, "
                f"local_day = excluded.local_day, synced_at = excluded.synced_at",
                [_v(getattr(a, c)) for c in cols]
                + [paths.local_day(a.start_time).isoformat(), _now()],
            )

    def update_activity_detail(self, activity_id: int, detail: dict) -> None:
        """HR-zone seconds, zone bounds, cadence, weather; marks the workout as
        detailed via `detail_synced_at`.

        Columns listed in `detail["unknown"]` are LEFT ALONE. A soft failure on
        one of the four endpoints used to write NULL over whatever was already
        there - the same class of loss `upsert_daily`'s COALESCE prevents on the
        daily row, and it was not guarded here."""
        unknown = set(detail.get("unknown") or ())
        cols = [c for c in _DETAIL_COLS if c not in unknown]
        if not cols:
            return          # nothing answered; leave the row and the stamp as they are
        set_clause = ", ".join(f"{c} = ?" for c in cols)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE activities SET {set_clause}, detail_synced_at = ? WHERE activity_id = ?",
                [detail.get(c) for c in cols] + [_now(), activity_id],
            )

    def upsert_activity_splits(self, activity_id: int, splits: list[ActivitySplit]) -> None:
        """Full replace: Garmin's (possibly corrected) split structure wins."""
        cols = ActivitySplit.column_names()
        with self._conn() as conn:
            conn.execute("DELETE FROM activity_splits WHERE activity_id = ?", (activity_id,))
            conn.executemany(
                f"INSERT INTO activity_splits ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})",
                [[getattr(s, c) for c in cols] for s in splits],
            )

    def activities_missing_detail(self, start: date, end: date, limit: int = 20) -> list[int]:
        """Newest first and capped, so one run never fires hundreds of per-activity
        Garmin calls."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT activity_id FROM activities WHERE detail_synced_at IS NULL "
                "AND local_day BETWEEN ? AND ? ORDER BY start_time DESC LIMIT ?",
                (_v(start), _v(end), limit),
            ).fetchall()
        return [r["activity_id"] for r in rows]

    def activity_type(self, activity_id: int) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT activity_type FROM activities WHERE activity_id = ?",
                               (activity_id,)).fetchone()
        return row["activity_type"] if row else None

    def upsert_lactate_history(self, points: list[dict]) -> int:
        """Write threshold measurements onto the rows of their measurement days.
        UPDATE only — a measurement day is always a run day, so the row exists;
        a bare threshold row without daily metrics would be a ghost row."""
        n = 0
        with self._conn() as conn:
            for p in points:
                if not p.get("day"):
                    continue
                day, hr, speed = _v(p["day"]), p.get("lthr_bpm"), p.get("lt_speed_mps")
                cur = conn.execute(
                    "UPDATE daily_metrics SET lthr_bpm = COALESCE(?, lthr_bpm), "
                    "lt_speed_mps = COALESCE(?, lt_speed_mps), lt_measured_on = ? "
                    "WHERE day = ? AND (lthr_bpm IS NOT ? OR lt_speed_mps IS NOT ? "
                    "OR lt_measured_on IS NOT ?)",
                    (hr, speed, day, day, hr, speed, day),
                )
                n += cur.rowcount or 0
        return n

    def update_daily_fields(self, day: date, fields: dict) -> int:
        """Set profile columns on an EXISTING day row. Never INSERT (a profile
        value does not make a measured day), and None values are dropped rather
        than nulling the column."""
        settable = {k: v for k, v in (fields or {}).items()
                    if k in _PROFILE_COLS and v is not None}
        if not settable:
            return 0
        names = sorted(settable)
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE daily_metrics SET {', '.join(f'{k} = ?' for k in names)} WHERE day = ?",
                [settable[k] for k in names] + [_v(day)],
            )
            return cur.rowcount or 0

    def replace_scheduled_workouts(self, items: list[ScheduledWorkout],
                                   start: date, end: date) -> int:
        """Mirror of the Garmin calendar for [start, end]: clear the window, then
        insert. Entries outside the window stay untouched."""
        rows = [(it.schedule_id, it.workout_id, _v(it.day), it.title, it.sport, _now())
                for it in items if start <= it.day <= end]
        with self._conn() as conn:
            conn.execute("DELETE FROM scheduled_workouts WHERE day BETWEEN ? AND ?",
                         (_v(start), _v(end)))
            conn.executemany(
                "INSERT OR REPLACE INTO scheduled_workouts "
                "(schedule_id, workout_id, day, title, sport, synced_at) VALUES (?,?,?,?,?,?)",
                rows)
        return len(rows)

    # ── Aggregate reads (MCP tools) ─────────────────────────

    def get_recovery_summary(self, start: date, end: date) -> dict:
        avg_cols = ", ".join(f"AVG({c}) AS avg_{c}" for c in TREND_METRICS)
        rng = (_v(start), _v(end))
        with self._conn() as conn:
            agg = conn.execute(
                f"SELECT COUNT(*) AS days_with_data, {avg_cols} FROM daily_metrics "
                f"WHERE day BETWEEN ? AND ?", rng).fetchone()
            latest = conn.execute(
                "SELECT * FROM daily_metrics WHERE day BETWEEN ? AND ? "
                "ORDER BY day DESC LIMIT 1", rng).fetchone()
            hrv = conn.execute(
                "SELECT hrv_status FROM daily_metrics WHERE day BETWEEN ? AND ? "
                "AND hrv_status IS NOT NULL ORDER BY day DESC LIMIT 1", rng).fetchone()
        return {
            "period_start": rng[0],
            "period_end": rng[1],
            "days_with_data": agg["days_with_data"],
            "averages": {c: (round(agg[f"avg_{c}"], 1) if agg[f"avg_{c}"] is not None else None)
                         for c in TREND_METRICS},
            "latest_hrv_status": hrv["hrv_status"] if hrv else None,
            "latest_day": latest,
        }

    def get_day(self, day: date) -> dict | None:
        with self._conn() as conn:
            return conn.execute("SELECT * FROM daily_metrics WHERE day = ?",
                                (_v(day),)).fetchone()

    def latest_day(self) -> date | None:
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(day) AS d FROM daily_metrics").fetchone()
        return _d(row["d"]) if row else None

    def is_empty(self) -> bool:
        """Nothing stored at all — BOTH tables, because a sync can write one
        without the other and did: `fetch_activities` succeeding while the
        per-day fetches come back empty leaves activities with no
        `daily_metrics` row (sync.py, `days_empty`).

        One method because the page asks the same question in JavaScript
        (`hasNoData` in logic.js) and the two answers have to agree. When they
        did not, the server skipped the startup sync while the page held
        itself to be non-empty: full Runs tab, green dot, no banner, no sync, data
        ageing in silence. `tests/test_js_python_contract.py` pins the pair."""
        with self._conn() as conn:
            return (conn.execute("SELECT 1 FROM daily_metrics LIMIT 1").fetchone() is None
                    and conn.execute("SELECT 1 FROM activities LIMIT 1").fetchone() is None)

    def get_trend(self, metric: str, start: date, end: date) -> list[dict]:
        """Weekly averages of ONE metric. `metric` is validated against
        TREND_METRICS — the column name never comes from the caller unchecked."""
        if metric not in TREND_METRICS:
            raise ValueError(f"unknown metric '{metric}' - allowed: {', '.join(TREND_METRICS)}")
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT day, {metric} AS value FROM daily_metrics "
                f"WHERE day BETWEEN ? AND ? AND {metric} IS NOT NULL ORDER BY day",
                (_v(start), _v(end))).fetchall()
        buckets: dict[date, list[float]] = {}
        for r in rows:
            buckets.setdefault(week_start(_d(r["day"])), []).append(r["value"])
        return [{"week_start": w.isoformat(), "avg_value": round(sum(v) / len(v), 1),
                 "days": len(v)} for w, v in sorted(buckets.items())]

    def get_training_load(self, end: date, lookback_days: int = 28) -> dict:
        """Load picture up to `end`: ACWR, training status, VO2max (+change) and
        weekly load buckets.

        ACWR source: Garmin's `acwr_ratio` if present; otherwise computed as acute
        7-day load ÷ (chronic 28-day load ÷ 4). `acwr_source` says which."""
        win_start = end - timedelta(days=lookback_days - 1)
        acute_start = end - timedelta(days=6)
        chronic_start = end - timedelta(days=27)

        with self._conn() as conn:
            latest = conn.execute(
                "SELECT day, training_status, acwr_ratio, acwr_status FROM daily_metrics "
                "WHERE day BETWEEN ? AND ? AND (training_status IS NOT NULL OR acwr_ratio IS NOT NULL) "
                "ORDER BY day DESC LIMIT 1", (_v(win_start), _v(end))).fetchone()
            loads = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN local_day >= ? THEN training_load END), 0) AS acute7, "
                "COALESCE(SUM(training_load), 0) AS chronic28, "
                "COALESCE(SUM(CASE WHEN local_day >= ? THEN 1 END), 0) AS acute_workouts, "
                "COUNT(*) AS total_workouts, MIN(local_day) AS first_day "
                "FROM activities WHERE local_day BETWEEN ? AND ?",
                (_v(acute_start), _v(acute_start), _v(chronic_start), _v(end))).fetchone()
            acts = conn.execute(
                "SELECT local_day, training_load FROM activities WHERE local_day BETWEEN ? AND ?",
                (_v(win_start), _v(end))).fetchall()
            vo2 = conn.execute(
                "SELECT vo2max FROM daily_metrics WHERE day BETWEEN ? AND ? "
                "AND vo2max IS NOT NULL ORDER BY day", (_v(win_start), _v(end))).fetchall()

        # ZERO-FILL, like `get_weekly_volume`. Leaving empty weeks out made a
        # training break invisible in the one block that narrates load to the
        # coach: three weeks off rendered as two adjacent, equal lines. And
        # `coach.md` tells the model to "check the weekly buckets for what
        # inflated it (a holiday week shrinks the chronic side)" - in a list
        # where holiday weeks did not appear at all.
        weekly: dict[date, dict] = {}
        if acts:
            # ...but only when there is SOMETHING in the window. With no
            # activities at all the caller says "no training data"; a column of
            # zero weeks under that would be noise where a sentence belongs.
            wk = week_start(win_start)
            while wk <= end:
                weekly[wk] = {"load": 0, "workouts": 0}
                wk += timedelta(days=7)
        for a in acts:
            w = weekly.setdefault(week_start(_d(a["local_day"])), {"load": 0, "workouts": 0})
            w["load"] += a["training_load"] or 0
            w["workouts"] += 1
        # BOTH ends, via the one predicate `get_weekly_volume` uses. The first
        # version of this flagged only the bucket cut off by the window START -
        # and the bucket cut off by the END is the running week, the newest and
        # most influential row in the block that narrates training load to the
        # coach. Four days of a week reported as a complete one is an invented
        # 40 % deload. It also meant `partial` said two different things on two
        # surfaces, which is the exact defect this whole change set was about.
        for wk, v in weekly.items():
            v["partial"] = _is_partial_week(wk, win_start, end)

        acute7, chronic28 = int(loads["acute7"]), int(loads["chronic28"])
        chronic_weekly = chronic28 / 4 if chronic28 else 0.0
        garmin_acwr = (round(float(latest["acwr_ratio"]), 2)
                       if latest and latest["acwr_ratio"] is not None else None)

        # Compute our own ACWR only with enough history — otherwise the chronic
        # base is artificially small and ACWR looks high (→ false REST). Gate on
        # BOTH span (>= 21 days) and density (>= 8 workouts).
        first_day = _d(loads["first_day"])
        history_days = (end - first_day).days if first_day else 0
        computed_acwr = None
        if chronic_weekly > 0 and history_days >= 21 and int(loads["total_workouts"]) >= 8:
            computed_acwr = min(round(acute7 / chronic_weekly, 2), 3.0)

        if garmin_acwr is not None:
            acwr, acwr_source = garmin_acwr, "garmin"
        elif computed_acwr is not None:
            acwr, acwr_source = computed_acwr, "computed"
        else:
            acwr, acwr_source = None, None

        # Report a change only if the value actually VARIES in the window: Garmin
        # carries the last value forward on days without a real measurement, so a
        # difference of window endpoints would be invented.
        values = [float(r["vo2max"]) for r in vo2]
        has_delta = len({round(v, 1) for v in values}) >= 2

        return {
            "window_start": win_start.isoformat(),
            "window_end": end.isoformat(),
            "training_status": latest["training_status"] if latest else None,
            "acwr": acwr,
            "acwr_source": acwr_source,
            "acwr_status": latest["acwr_status"] if latest else None,
            "acute_load_7d": acute7,
            "chronic_load_weekly": round(chronic_weekly) if chronic_weekly else 0,
            "workouts_7d": int(loads["acute_workouts"]),
            "vo2max": values[-1] if values else None,
            "vo2max_change": round(values[-1] - values[0], 1) if has_delta else None,
            "weekly_load": [{"week_start": w.isoformat(), **v} for w, v in sorted(weekly.items())],
        }

    def get_recent_activities(self, start: date, end: date, *, limit: int = 8) -> dict:
        """Workout digest: per sport count/load/distance/duration, plus the last
        `limit` workouts in compact form."""
        rng = (_v(start), _v(end))
        with self._conn() as conn:
            by_type = conn.execute(
                "SELECT activity_type, COUNT(*) AS workouts, "
                "COALESCE(SUM(training_load), 0) AS load, COALESCE(SUM(distance_m), 0) AS distance_m, "
                "COALESCE(SUM(duration_s), 0) AS duration_s, AVG(aerobic_te) AS avg_aerobic_te "
                "FROM activities WHERE local_day BETWEEN ? AND ? "
                "GROUP BY activity_type ORDER BY load DESC", rng).fetchall()
            recent = conn.execute(
                "SELECT local_day AS day, activity_type, name, distance_m, duration_s, avg_hr, "
                "training_load, aerobic_te, anaerobic_te, te_label FROM activities "
                "WHERE local_day BETWEEN ? AND ? ORDER BY start_time DESC LIMIT ?",
                (*rng, limit)).fetchall()
        for t in by_type:
            if t["avg_aerobic_te"] is not None:
                t["avg_aerobic_te"] = round(t["avg_aerobic_te"], 1)
        return {"window_start": rng[0], "window_end": rng[1],
                "by_type": by_type, "recent": recent}

    def get_intensity_distribution(self, start: date, end: date) -> dict:
        """Time in HR zones across all runs in the window: easy (Z1+2), moderate
        (Z3, the "grey zone"), hard (Z4+5) and vo2max (Z5). `with_detail` /
        `total_runs` shows how many runs already have detail data."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(hr_z1_s),0) AS z1, COALESCE(SUM(hr_z2_s),0) AS z2, "
                "COALESCE(SUM(hr_z3_s),0) AS z3, COALESCE(SUM(hr_z4_s),0) AS z4, "
                "COALESCE(SUM(hr_z5_s),0) AS z5, "
                "COALESCE(SUM(CASE WHEN detail_synced_at IS NOT NULL THEN 1 END),0) AS with_detail, "
                f"COUNT(*) AS total_runs FROM activities WHERE {_RUNNING} "
                "AND local_day BETWEEN ? AND ?", (_v(start), _v(end))).fetchone()
        z = {k: int(row[k]) for k in ("z1", "z2", "z3", "z4", "z5")}
        total = sum(z.values())
        easy, moderate, hard = z["z1"] + z["z2"], z["z3"], z["z4"] + z["z5"]

        def pct(x: int) -> float | None:
            return round(100 * x / total, 1) if total else None

        return {
            "window_start": start.isoformat(), "window_end": end.isoformat(),
            "zones_s": z, "total_s": total,
            "easy_s": easy, "moderate_s": moderate, "hard_s": hard, "vo2max_s": z["z5"],
            "easy_pct": pct(easy), "moderate_pct": pct(moderate),
            "hard_pct": pct(hard), "vo2max_pct": pct(z["z5"]),
            "with_detail": int(row["with_detail"]), "total_runs": int(row["total_runs"]),
        }

    def get_workout_analysis(self, activity_id: int | None = None,
                             day: date | None = None) -> dict | None:
        """Detail of ONE workout. Selection: explicit `activity_id`, else the latest
        run with detail on `day`, else the latest run with detail at all."""
        with self._conn() as conn:
            if activity_id is not None:
                act = conn.execute("SELECT * FROM activities WHERE activity_id = ?",
                                   (activity_id,)).fetchone()
            elif day is not None:
                act = conn.execute(
                    f"SELECT * FROM activities WHERE {_RUNNING} AND local_day = ? "
                    "AND detail_synced_at IS NOT NULL ORDER BY start_time DESC LIMIT 1",
                    (_v(day),)).fetchone()
            else:
                act = conn.execute(
                    f"SELECT * FROM activities WHERE {_RUNNING} "
                    "AND detail_synced_at IS NOT NULL ORDER BY start_time DESC LIMIT 1").fetchone()
            if act is None:
                return None
            splits = conn.execute(
                "SELECT * FROM activity_splits WHERE activity_id = ? ORDER BY split_index",
                (act["activity_id"],)).fetchall()

        facts = interval_facts(splits)
        return {
            **{k: act[k] for k in ("activity_id", "activity_type", "name", "distance_m",
                                   "duration_s", "avg_hr", "max_hr", "avg_cadence",
                                   "temperature_c", "humidity_pct", "performance_condition",
                                   "aerobic_te", "anaerobic_te", "training_load",
                                   "hr_z4_low", "hr_z5_low")},
            "day": act["local_day"],
            "has_detail": act["detail_synced_at"] is not None,
            "zones_s": {f"z{i}": act[f"hr_z{i}_s"] for i in range(1, 6)},
            "vo2max_s": act["hr_z5_s"],
            "high_s": (act["hr_z4_s"] or 0) + (act["hr_z5_s"] or 0),
            **{k: facts[k] for k in ("has_intervals", "rep_count", "avg_rep_duration_s",
                                     "avg_rep_distance_m", "avg_active_hr", "max_active_hr",
                                     "avg_recovery_hr", "split_count")},
            "structure_kind": facts["kind"],
            "structure_label": facts["label"],
        }

    def analysis_anchor(self) -> date:
        """The day every analysis window ends on: the most recent day that carries
        a recovery signal, or today if there is none.

        This exists because three surfaces used to pick their own end day — the
        readiness verdict took the last day with health data, the load tool took
        today, the snapshot took the last day with any row at all. With a sync
        two days stale that put the same athlete's ACWR at 1.19 and at 0.32 in
        one conversation, and the verdict was computed from the wrong window.
        A window that ends after the data does not measure a quieter athlete, it
        measures the gap."""
        today = paths.today()
        with self._conn() as conn:
            # `day <= today`: Garmin occasionally returns a row for tomorrow in a
            # timezone ahead of ours, and an anchor in the future would shift
            # every window off the data.
            row = conn.execute(
                "SELECT MAX(day) AS d FROM daily_metrics WHERE day <= ? AND (hrv_status IS NOT NULL "
                "OR sleep_score IS NOT NULL OR body_battery_high IS NOT NULL "
                "OR resting_hr IS NOT NULL)", (_v(today),)).fetchone()
        return _d(row["d"]) if row and row["d"] else today

    def last_hard_day(self, on_or_before: date) -> date | None:
        """The most recent day with a hard session up to `on_or_before`.

        Separate from `get_readiness`, which anchors on the newest day that has
        HEALTH data: the spacing rule has to hold when the nightly sync is
        behind, and a proposal built on a three-day-old readiness row would
        otherwise be told the last hard session was three days further back
        than it was."""
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT MAX(local_day) AS d FROM activities WHERE {_HARD_SQL} AND local_day <= ?",
                (_v(on_or_before),)).fetchone()
        return _d(row["d"]) if row and row["d"] else None

    def get_readiness(self, day: date | None = None) -> dict:
        """Today's readiness signals + verdict. Collects the latest day row (<= `day`),
        the resting-HR average of the 27 days BEFORE it as baseline (without the day
        itself — otherwise the current value dampens its own delta), the ACWR and the
        days since the last hard workout (`_HARD_SQL`, i.e. `logic.is_hard` in SQL).

        Everything is anchored on `analysis_anchor()`, not on today — see there."""
        anchor = day or self.analysis_anchor()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT day, hrv_status, sleep_score, body_battery_high, resting_hr, training_status "
                "FROM daily_metrics WHERE day <= ? AND (hrv_status IS NOT NULL OR sleep_score IS NOT NULL "
                "OR body_battery_high IS NOT NULL OR resting_hr IS NOT NULL) "
                "ORDER BY day DESC LIMIT 1", (_v(anchor),)).fetchone()
            if row is None:
                return {"day": None, "verdict": None, "reasons": ["No health data yet."],
                        "reason_flags": [], "signals": {}}
            # Anchor every window on the ACTUAL data day, not on today: with a stale
            # sync the baseline would otherwise include the day it is compared to.
            ref_day = _d(row["day"])
            base = conn.execute(
                "SELECT AVG(resting_hr) AS rhr, COUNT(resting_hr) AS n FROM daily_metrics "
                "WHERE day >= ? AND day < ? AND resting_hr IS NOT NULL",
                (_v(ref_day - timedelta(days=27)), row["day"])).fetchone()
            last_hard = conn.execute(
                f"SELECT MAX(local_day) AS d FROM activities WHERE {_HARD_SQL} AND local_day <= ?",
                (row["day"],)).fetchone()

        # A MINIMUM SAMPLE, like the ACWR gate right below. Resting HR is the one
        # signal that can raise a rest-rank flag on its own (+7 bpm), so a single
        # noisy reference night must not become "the baseline". After a fresh
        # install (`sync` defaults to 3 days) or a week without the watch at night,
        # an ungated AVG() over n=1 turned an otherwise green day into REST - the
        # app's headline answer, wrong from one number.
        rhr_baseline = (round(base["rhr"], 1)
                        if base and base["rhr"] is not None and base["n"] >= RHR_BASELINE_MIN_DAYS
                        else None)
        days_since_hard = ((ref_day - _d(last_hard["d"])).days
                           if last_hard and last_hard["d"] else None)
        # ACWR from ONE source, anchored on the same data day → the readiness light
        # and the training-load tool can never diverge.
        tl = self.get_training_load(ref_day, 28)

        verdict, reasons, reason_flags = readiness_verdict(
            hrv_status=row["hrv_status"], sleep_score=row["sleep_score"],
            body_battery_high=row["body_battery_high"],
            acwr=tl["acwr"], acwr_source=tl["acwr_source"],
            resting_hr=row["resting_hr"], resting_hr_baseline=rhr_baseline,
            days_since_hard=days_since_hard,
        )
        return {
            "day": row["day"], "verdict": verdict, "reasons": reasons,
            "reason_flags": reason_flags,
            "signals": {
                "hrv_status": row["hrv_status"], "sleep_score": row["sleep_score"],
                "body_battery_high": row["body_battery_high"],
                "resting_hr": row["resting_hr"], "resting_hr_baseline": rhr_baseline,
                "acwr": tl["acwr"], "acwr_source": tl["acwr_source"],
                "training_status": row["training_status"],
                "days_since_hard_workout": days_since_hard,
            },
        }

    # ── Series reads (web app) ──────────────────────────────

    def get_daily_series(self, start: date, end: date) -> list[dict]:
        """One row per day WITH gaps: a missing day is a missing measurement, the UI
        draws it as a gap rather than as zero."""
        with self._conn() as conn:
            return conn.execute(
                "SELECT day, sleep_seconds, sleep_score, deep_sleep_seconds, light_sleep_seconds, "
                "rem_sleep_seconds, awake_seconds, hrv_avg_ms, hrv_status, stress_avg, "
                "body_battery_high, resting_hr, steps, vo2max, training_status, acute_load, "
                "chronic_load, acwr_ratio, acwr_status, intensity_moderate_min, intensity_vigorous_min "
                "FROM daily_metrics WHERE day BETWEEN ? AND ? ORDER BY day",
                (_v(start), _v(end))).fetchall()

    def get_recent_runs_detail(self, limit: int = 30) -> list[dict]:
        """The latest workouts including interval structure, splits in one query."""
        with self._conn() as conn:
            acts = conn.execute(
                "SELECT * FROM activities ORDER BY start_time DESC LIMIT ?", (limit,)).fetchall()
            ids = [a["activity_id"] for a in acts]
            by_act: dict[int, list[dict]] = {i: [] for i in ids}
            if ids:
                for s in conn.execute(
                        f"SELECT * FROM activity_splits WHERE activity_id IN ({','.join('?' * len(ids))}) "
                        "ORDER BY activity_id, split_index", ids).fetchall():
                    by_act[s["activity_id"]].append(s)
        out = []
        for a in acts:
            a["day"] = a.pop("local_day")
            a["has_detail"] = a.pop("detail_synced_at") is not None
            a["zones_s"] = {f"z{i}": a.get(f"hr_z{i}_s") for i in range(1, 6)}
            a["structure"] = interval_facts(by_act[a["activity_id"]])
            a.pop("synced_at", None)
            out.append(a)
        return out

    def get_weekly_volume(self, start: date, end: date) -> list[dict]:
        """Weekly buckets with distance AND zone seconds. Weeks WITHOUT a session
        are included as zero rows: unlike a missing daily measurement, an empty
        training week is a result (nothing was trained). Leaving it out would hide
        a two-week break and inflate the weekly average.

        `partial` marks buckets that the window only covers in part — they must
        not be drawn or averaged like full weeks."""
        with self._conn() as conn:
            acts = conn.execute(
                "SELECT local_day, activity_type, distance_m, duration_s, training_load, "
                "hr_z1_s, hr_z2_s, hr_z3_s, hr_z4_s, hr_z5_s FROM activities "
                "WHERE local_day BETWEEN ? AND ?", (_v(start), _v(end))).fetchall()
        empty = {"distance_m": 0.0, "duration_s": 0, "load": 0.0, "runs": 0, "workouts": 0,
                 "easy_s": 0, "moderate_s": 0, "hard_s": 0, "z5_s": 0}
        weeks: dict[date, dict] = {}
        for a in acts:
            w = weeks.setdefault(week_start(_d(a["local_day"])), dict(empty))
            w["distance_m"] += a["distance_m"] or 0
            w["duration_s"] += a["duration_s"] or 0
            w["load"] += a["training_load"] or 0
            # `.lower()` like `_RUNNING` (SQL LIKE) and `logic.is_running`. This
            # was the only case-sensitive spelling of the predicate in the repo;
            # Garmin sends lowercase today, so it was one non-Garmin import away
            # from counting "Trail_Running" as not a run.
            w["runs"] += "running" in str(a["activity_type"] or "").lower()
            w["workouts"] += 1
            w["easy_s"] += (a["hr_z1_s"] or 0) + (a["hr_z2_s"] or 0)
            w["moderate_s"] += a["hr_z3_s"] or 0
            w["hard_s"] += (a["hr_z4_s"] or 0) + (a["hr_z5_s"] or 0)
            w["z5_s"] += a["hr_z5_s"] or 0
        out = []
        w = week_start(start)
        while w <= end:
            out.append({"week_start": w.isoformat(), **weeks.get(w, empty),
                        "partial": _is_partial_week(w, start, end)})
            w += timedelta(days=7)
        return out

    def latest_lactate_threshold(self) -> dict | None:
        with self._conn() as conn:
            return conn.execute(
                "SELECT lthr_bpm, lt_speed_mps, lt_measured_on, day AS seen_on FROM daily_metrics "
                "WHERE lthr_bpm IS NOT NULL ORDER BY day DESC LIMIT 1").fetchone()

    def lactate_threshold_history(self) -> list[dict]:
        """One point per measurement day, oldest first. Measurements are only ever
        written onto their own day row, so there is nothing to de-duplicate."""
        with self._conn() as conn:
            return conn.execute(
                "SELECT lt_measured_on AS day, lthr_bpm, lt_speed_mps FROM daily_metrics "
                "WHERE lt_measured_on IS NOT NULL ORDER BY lt_measured_on").fetchall()

    def get_scheduled_workouts(self, start: date, end: date) -> list[dict]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT schedule_id, workout_id, day, title, sport FROM scheduled_workouts "
                "WHERE day BETWEEN ? AND ? ORDER BY day, schedule_id",
                (_v(start), _v(end))).fetchall()

    # ── Workouts this app created (provenance for the write path) ──────────

    def record_workout(self, workout_id: int, *, name: str, kind: str, spec_json: str,
                       schedule_id: int | None = None, scheduled_day: date | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO runcoach_workouts (workout_id, name, kind, spec_json, created_at, "
                "schedule_id, scheduled_day) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT (workout_id) DO UPDATE SET schedule_id = excluded.schedule_id, "
                "scheduled_day = excluded.scheduled_day",
                (int(workout_id), name, kind, spec_json, _now(), schedule_id, _v(scheduled_day)))

    def own_workouts(self) -> list[dict]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT workout_id, name, kind, created_at, schedule_id, scheduled_day "
                "FROM runcoach_workouts ORDER BY created_at DESC").fetchall()

    # ── Claims: who may upload which session of a proposal ─────────────────

    def claim_proposal_item(self, proposal_id: str, index: int) -> bool:
        """`True` if THIS caller may upload session `index` of the proposal.

        The one piece of concurrency control in the write path. Two applies of
        the same proposal - a click on the card and a yes in a Claude Code
        session - used to read the same "open" status and both upload, leaving
        the session on the watch twice. Here exactly one INSERT wins.

        Taken BEFORE the upload on purpose: a crash in between loses a session
        (reported, reaped by `reap_stale_claims` and appliable again) instead
        of duplicating one on the athlete's watch."""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO proposal_items "
                "(proposal_id, item_index, claimed_at, state) VALUES (?,?,?,'in_flight')",
                (str(proposal_id), int(index), _now()))
            return cur.rowcount == 1

    def release_proposal_item(self, proposal_id: str, index: int) -> None:
        """Give the claim back after an upload that PROVABLY created nothing.

        The claim serialises two applies against each other; it is not an
        idempotency key against Garmin, which offers none. A refused upload
        (expired session, rate limit, connection refused) created nothing, so
        holding the claim would only make a transient failure permanent. For a
        failure that proves nothing either way, `mark_proposal_item_unknown`
        keeps the claim instead."""
        with self._conn() as conn:
            conn.execute("DELETE FROM proposal_items WHERE proposal_id = ? AND item_index = ? "
                         "AND workout_id IS NULL", (str(proposal_id), int(index)))

    def mark_proposal_item_unknown(self, proposal_id: str, index: int) -> None:
        """The upload may or may not have reached Garmin. Keep the claim, out of
        reach of the reaper: retrying is what would duplicate the session."""
        with self._conn() as conn:
            conn.execute("UPDATE proposal_items SET state = 'unknown' WHERE proposal_id = ? "
                         "AND item_index = ? AND workout_id IS NULL",
                         (str(proposal_id), int(index)))

    def record_proposal_item(self, proposal_id: str, index: int, workout_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE proposal_items SET workout_id = ?, state = 'done' "
                "WHERE proposal_id = ? AND item_index = ?",
                (int(workout_id), str(proposal_id), int(index)))

    def proposal_items(self, proposal_id: str) -> dict[int, int | None]:
        """`{item_index: workout_id or None}` for the proposal's SESSIONS - what
        it really put on Garmin. The database, not the proposal file, is the
        truth here: two applies writing the same file can lose each other's ids.

        Negative indices are `plan`'s own claim space for the steps that are not
        a session upload (the calendar entries it replaces, the scheduling of an
        already uploaded session). They share the table because they need the
        same "exactly one INSERT wins", and they are not sessions."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT item_index, workout_id FROM proposal_items "
                "WHERE proposal_id = ? AND item_index >= 0", (str(proposal_id),)).fetchall()
        return {r["item_index"]: r["workout_id"] for r in rows}

    def reap_stale_claims(self, minutes: int = CLAIM_STALE_MINUTES) -> int:
        """Drop claims whose apply never came back, so the session can be tried
        again. Only `in_flight` rows: a claim the apply could not resolve is
        `unknown` and stays, because re-uploading is the one outcome worse than
        a missing session."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=int(minutes))).isoformat()
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM proposal_items WHERE state = 'in_flight' "
                               "AND workout_id IS NULL AND claimed_at < ?", (cutoff,))
            return cur.rowcount

    def unresolved_claims(self) -> list[dict]:
        """Sessions whose upload never gave an answer - for `runcoach doctor`.
        Each one is a question only the athlete's Garmin library can settle."""
        with self._conn() as conn:
            return conn.execute(
                "SELECT proposal_id, item_index, claimed_at FROM proposal_items "
                "WHERE state = 'unknown' ORDER BY claimed_at").fetchall()

    def latest_race_predictions(self) -> dict | None:
        with self._conn() as conn:
            return conn.execute(
                "SELECT day, race_5k_s, race_10k_s, race_hm_s, race_m_s FROM daily_metrics "
                "WHERE race_5k_s IS NOT NULL ORDER BY day DESC LIMIT 1").fetchone()

    def max_hr_since(self, start: date) -> dict | None:
        """Highest measured heart rate since `start`, with its day. Context for the
        threshold: if it sits at 94 % of the highest value ever seen, either the
        threshold is optimistic or max HR was never reached."""
        with self._conn() as conn:
            return conn.execute(
                "SELECT max_hr, local_day AS day FROM activities WHERE max_hr IS NOT NULL "
                "AND local_day >= ? ORDER BY max_hr DESC, start_time DESC LIMIT 1",
                (_v(start),)).fetchone()

    def last_synced_at(self) -> str | None:
        """UTC timestamp of the most recent write — the data version a coach card
        was computed on."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(s) AS s FROM (SELECT MAX(synced_at) AS s FROM daily_metrics "
                "UNION ALL SELECT MAX(synced_at) FROM activities)").fetchone()
        return row["s"] if row else None

    def counts(self) -> dict:
        with self._conn() as conn:
            return {
                "days": conn.execute("SELECT COUNT(*) AS n FROM daily_metrics").fetchone()["n"],
                "activities": conn.execute("SELECT COUNT(*) AS n FROM activities").fetchone()["n"],
            }
