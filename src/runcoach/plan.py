"""Proposals: a session the athlete has SEEN and can say yes to.

`propose` builds a session (pure, `planning.py`), writes it to
`~/.runcoach/proposals/<id>.json` and returns the preview. Nothing reaches
Garmin. `apply` is the one function in this codebase that changes the
athlete's Garmin account - upload, schedule, push to the watch - and it runs
only after a human said yes: in the app that is a click on the proposal card,
in a Claude Code session it is the athlete's answer in the conversation. The
coach's unattended card runs cannot call it at all (`web/agent.py` passes
`--disallowedTools` for it - configuration, not a promise in a prompt).

A proposal is a PACKAGE: `items` holds one session ("intervals for my 10 km")
or a whole week (`propose_week`), applied after ONE yes. `replaces` names
calendar entries the apply removes first - the readiness swap, an easy run in
place of the hard session the calendar had.

Two rules carried over from the cockpit this was extracted from:

* Read back what was written and compare it with what was meant. An upload
  that "succeeded" once carried a heart-rate target on the recovery jog; the
  only way to know is to ask Garmin what it stored.
* The app deletes nothing it did not create. `runcoach_workouts` is the
  record of what it created. Replacing a calendar entry UNSCHEDULES it - the
  workout stays in the athlete's library.
"""

from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import garmin, paths, planning
from .store import Store
from .web.jobs import _read_json, _write_json

#: A proposal nobody applied for this long is stale: the readiness it was
#: built on is gone, and so is the week it was meant for.
TTL_DAYS = 7
#: The same window the sync mirrors; a scheduled day outside it would be
#: invisible on the Today tab until the window moves.
CALENDAR_WINDOW = (7, 14)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _path(proposal_id: str) -> Path:
    return paths.proposals_dir() / f"{proposal_id}.json"


def new_id() -> str:
    return f"p-{_now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def read(proposal_id: str) -> dict | None:
    if not proposal_id.startswith("p-") or "/" in proposal_id or "\\" in proposal_id:
        return None
    return _read_json(_path(proposal_id))


def open_proposals() -> list[dict]:
    """Newest first, unapplied, not expired."""
    cutoff = (_now() - timedelta(days=TTL_DAYS)).isoformat(timespec="seconds")
    out = []
    for p in sorted(paths.proposals_dir().glob("p-*.json"), reverse=True):
        d = _read_json(p)
        if d and d.get("status") == "open" and d.get("created", "") >= cutoff:
            out.append(d)
    return out


def cleanup() -> int:
    cutoff = (_now() - timedelta(days=TTL_DAYS)).isoformat(timespec="seconds")
    n = 0
    for p in paths.proposals_dir().glob("p-*.json"):
        d = _read_json(p)
        if d and d.get("created", "") < cutoff:
            p.unlink(missing_ok=True)
            n += 1
    return n


def _item(day: date, spec: planning.SessionSpec) -> dict:
    return {"day": day.isoformat(), "spec": planning.to_json(spec),
            "preview": planning.describe(spec), "workout_id": None, "schedule_id": None}


def _file(items: list[dict], *, replaces: list[dict], assumptions: list[str], zones: dict) -> dict:
    """Write a proposal - one session or a week's package - and return it.
    `items` is the truth; `day`/`days`/`preview` at the top summarise it for
    the card."""
    preview = "\n\n".join(it["preview"] for it in items)
    if replaces:
        preview += "\n\nreplaces on the calendar: " + "; ".join(
            f"{r['day']} \"{r['title'] or '?'}\" (schedule {r['schedule_id']})" for r in replaces)
    if assumptions:
        preview += "\n" + "\n".join(f"  assumption: {a}" for a in assumptions)
    proposal = {
        "id": new_id(),
        "created": _now().isoformat(timespec="seconds"),
        "day": items[0]["day"], "days": len(items),
        "items": items, "replaces": replaces,
        "preview": preview,
        "zones_as_of": zones.get("as_of_day"),
        "status": "open", "applied_at": None, "warnings": [],
    }
    cleanup()
    _write_json(_path(proposal["id"]), proposal)
    return proposal


def _calendar_entries(store: Store, schedule_ids: list[int], *, today: date) -> list[dict]:
    """The calendar entries a proposal may replace: only ids the MIRROR knows,
    so the coach cannot unschedule something it merely claims is there."""
    if not schedule_ids:
        return []
    window = {int(e["schedule_id"]): e for e in
              store.get_scheduled_workouts(today - timedelta(days=CALENDAR_WINDOW[0]),
                                           today + timedelta(days=CALENDAR_WINDOW[1]))}
    out = []
    for sid in schedule_ids:
        if int(sid) not in window:
            raise ValueError(f"schedule {sid} is not on the calendar the app knows - sync first, "
                             f"then use the id get_training_readiness prints")
        e = window[int(sid)]
        out.append({"schedule_id": int(sid), "workout_id": e.get("workout_id"),
                    "title": e.get("title"), "day": e.get("day")})
    return out


def propose(store: Store, kind: str, *, distance_km: float | None = None,
            duration_min: int | None = None, day: date | None = None,
            name: str | None = None, replaces: list[int] | None = None,
            today: date | None = None) -> dict:
    """Build and file a proposal. Raises `ValueError` with a sentence the
    caller can show ("10 km is too short for …"). `replaces` names calendar
    entries (schedule ids) that `apply` unschedules first."""
    from . import snapshot

    anchor = today or paths.today()
    zones = snapshot.assemble(store, today=anchor).get("zones") or {}
    spec = planning.build_session(kind, distance_km=distance_km, duration_min=duration_min,
                                  zones=zones, name=name)
    return _file([_item(day or anchor, spec)],
                 replaces=_calendar_entries(store, list(replaces or []), today=anchor),
                 assumptions=[], zones=zones)


def propose_week(store: Store, *, start: date | None = None, days_per_week: int | None = None,
                 long_run_day: str | None = None, today: date | None = None) -> dict:
    """A week as ONE proposal: up to six sessions under one id, applied after
    one yes. Profile values fill what the caller leaves out; what neither
    gives is a default and is listed as an assumption."""
    from . import snapshot

    anchor = today or paths.today()
    if start is None:
        start = anchor + timedelta(days=(7 - anchor.weekday()) % 7)   # next Monday, or today
    latest = anchor + timedelta(days=CALENDAR_WINDOW[1] - 6)
    if not anchor <= start <= latest:
        raise ValueError(f"start must be between today and {latest} - the app mirrors the "
                         f"calendar {CALENDAR_WINDOW[1]} days ahead")
    profile = snapshot.load_profile()
    assumptions: list[str] = []
    if days_per_week is None:
        days_per_week = profile.get("days_per_week")
        if days_per_week is None:
            days_per_week = planning.DEFAULT_DAYS_PER_WEEK
            assumptions.append(f"{days_per_week} running days - no days_per_week in the profile "
                               f"(runcoach profile --days-per-week N)")
    if long_run_day is None:
        long_run_day = profile.get("long_run_day")
        if long_run_day is None:
            long_run_day = planning.DEFAULT_LONG_RUN_DAY
            assumptions.append(f"long run on {long_run_day} - no long_run_day in the profile "
                               f"(runcoach profile --long-run-day sun)")
    zones = snapshot.assemble(store, today=anchor).get("zones") or {}
    sessions, notes = planning.build_week(start, days_per_week=int(days_per_week),
                                          long_run_day=str(long_run_day), zones=zones)
    busy = store.get_scheduled_workouts(start, start + timedelta(days=6))
    if busy:
        notes.append("the calendar already has " + "; ".join(
            f"{e['day']} \"{e['title'] or '?'}\"" for e in busy[:7])
            + " - this package ADDS to it")
    return _file([_item(d, s) for d, s in sessions], replaces=[],
                 assumptions=assumptions + notes, zones=zones)


def apply(store: Store, client, proposal_id: str, *, today: date | None = None) -> dict:
    """Upload, schedule, push, read back, verify - per session, in that order,
    each step recorded before the next, so a failure half-way leaves a
    truthful state rather than a workout on Garmin the app does not know it
    owns. A package is applied session by session; from the first upload on
    the proposal counts as applied (a second apply would upload twice), and
    what did not make it is a warning naming the day.

    Returns the updated proposal. Never raises for a Garmin-side failure: the
    text in `error` says what happened and what the athlete can do."""
    p = read(proposal_id)
    if p is None:
        ids = [x["id"] for x in open_proposals()]
        return {"id": proposal_id, "status": "unknown",
                "error": (f"no proposal {proposal_id}. Open proposals: "
                          + (", ".join(ids) if ids else "none - call propose_workout first"))}
    if p.get("status") != "open":
        done = [str(it.get("workout_id")) for it in p.get("items", []) if it.get("workout_id")]
        return {**p, "error": (f"already applied on {p.get('applied_at')}: workout(s) "
                               f"{', '.join(done) or '-'} from {p.get('day')}. "
                               f"Propose again for a new session.")}

    warnings: list[str] = []
    for r in p.get("replaces", []):
        try:
            garmin.unschedule(client, int(r["schedule_id"]))
        except Exception as exc:  # noqa: BLE001 — the old entry stays, say so
            warnings.append(f"could not remove \"{r.get('title')}\" from {r.get('day')} "
                            f"({type(exc).__name__}) - both are on the calendar now")

    for it in p["items"]:
        spec = planning.from_json(it["spec"])
        day = date.fromisoformat(it["day"])
        try:
            wid = garmin.upload_workout(client, spec)
        except Exception as exc:  # noqa: BLE001 — reported, the athlete decides
            if p.get("status") == "open":
                return {**p, "error": f"upload failed: {type(exc).__name__}: {exc}"}
            warnings.append(f"{day} \"{spec.name}\" NOT uploaded: {type(exc).__name__}: {exc}")
            continue
        store.record_workout(wid, name=spec.name, kind=spec.kind, spec_json=json.dumps(it["spec"]))
        it["workout_id"] = wid
        p.update(status="applied", applied_at=_now().isoformat(timespec="seconds"))
        _write_json(_path(proposal_id), p)   # ours from this moment, whatever follows

        try:
            sid = garmin.schedule(client, wid, day)
        except Exception as exc:  # noqa: BLE001
            sid = None
            warnings.append(f"\"{spec.name}\" uploaded but NOT scheduled: {type(exc).__name__}: "
                            f"{exc} - it is in your Garmin library, put it on {day} by hand")
        store.record_workout(wid, name=spec.name, kind=spec.kind, spec_json=json.dumps(it["spec"]),
                             schedule_id=sid, scheduled_day=day)
        it["schedule_id"] = sid
        try:
            garmin.push_to_device(client, wid)
        except Exception as exc:  # noqa: BLE001 — the watch may simply be off
            warnings.append(f"\"{spec.name}\" not pushed to the watch ({type(exc).__name__}) - it "
                            f"syncs from the calendar on the next connection")
        try:
            problems = garmin.verify(spec, garmin.read_back(client, wid))
        except Exception as exc:  # noqa: BLE001
            problems = [f"could not read the workout back: {type(exc).__name__}: {exc}"]
        warnings.extend(f"MISMATCH on Garmin, {day} \"{spec.name}\": {x}" for x in problems)

    # The Today tab reads the mirror; refresh the window so the new entries are
    # there now, not after the next sync.
    anchor = today or paths.today()
    start, end = anchor - timedelta(days=CALENDAR_WINDOW[0]), anchor + timedelta(days=CALENDAR_WINDOW[1])
    try:
        entries, complete = garmin.fetch_scheduled_workouts(client, start, end)
        if complete:
            store.replace_scheduled_workouts(entries, start, end)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"calendar mirror not refreshed ({type(exc).__name__}); ↻ or the "
                        f"next sync will show it")

    p.update(warnings=warnings)
    _write_json(_path(proposal_id), p)
    return p


def workouts_of(p: dict) -> list[int]:
    """The Garmin workout ids a proposal put there (empty while open)."""
    return [int(it["workout_id"]) for it in p.get("items", []) if it.get("workout_id")]


def describe_result(p: dict) -> str:
    """One paragraph for the model and the athlete."""
    if p.get("error"):
        return p["error"]
    warnings = p.get("warnings", [])
    lines = []
    for it in p.get("items", []):
        if not it.get("workout_id"):
            continue
        spec = planning.from_json(it["spec"])
        pushed = not any("not pushed" in w and spec.name in w for w in warnings)
        lines.append(f"On Garmin: \"{spec.name}\" (workout {it['workout_id']}) scheduled for "
                     f"{it['day']}" + (", pushed to the watch." if pushed else "."))
    for r in p.get("replaces", []):
        if not any("could not remove" in w and str(r.get("title")) in w for w in warnings):
            lines.append(f"Removed from the calendar: \"{r.get('title')}\" on {r.get('day')} "
                         f"(the workout stays in your library).")
    lines.extend(f"  ! {w}" for w in warnings)
    if not warnings:
        lines.append("  read back from Garmin and verified: structure and targets as proposed.")
    return "\n".join(lines)
