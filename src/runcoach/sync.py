"""Garmin → SQLite sync. Fully unattended: the token resume needs no
interaction, so this can run from `runcoach serve`, the refresh button or cron.

By default the last 3 days are re-fetched, not just yesterday: Garmin finalises
sleep/HRV hours later and occasionally corrects values afterwards. The upsert
makes that idempotent.

Hard errors (a day or workout could not be synced) carry the exit code. The
soft side channels (threshold history, race predictions, calendar mirror) are
report only — but visible: a permanently failing calendar must not look like a
healthy sync.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import timedelta

from . import garmin, paths
from .store import Store

log = logging.getLogger(__name__)


class _StoppedError(Exception):
    """Garmin stopped us mid-run. Unwinds to the summary instead of past it."""


DEFAULT_DAYS = 3
ZONE_KEYS = ("hr_z1_s", "hr_z2_s", "hr_z3_s", "hr_z4_s", "hr_z5_s")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class SyncReport:
    days_written: int = 0
    days_empty: int = 0
    activities: int = 0
    details: int = 0
    errors: int = 0
    soft_errors: int = 0
    #: Set when Garmin itself stopped us (rate limit, expired session, no route).
    #: Distinguishing this from "some days failed" is what lets the CLI tell the
    #: athlete to wait rather than to log in again.
    fatal: str | None = None
    lines: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.fatal:
            return 2
        return 3 if self.errors else 0

    def summary(self) -> str:
        mark = "!" if (self.errors or self.soft_errors or self.fatal) else "OK"
        soft = f", {self.soft_errors} soft (side channels)" if self.soft_errors else ""
        head = (f"[{mark}] {self.days_written} day(s), {self.days_empty} empty, "
                f"{self.activities} workout(s), {self.details} with detail, "
                f"{self.errors} error(s){soft}")
        return f"{head}\nAborted by Garmin: {self.fatal}" if self.fatal else head


def ingest_detail(store: Store, client, activity_id: int) -> tuple[str, dict]:
    """Fetch and write HR zones + splits of ONE activity. Returns `(outcome, detail)`
    with outcome 'written' or 'soft_fail': a RUN with completely empty detail is NOT
    marked as synced (transient endpoint failure → retry next time), whereas
    strength/walk without detail is normal and gets marked."""
    det = garmin.fetch_activity_detail(client, activity_id)
    # An endpoint that FAILED is not an endpoint that answered "none". Writing the
    # stamp here removed the workout from `activities_missing_detail` forever, so
    # one transient 500 deleted a run's HR zones permanently - while the sync
    # printed "1 with detail, 0 errors" and exited 0.
    if set(det.get("unknown") or ()) & set(garmin.DETAIL_ESSENTIAL):
        return "soft_fail", det
    empty = not any(det.get(k) is not None for k in ZONE_KEYS) and not det["splits"]
    if empty and "running" in (store.activity_type(activity_id) or "").lower():
        return "soft_fail", det
    # SPLITS FIRST, stamp last. `update_activity_detail` sets `detail_synced_at`,
    # which is what removes the workout from `activities_missing_detail` — for
    # good. Stamping before the split write meant one transient "database is
    # locked" left the run permanently marked as detailed with zero intervals,
    # and nothing would ever fetch them again. The stamp is a promise about what
    # is already in the database, so it has to be written after everything it
    # promises.
    # ONLY when the endpoint actually answered. `upsert_activity_splits` is a
    # full REPLACE, so writing an empty list because the endpoint returned a 500
    # deletes a run's interval structure - and the stamp below then makes sure
    # nothing ever fetches it again. `update_activity_detail` has honoured
    # `unknown` since it was added; the split write did not, which left the same
    # silent permanent loss one layer down.
    if "splits" not in (det.get("unknown") or ()):
        store.upsert_activity_splits(activity_id, det["splits"])
    store.update_activity_detail(activity_id, det)
    return "written", det


def _fetch_day_with_retry(client, day, *, retries: int = 1):
    """One day, with one retry. A FATAL error (429 / auth / connection) is
    re-raised instead of retried: retrying into a rate limiter that just said
    stop only deepens the block, and `run()` needs to abort the whole sync
    rather than spend 60 more requests proving the point."""
    for attempt in range(retries + 1):
        try:
            return garmin.fetch_day(client, day)
        except Exception as exc:
            if garmin.is_fatal(exc):
                raise
            if attempt < retries:
                time.sleep(5 * (attempt + 1))
            else:
                log.error("%s: %s: %s", day.isoformat(), type(exc).__name__, exc)
                return None


def run(store: Store, client, days: int = DEFAULT_DAYS, *, say=print) -> SyncReport:
    days = max(1, min(days, 90))   # Garmin rate limits: no excessive backfills
    rep = SyncReport()
    today = paths.today()

    for offset in range(days):
        day = today - timedelta(days=offset)
        try:
            m = _fetch_day_with_retry(client, day)
        except Exception as exc:  # noqa: BLE001 — fatal: rate limited, logged out, gone
            rep.errors += 1
            rep.fatal = f"{type(exc).__name__}: {exc}"
            say(f"  ! {day}: {rep.fatal} - sync aborted, nothing else will be fetched")
            break
        if m is None:
            rep.errors += 1
            say(f"  ! {day}: Garmin error - skipped")
        elif not m.has_any_metric():
            rep.days_empty += 1
            say(f"  . {day}: no metrics (watch off?)")
        else:
            store.upsert_daily(m)
            rep.days_written += 1
            if m.unknown:
                # Visible, not silent: the stored values for those columns are
                # kept, but "0 error(s)" would have claimed a complete day.
                rep.soft_errors += 1
                say(f"  . {day}: {len(m.unknown)} field(s) not retrieved "
                    f"({', '.join(sorted(m.unknown)[:3])}…) - kept what was stored")
            say(f"  + {day}: sleep={m.sleep_seconds and m.sleep_seconds // 60}min hrv={m.hrv_avg_ms} "
                f"rhr={m.resting_hr} status={m.training_status} acwr={m.acwr_ratio} vo2max={m.vo2max}")

    if rep.fatal:   # Garmin said stop — asking it 60 more times will not help
        say(rep.summary())
        return rep

    # Activities: a wider window than the daily fetches. One cheap call delivers
    # the ~28-day load history that the ACWR fallback needs.
    act_days = max(days, _env_int("RUNCOACH_ACTIVITY_BACKFILL_DAYS", 35))
    since = today - timedelta(days=act_days - 1)
    try:
        # One day wider: an early-morning local run can start on the previous UTC day.
        acts = garmin.fetch_activities(client, since - timedelta(days=1))
    except Exception as exc:  # noqa: BLE001 — one failed call, not a failed sync
        rep.errors += 1
        acts = []
        if garmin.is_fatal(exc):
            # Same class as a fatal day error: Garmin stopped us. Without this the
            # exit code said "partial" and the CLI advised a re-login instead of
            # "wait" — for a 429 that is the one piece of advice that cannot help.
            rep.fatal = f"{type(exc).__name__}: {exc}"
        say(f"  ! workout fetch failed: {type(exc).__name__}: {exc}")
    if acts is None:                      # the endpoint answered with an error
        rep.errors += 1
        acts = []
        say("  ! workout fetch failed - the endpoint returned an error, no workouts read")
    for a in acts:
        try:
            store.upsert_activity(a)
            rep.activities += 1
        except Exception as exc:  # noqa: BLE001 — one broken insert, not the batch
            rep.errors += 1
            say(f"  ! workout {a.activity_id}: {type(exc).__name__}: {exc}")

    # Detail backfill: four Garmin calls per workout → capped, newest first,
    # gentle backoff. The rest is picked up by the next run.
    for aid in store.activities_missing_detail(since, today, limit=20):
        try:
            outcome, _ = ingest_detail(store, client, aid)
            if outcome == "soft_fail":
                # Documented as benign ("retry next time"), so it must not paint
                # the sync red: counting it made a normal cron run exit 3 and the
                # web refresh show an error state.
                rep.soft_errors += 1
                say(f"  . detail {aid}: incomplete (endpoint failed or empty) - retry next sync")
                continue
            rep.details += 1
            time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001 — one workout's detail, not the sync
            rep.errors += 1
            if garmin.is_fatal(exc):   # do not keep hammering a throttled Garmin
                say(f"  ! detail backfill aborted ({type(exc).__name__}) - rest next sync")
                break
            say(f"  ! detail {aid}: {type(exc).__name__}: {exc}")

    try:
        _side_channel(rep, say, "lactate threshold", lambda: _sync_threshold(store, client, today))
        _side_channel(rep, say, "race predictions", lambda: _sync_predictions(store, client, today))
        _side_channel(rep, say, "calendar", lambda: _sync_calendar(store, client, today, rep, say))
    except _StoppedError:
        pass

    say(rep.summary())
    return rep


def _side_channel(rep: SyncReport, say, label: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — side channels report, they do not abort
        if garmin.is_fatal(exc):
            # Do NOT re-raise: that lost the whole report, so the days that WERE
            # written went unreported and the summary line never printed.
            rep.fatal = f"{type(exc).__name__}: {exc}"
            say(f"  ! {label}: {rep.fatal} - Garmin stopped us, skipping the rest")
            raise _StoppedError from exc
        rep.soft_errors += 1
        say(f"  ! {label}: {type(exc).__name__}: {exc}")


def _sync_threshold(store: Store, client, today) -> None:
    lt_days = _env_int("RUNCOACH_LT_HISTORY_DAYS", 180)
    points = garmin.fetch_lactate_history(client, today - timedelta(days=lt_days), today)
    if points:
        store.upsert_lactate_history(points)


def _sync_predictions(store: Store, client, today) -> None:
    """Race predictions are a profile value: they go onto the most recent day row
    that exists, not onto `today`. `update_daily_fields` never inserts, so an
    early-morning run before today's row exists would have dropped them silently."""
    rp = garmin.fetch_race_predictions(client)
    if rp:
        store.update_daily_fields(store.latest_day() or today, rp)


def _sync_calendar(store: Store, client, today, rep: SyncReport, say) -> None:
    start, end = today - timedelta(days=7), today + timedelta(days=14)
    plan, complete = garmin.fetch_scheduled_workouts(client, start, end)
    if not complete:
        # A full replace on half a source deletes real entries.
        rep.soft_errors += 1
        say(f"  ! calendar fetched incompletely - mirror left as is ({len(plan)} entries seen)")
        return
    store.replace_scheduled_workouts(plan, start, end)


def sync_now(days: int = DEFAULT_DAYS, *, say=print) -> SyncReport:
    """Login + run. Raises on login failure (caller decides how to report it)."""
    client = garmin.login()
    return run(Store(), client, days, say=say)
