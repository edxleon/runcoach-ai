"""Proposals: a session the athlete has SEEN and can say yes to.

`propose` builds a session (pure, `planning.py`), writes it to
`~/.runcoach/proposals/<id>.json` and returns the preview. Nothing reaches
Garmin. `apply` is the one function in this codebase that changes the
athlete's Garmin account - upload, schedule, push to the watch - and it runs
only after a human said yes: in the app that is a click on the proposal card,
in a Claude Code session it is the athlete's answer in the conversation. The
coach's unattended card runs cannot call it at all (`web/agent.py` passes
`--disallowedTools` for it - configuration, not a promise in a prompt).

Two rules carried over from the cockpit this was extracted from:

* Read back what was written and compare it with what was meant. An upload
  that "succeeded" once carried a heart-rate target on the recovery jog; the
  only way to know is to ask Garmin what it stored.
* The app deletes nothing it did not create. `runcoach_workouts` is the
  record of what it created.
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


def propose(store: Store, kind: str, *, distance_km: float | None = None,
            duration_min: int | None = None, day: date | None = None,
            name: str | None = None, today: date | None = None) -> dict:
    """Build and file a proposal. Raises `ValueError` with a sentence the
    caller can show ("10 km is too short for …")."""
    from . import snapshot

    zones = snapshot.assemble(store, today=today).get("zones") or {}
    spec = planning.build_session(kind, distance_km=distance_km, duration_min=duration_min,
                                  zones=zones, name=name)
    target_day = day or today or paths.today()
    proposal = {
        "id": new_id(),
        "created": _now().isoformat(timespec="seconds"),
        "day": target_day.isoformat(),
        "spec": planning.to_json(spec),
        "preview": planning.describe(spec),
        "zones_as_of": zones.get("as_of_day"),
        "status": "open",
        "workout_id": None, "schedule_id": None, "applied_at": None, "warnings": [],
    }
    cleanup()
    _write_json(_path(proposal["id"]), proposal)
    return proposal


def apply(store: Store, client, proposal_id: str, *, today: date | None = None) -> dict:
    """Upload, schedule, push, read back, verify - in that order, each step
    recorded before the next, so a failure half-way leaves a truthful state
    rather than a workout on Garmin the app does not know it owns.

    Returns the updated proposal. Never raises for a Garmin-side failure: the
    text in `error` says what happened and what the athlete can do."""
    p = read(proposal_id)
    if p is None:
        ids = [x["id"] for x in open_proposals()]
        return {"id": proposal_id, "status": "unknown",
                "error": (f"no proposal {proposal_id}. Open proposals: "
                          + (", ".join(ids) if ids else "none - call propose_workout first"))}
    if p.get("status") != "open":
        return {**p, "error": (f"already applied on {p.get('applied_at')}: workout "
                               f"{p.get('workout_id')} scheduled for {p.get('day')}. "
                               f"Propose again for a new session.")}

    spec = planning.from_json(p["spec"])
    day = date.fromisoformat(p["day"])
    try:
        wid = garmin.upload_workout(client, spec)
    except Exception as exc:  # noqa: BLE001 — reported, the athlete decides
        return {**p, "error": f"upload failed: {type(exc).__name__}: {exc}"}
    store.record_workout(wid, name=spec.name, kind=spec.kind, spec_json=json.dumps(p["spec"]))
    p.update(workout_id=wid)
    _write_json(_path(proposal_id), p)   # ours from this moment, whatever follows

    warnings: list[str] = []
    try:
        sid = garmin.schedule(client, wid, day)
    except Exception as exc:  # noqa: BLE001
        sid = None
        warnings.append(f"uploaded but NOT scheduled: {type(exc).__name__}: {exc} - "
                        f"the workout is in your Garmin library, put it on {day} by hand")
    store.record_workout(wid, name=spec.name, kind=spec.kind, spec_json=json.dumps(p["spec"]),
                         schedule_id=sid, scheduled_day=day)
    try:
        garmin.push_to_device(client, wid)
    except Exception as exc:  # noqa: BLE001 — the watch may simply be off
        warnings.append(f"not pushed to the watch ({type(exc).__name__}) - it syncs from the "
                        f"calendar on the next connection")
    try:
        problems = garmin.verify(spec, garmin.read_back(client, wid))
    except Exception as exc:  # noqa: BLE001
        problems = [f"could not read the workout back: {type(exc).__name__}: {exc}"]
    warnings.extend(f"MISMATCH on Garmin: {x}" for x in problems)

    # The Today tab reads the mirror; refresh the window so the new entry is
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

    p.update(status="applied", schedule_id=sid,
             applied_at=_now().isoformat(timespec="seconds"), warnings=warnings)
    _write_json(_path(proposal_id), p)
    return p


def describe_result(p: dict) -> str:
    """One paragraph for the model and the athlete."""
    if p.get("error"):
        return p["error"]
    spec = planning.from_json(p["spec"])
    lines = [f"On Garmin: \"{spec.name}\" (workout {p['workout_id']}) scheduled for {p['day']}"
             + (", pushed to the watch." if not any("not pushed" in w for w in p.get("warnings", []))
                else ".")]
    lines.extend(f"  ! {w}" for w in p.get("warnings", []))
    if not p.get("warnings"):
        lines.append("  read back from Garmin and verified: structure and targets as proposed.")
    return "\n".join(lines)
