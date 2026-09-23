"""Proposals: a session the athlete has SEEN and can say yes to.

`propose` builds a session (pure, `planning.py`), writes it to
`~/.runcoach/proposals/<id>.json` and returns the preview. Nothing reaches
Garmin. `apply` is the one function in this codebase that changes the
athlete's Garmin account - upload, schedule, push to the watch - and it runs
only after a human said yes: in the app that is a click on the proposal card,
in a Claude Code session it is the athlete's answer in the conversation. The
coach's unattended card runs cannot call it at all (`web/agent.py` passes
`--disallowedTools` for it, and the child server refuses through
`RUNCOACH_UNATTENDED` - configuration twice, not a promise in a prompt).

A proposal is a PACKAGE: `items` holds one session ("intervals for my 10 km")
or a whole week (`propose_week`), applied after ONE yes. `replaces` names
calendar entries the apply removes first - the readiness swap, an easy run in
place of the hard session the calendar had.

Three rules, each of them learned from something that went wrong:

* Read back what was written and compare it with what was meant. An upload
  that "succeeded" once carried a heart-rate target on the recovery jog; the
  only way to know is to ask Garmin what it stored.
* The app deletes nothing it did not create. `runcoach_workouts` is the
  record of what it created. Replacing a calendar entry UNSCHEDULES it - the
  workout stays in the athlete's library.
* **What the summary claims is read from what happened, never from the text
  of a warning.** Every step writes a flag on its item; `describe_result`
  reads the flags. The version that matched substrings told an athlete his
  hard session had been taken off the calendar in the same breath as the
  warning saying it had not.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import garmin, paths, planning
from .store import Store
from .web.jobs import _read_json, _write_json

log = logging.getLogger(__name__)

#: A proposal nobody applied for this long is stale: the readiness it was
#: built on is gone, and so is the week it was meant for.
TTL_DAYS = 7
#: The same window the sync mirrors; a scheduled day outside it would be
#: invisible on the Today tab until the window moves.
CALENDAR_WINDOW = (7, 14)
#: Warnings kept on a proposal. A resume APPENDS to the ones already there -
#: a mismatch found on Monday must not disappear because Tuesday's retry went
#: through - so the list needs an end.
MAX_WARNINGS = 40

#: Exception names that prove Garmin created nothing: it refused the request,
#: or the connection never carried one. Anything else - above all a read
#: timeout, where the request landed and the answer did not - leaves the claim
#: in place, because a retry is what would put the session on the watch twice.
#: Matched on the exception's NAME, not its class: the vendor library defines
#: its own hierarchy (`GarminConnectConnectionError` and friends inherit from
#: plain `Exception`, not from the builtin `ConnectionError`), and `requests`
#: has a third. A name is the one thing all three agree on.
#: `InvalidFileFormat` is in here because a rejected workout is a decision the
#: server made about the request, not a lost answer - nothing was created.
_CREATED_NOTHING_HINTS = ("ConnectionRefused", "ConnectionError", "NameResolution",
                          "ConnectTimeout", "TooManyRequests", "Authentication",
                          "Unauthorized", "Forbidden", "SSLError", "InvalidFileFormat")


#: The shape of a proposal id, in ONE place. It guards three entrances - the
#: MCP tool's parameter, the field a card run may write, and the HTTP route the
#: click uses - and a shape that only mostly agrees across three copies is a
#: path-traversal check with a hole in it.
PROPOSAL_ID_PATTERN = r"^p-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$"
PROPOSAL_ID_RE = re.compile(PROPOSAL_ID_PATTERN)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _created_nothing(exc: BaseException) -> bool:
    name = type(exc).__name__
    if "ReadTimeout" in name or name == "Timeout" or isinstance(exc, TimeoutError):
        return False
    if isinstance(exc, ConnectionError):
        return True
    return any(h in name for h in _CREATED_NOTHING_HINTS)


def _path(proposal_id: str) -> Path:
    return paths.proposals_dir() / f"{proposal_id}.json"


def new_id() -> str:
    return f"p-{_now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def read(proposal_id: str, store: Store | None = None) -> dict | None:
    """The proposal as it stands. WITH a store, reconciled against the claim
    table first - and every surface that reports on proposals passes one.

    Without it, a process that died between recording an upload and saving the
    file leaves a proposal that still reads "open": the card offers to write a
    session that is already on Garmin, and `runcoach doctor` answers "0 applied
    only in part" about exactly the state it exists to find."""
    if not PROPOSAL_ID_RE.match(proposal_id):
        return None
    d = _read_json(_path(proposal_id))
    # A file that parses but is not a proposal is not a proposal. `_finish`
    # used to be able to write a stub, and one of those reaching the page made
    # every `/api/state` a 500 - the whole dashboard, not just that card.
    if not isinstance(d, dict) or not isinstance(d.get("items"), list) or not d["items"]:
        return None
    return _reconcile(store, d) if store is not None else d


def open_proposals(store: Store | None = None) -> list[dict]:
    """Newest first, unapplied, not expired - reconciled when a store is given,
    so one that was applied but never saved does not count as waiting."""
    cutoff = (_now() - timedelta(days=TTL_DAYS)).isoformat(timespec="seconds")
    out = []
    for f in sorted(paths.proposals_dir().glob("p-*.json"), reverse=True):
        d = read(f.stem, store)
        if d and d.get("status") == "open" and d.get("created", "") >= cutoff:
            out.append(d)
    return out


def cleanup() -> int:
    """Forget proposals older than the TTL - applied ones too: what they put on
    Garmin is recorded in `runcoach_workouts` and `proposal_items`, which are
    not swept. Only the preview and the card's button go."""
    cutoff = (_now() - timedelta(days=TTL_DAYS)).isoformat(timespec="seconds")
    n = 0
    for f in paths.proposals_dir().glob("p-*.json"):
        d = _read_json(f)
        if isinstance(d, dict) and d.get("created", "") < cutoff:
            f.unlink(missing_ok=True)
            n += 1
    return n


def _item(day: date, spec: planning.SessionSpec) -> dict:
    return {"day": day.isoformat(), "spec": planning.to_json(spec),
            "preview": planning.describe(spec),
            # Filled by `apply`, one per step of the write. `None` means "not
            # attempted"; these are what `describe_result` reports.
            "workout_id": None, "schedule_id": None, "pushed": None, "verified": None}


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


#: The claim index the CALENDAR step of a proposal takes, kept clear of the
#: session indices (0, 1, 2 …) so both can live in one table.
REPLACES_CLAIM_INDEX = -1
#: ...and the index space for "the SCHEDULING of session i", used when a resume
#: finishes a session that was uploaded but never got a day.
SCHEDULE_CLAIM_BASE = -1000

#: Per-item facts `apply` learns and `describe_result` reports. Merged onto the
#: file at the end, so a concurrent apply cannot erase them.
_ITEM_FACTS = ("workout_id", "schedule_id", "pushed", "verified")


def _reconcile(store: Store, p: dict) -> dict:
    """Fill the proposal from the claim table, which is what actually reached
    Garmin. A proposal with an uploaded session is `applied`, whatever the file
    says - two applies writing the same file can otherwise lose each other's
    ids and leave a card offering to write a session that is already there."""
    claimed = store.proposal_items(p["id"])
    scheduled = {w["workout_id"]: w["schedule_id"] for w in store.own_workouts()}
    for idx, it in enumerate(p["items"]):
        if claimed.get(idx) and not it.get("workout_id"):
            it["workout_id"] = claimed[idx]
        # In BOTH directions for a workout the database knows: after an undo
        # the day is gone, and a merge that only ever fills in a missing value
        # would hand the stale one back from the file.
        if it.get("workout_id") in scheduled:
            it["schedule_id"] = scheduled[it["workout_id"]]
    if any(it.get("workout_id") for it in p["items"]):
        # "applied" = this proposal has touched Garmin, which is what decides
        # whether a second apply may upload. Whether every session also got a
        # day is `pending_of`, and the card reads both.
        p["status"] = "applied"
    return p


def _finish(store: Store, proposal_id: str, local: dict, warnings: list[str]) -> dict:
    """Save the outcome on top of whatever the file holds NOW.

    Not the copy this call started with: a second apply running at the same
    time may have written its own ids in between, and blindly saving a stale
    copy over them was how the loser of that race erased the winner's work. So
    the file is re-read, the facts THIS call learned are merged in, and the
    warnings are appended to the ones already recorded - a mismatch found on
    Monday must survive Tuesday's retry.

    The merge is itself read-modify-write, so two applies running at the same
    instant can still lose one WARNING to each other. They cannot lose a
    workout id: that comes from the claim table, which is where the truth about
    Garmin lives. A note is worth a file, an id is not."""
    fresh = read(proposal_id)
    if fresh is None:            # cleaned up or deleted mid-apply: do not recreate it
        return {**local, "warnings": warnings}
    _reconcile(store, fresh)
    for idx, it in enumerate(fresh["items"]):
        if idx < len(local.get("items", [])):
            for key in _ITEM_FACTS:
                if it.get(key) is None and local["items"][idx].get(key) is not None:
                    it[key] = local["items"][idx][key]
    for i, r in enumerate(fresh.get("replaces", [])):
        if r.get("removed") is None and i < len(local.get("replaces", [])):
            r["removed"] = local["replaces"][i].get("removed")
    seen, merged = set(), []
    for w in list(fresh.get("warnings") or []) + list(warnings):
        if w not in seen:
            seen.add(w)
            merged.append(w)
    fresh["warnings"] = merged[-MAX_WARNINGS:]
    if fresh.get("status") == "applied" and not fresh.get("applied_at"):
        fresh["applied_at"] = _now().isoformat(timespec="seconds")
    _write_json(_path(proposal_id), fresh)
    return fresh


def _scheduled_workout_id(store: Store, schedule_id: int, *, today: date) -> int | None:
    """What the mirror currently has behind a calendar id, `None` if it is gone.

    A proposal may sit for days before anyone says yes, and `replaces` was
    checked when it was written. Garmin hands out calendar ids again, so
    unscheduling one on trust is how the app would delete an entry the athlete
    made in the meantime."""
    lo = today - timedelta(days=CALENDAR_WINDOW[0])
    for e in store.get_scheduled_workouts(lo, today + timedelta(days=CALENDAR_WINDOW[1])):
        if int(e["schedule_id"]) == int(schedule_id):
            return e.get("workout_id")
    return None


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
                    "title": e.get("title"), "day": e.get("day"), "removed": None})
    return out


def spacing_notes(store: Store, day: date, kind: str, *, today: date) -> list[str]:
    """What the calendar and the training record say against putting THIS kind
    on THIS day. Advisory, never a refusal - the athlete decides.

    `build_week` enforces 48 hours between hard sessions and keeps the day
    before the long run clear, in code. The single moved session went through
    `propose_workout`, where the same two rules existed only as a sentence in
    the coach's prompt asking it to do the arithmetic itself - one of two paths
    carrying the same promise, and the one a red morning actually uses. These
    notes ride along with the proposal, so the preview says it whatever the
    model worked out."""
    if kind not in planning.HARD_KINDS:
        return []
    notes: list[str] = []
    # The app's OWN decision for the day, not the model's reading of it. The
    # template tells the coach to file the lighter session on a red day; that
    # is a sentence in a prompt, and the guarantee it carries is weaker than
    # the one two lines below - which is the same promise, annotated in code.
    # So this one is annotated too: the preview says what the day decided,
    # whatever the coach concluded.
    if day == today:
        from . import tools

        decision = tools._decide(store, today)
        if decision.get("decision") in ("easy", "rest"):
            notes.append(f"the app's decision for today is {decision['decision'].upper()} - "
                         f"\"{decision.get('sentence', '')}\"")
    last_hard = store.last_hard_day(today)
    if last_hard is not None and (day - last_hard).days < 2:
        notes.append(f"only {(day - last_hard).days} day(s) after your last hard session "
                     f"({last_hard}) - hard stimuli sit 48 hours apart")
    own = {w["workout_id"]: w["kind"] for w in store.own_workouts() if w["workout_id"]}
    for e in store.get_scheduled_workouts(day + timedelta(days=1), day + timedelta(days=1)):
        recorded = own.get(e.get("workout_id"))
        if recorded == "long":
            notes.append(f"the day before your long run on {e['day']} - that is the week's "
                         f"other hard session")
        elif recorded is None and "long" in str(e.get("title") or "").lower():
            notes.append(f"the day before \"{e['title']}\" on {e['day']}, which looks like a "
                         f"long run - judged from its name, so check it")
    return notes


def _check_day(day: date, *, today: date) -> None:
    """A session is only worth writing where the athlete will see it: not in the
    past, and inside the window the app mirrors - beyond it the entry exists on
    Garmin but never appears on the Today tab."""
    if day < today:
        raise ValueError(f"{day} is in the past - a workout cannot be scheduled backwards")
    latest = today + timedelta(days=CALENDAR_WINDOW[1])
    if day > latest:
        raise ValueError(f"{day} is beyond {latest}, the last day this app mirrors from the "
                         f"Garmin calendar - plan it closer to the day")


def propose(store: Store, kind: str, *, distance_km: float | None = None,
            duration_min: int | None = None, day: date | None = None,
            name: str | None = None, replaces: list[int] | None = None,
            today: date | None = None) -> dict:
    """Build and file a proposal. Raises `ValueError` with a sentence the
    caller can show ("10 km is too short for …"). `replaces` names calendar
    entries (schedule ids) that `apply` unschedules first."""
    from . import snapshot

    anchor = today or paths.today()
    _check_day(day or anchor, today=anchor)
    zones = snapshot.assemble(store, today=anchor).get("zones") or {}
    spec = planning.build_session(kind, distance_km=distance_km, duration_min=duration_min,
                                  zones=zones, name=name)
    target = day or anchor
    return _file([_item(target, spec)],
                 replaces=_calendar_entries(store, list(replaces or []), today=anchor),
                 assumptions=spacing_notes(store, target, kind, today=anchor), zones=zones)


def propose_week(store: Store, *, start: date | None = None, days_per_week: int | None = None,
                 long_run_day: str | None = None, today: date | None = None) -> dict:
    """A week as ONE proposal: up to six sessions under one id, applied after
    one yes. Profile values fill what the caller leaves out; what neither
    gives is a default and is listed as an assumption."""
    from . import snapshot

    anchor = today or paths.today()
    if start is None:
        start = anchor + timedelta(days=(7 - anchor.weekday()) % 7)   # next Monday, or today
    _check_day(start, today=anchor)
    _check_day(start + timedelta(days=6), today=anchor)
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
    budget, basis = _week_budget(store, anchor)
    assumptions.append(basis)
    sessions, notes = planning.build_week(start, days_per_week=int(days_per_week),
                                          long_run_day=str(long_run_day), zones=zones,
                                          total_minutes=budget)
    busy = store.get_scheduled_workouts(start, start + timedelta(days=6))
    if busy:
        notes.append("the calendar already has " + "; ".join(
            f"{e['day']} \"{e['title'] or '?'}\"" for e in busy[:7])
            + " - this package ADDS to it")
    return _file([_item(d, s) for d, s in sessions], replaces=[],
                 assumptions=assumptions + notes, zones=zones)


def _week_budget(store: Store, today: date) -> tuple[int | None, str]:
    """How many running minutes this week may hold, from the weeks the athlete
    actually ran - and the sentence that says so.

    The planner used to hand every athlete the same week (50/55/45/90 min).
    For someone running 90 minutes a week that is a 160 % jump in one step,
    and the size of the jump is the best-evidenced injury factor there is. So
    the week is sized from the last four completed weeks; the 10 % step on top
    is a CONVENTION, not a finding (`zones.md` says so), and it is named as
    one. Without history there is nothing to scale to, and the defaults are
    used with that said out loud."""
    monday = today - timedelta(days=today.weekday())
    weeks = store.get_weekly_volume(monday - timedelta(weeks=4), monday - timedelta(days=1))
    minutes = sorted(int((w.get("duration_s") or 0) // 60) for w in (weeks or [])
                     if (w.get("duration_s") or 0) > 0)
    if len(minutes) < 2:
        return None, ("week sized from the built-in defaults - fewer than two completed weeks "
                      "of running on record to scale to")
    median = minutes[len(minutes) // 2] if len(minutes) % 2 else \
        (minutes[len(minutes) // 2 - 1] + minutes[len(minutes) // 2]) // 2
    budget = int(round(median * 1.10))
    # The weeks themselves, not only their median: a planned down week or a
    # taper is in there and nothing here can tell it from a normal week, so
    # the athlete gets the basis to judge rather than a number to trust.
    return budget, (f"week sized at ~{budget} min from your last {len(minutes)} weeks "
                    f"({', '.join(str(m) for m in minutes)} min, median {median}) plus 10 % - "
                    f"the 10 % step is a convention, not a finding, and a down week or taper "
                    f"among those weeks pulls it down with them")


def apply(store: Store, client, proposal_id: str, *, today: date | None = None) -> dict:
    """Upload, schedule, push, read back, verify - per session, in that order,
    each step recorded before the next, so a failure half-way leaves a
    truthful state rather than a workout on Garmin the app does not know it
    owns. A package is applied session by session and can be applied again to
    finish what is still missing; what did not make it is a warning naming the
    day, and the summary never claims a step that did not happen.

    Returns the updated proposal. Never raises for a Garmin-side failure: the
    text in `error` says what happened and what the athlete can do."""
    anchor = today or paths.today()
    store.reap_stale_claims()
    p = read(proposal_id)
    if p is None:
        ids = [x["id"] for x in open_proposals(store)]
        return {"id": proposal_id, "status": "unknown",
                "error": (f"no proposal {proposal_id}. Open proposals: "
                          + (", ".join(ids) if ids else "none - call propose_workout first"))}

    # The CLAIMS are the truth about what reached Garmin, not the file: two
    # applies writing the same file can lose each other's ids, and the file is
    # also what a stale reader saw. Reconcile before deciding anything.
    _reconcile(store, p)
    pending = [i for i, it in enumerate(p["items"]) if not _is_placed(it)]
    if p.get("status") != "open" and not pending:
        done = [str(it["workout_id"]) for it in p["items"] if it.get("workout_id")]
        return {**p, "error": (f"already applied on {p.get('applied_at')}: workout(s) "
                               f"{', '.join(done) or '-'} from {p.get('day')}. "
                               f"Propose again for a new session.")}

    warnings: list[str] = []
    # Each entry is handled ONCE, ever - `removed` records the outcome and is
    # saved with the proposal. A resume that unscheduled again would aim a
    # delete at a calendar id Garmin may have given to something else.
    #
    # And once GLOBALLY, not once per process: the calendar step is claimed the
    # same way a session is, under a reserved index, so two applies racing each
    # other (a click plus a chat, or an impatient second click after the
    # browser's timeout) cannot both send the unschedule. Without it the second
    # one deleted whatever now held that id and then overwrote `removed`.
    todo = [r for r in p.get("replaces", []) if r.get("removed") is None]
    if todo and not store.claim_proposal_item(proposal_id, REPLACES_CLAIM_INDEX):
        warnings.append("the calendar entries of this proposal are being handled by another "
                        "apply - nothing was removed here")
        todo = []
    for r in todo:
        sid, wid_was = int(r["schedule_id"]), r.get("workout_id")
        now_is = _scheduled_workout_id(store, sid, today=anchor)
        # POSITIVE check: touch it only while the mirror still shows the entry
        # the athlete agreed to replace. Unknown is not permission.
        if now_is is None or wid_was is None or now_is != wid_was:
            r["removed"] = False
            warnings.append(
                f"did NOT remove schedule {sid} (\"{r.get('title')}\"): the calendar no longer "
                f"shows the entry the proposal was built on - remove it yourself if you still "
                f"want it gone")
            continue
        try:
            garmin.unschedule(client, sid)
            r["removed"] = True
            log.info("unscheduled %s (workout %s) for proposal %s", sid, wid_was, proposal_id)
        except Exception as exc:  # noqa: BLE001 — the old entry stays, say so
            r["removed"] = False
            warnings.append(f"could not remove \"{r.get('title')}\" from {r.get('day')} "
                            f"({type(exc).__name__}) - both are on the calendar now")
    # Saved BEFORE the first upload: an unschedule that happened must not be
    # forgotten because the upload after it failed. Through `_finish`, not a
    # raw dump - a concurrent apply may have recorded something in between.
    if todo:
        p = _finish(store, proposal_id, p, warnings)

    failed_first: str | None = None
    for idx in pending:
        it = p["items"][idx]
        spec = planning.from_json(it["spec"])
        day = date.fromisoformat(it["day"])
        if it.get("workout_id"):
            # Uploaded on an earlier run, never given a day. Finish THAT step
            # instead of uploading the session a second time.
            _place(store, client, proposal_id, idx, it, spec, day, warnings)
            continue
        if not store.claim_proposal_item(proposal_id, idx):
            warnings.append(f"{day} \"{spec.name}\" skipped: another apply of this proposal "
                            f"is already handling it")
            continue
        try:
            wid = garmin.upload_workout(client, spec)
        except Exception as exc:  # noqa: BLE001 — reported, the athlete decides
            if _created_nothing(exc):
                store.release_proposal_item(proposal_id, idx)
                note = "apply the proposal again to retry just this one"
            else:
                store.mark_proposal_item_unknown(proposal_id, idx)
                note = (f"it is unclear whether Garmin created it - look for \"{spec.name}\" in "
                        f"your Garmin library before proposing it again")
            log.warning("upload failed for proposal %s item %s: %r", proposal_id, idx, exc)
            if not failed_first and idx == pending[0]:
                failed_first = f"upload failed: {type(exc).__name__}: {exc}"
            warnings.append(f"{day} \"{spec.name}\" NOT uploaded: {type(exc).__name__}: {exc} - "
                            f"{note}")
            continue
        store.record_proposal_item(proposal_id, idx, wid)
        store.record_workout(wid, name=spec.name, kind=spec.kind, spec_json=json.dumps(it["spec"]))
        it["workout_id"] = wid
        p["status"] = "applied"
        # Ours from this moment, whatever follows - saved through the SAME
        # merging path as the final write. A plain dump of the local copy here
        # erased what a concurrent apply had just recorded, which is the bug
        # `_finish` exists to prevent, reintroduced four lines further down.
        p = _finish(store, proposal_id, p, warnings)
        it = p["items"][idx]
        log.info("uploaded workout %s (\"%s\") for proposal %s", wid, spec.name, proposal_id)

        _place(store, client, proposal_id, idx, it, spec, day, warnings)

    # The Today tab reads the mirror; refresh the window so the new entries are
    # there now, not after the next sync.
    start, end = anchor - timedelta(days=CALENDAR_WINDOW[0]), anchor + timedelta(days=CALENDAR_WINDOW[1])
    try:
        entries, complete = garmin.fetch_scheduled_workouts(client, start, end)
        if complete:
            store.replace_scheduled_workouts(entries, start, end)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"calendar mirror not refreshed ({type(exc).__name__}); ↻ or the "
                        f"next sync will show it")

    done = _finish(store, proposal_id, p, warnings)
    return {**done, "error": failed_first} if failed_first else done


def _place(store: Store, client, proposal_id: str, idx: int, it: dict,
           spec, day: date, warnings: list[str]) -> None:
    """Give an uploaded session a day, push it, read it back. Shared by the
    first apply and by a resume that found the upload done and the rest not."""
    wid = int(it["workout_id"])
    # The scheduling has its own claim, so two resumes cannot both put the same
    # workout on the calendar twice.
    if not store.claim_proposal_item(proposal_id, SCHEDULE_CLAIM_BASE - idx):
        warnings.append(f"{day} \"{spec.name}\": another apply is scheduling it")
        return
    try:
        sid = garmin.schedule(client, wid, day)
        if sid is None:
            warnings.append(f"\"{spec.name}\" uploaded, but Garmin returned no calendar id - "
                            f"check whether it really sits on {day}")
    except Exception as exc:  # noqa: BLE001
        sid = None
        warnings.append(f"\"{spec.name}\" uploaded but NOT scheduled: {type(exc).__name__}: "
                        f"{exc} - apply the proposal again to give it a day")
    if sid is None:
        # Nothing was placed, so the next apply must be allowed to try again.
        store.release_proposal_item(proposal_id, SCHEDULE_CLAIM_BASE - idx)
    else:
        store.record_proposal_item(proposal_id, SCHEDULE_CLAIM_BASE - idx, wid)
    store.record_workout(wid, name=spec.name, kind=spec.kind, spec_json=json.dumps(it["spec"]),
                         schedule_id=sid, scheduled_day=day)
    it["schedule_id"] = sid
    log.info("scheduled workout %s on %s as %s", wid, day, sid)
    try:
        garmin.push_to_device(client, wid)
        it["pushed"] = True
    except Exception as exc:  # noqa: BLE001 — the watch may simply be off
        it["pushed"] = False
        warnings.append(f"\"{spec.name}\" not pushed to the watch ({type(exc).__name__}) - it "
                        f"syncs from the calendar on the next connection")
    try:
        problems = garmin.verify(spec, garmin.read_back(client, wid))
    except Exception as exc:  # noqa: BLE001
        problems = [f"could not read the workout back: {type(exc).__name__}: {exc}"]
    it["verified"] = not problems
    warnings.extend(f"MISMATCH on Garmin, {day} \"{spec.name}\": {x}" for x in problems)


def undo(store: Store, client, proposal_id: str, *, today: date | None = None) -> dict:
    """Take an applied proposal back off the calendar.

    The counterpart the write path was missing: a session could go onto the
    watch and never come off it again, which makes the first real write a
    one-way door - and a one-way door is the reason a first real write does
    not get made. It UNSCHEDULES; the workout stays in the athlete's Garmin
    library, because deleting is a door of its own and v1 does not open it.

    Only sessions THIS proposal placed, and only those this app uploaded -
    `runcoach_workouts` is the record, and a calendar id that no longer holds
    our workout belongs to something else by now."""
    anchor = today or paths.today()
    p = read(proposal_id, store)
    if p is None:
        return {"id": proposal_id, "status": "unknown",
                "error": f"no proposal {proposal_id} - nothing to take back"}
    placed = [it for it in p["items"] if it.get("schedule_id")]
    if not placed:
        return {**p, "error": ("nothing of this proposal is on the calendar"
                               + (" any more" if p.get("status") == "applied" else ""))}

    ours = {w["workout_id"] for w in store.own_workouts() if w["workout_id"]}
    warnings: list[str] = []
    removed = 0
    for it in placed:
        sid, wid = int(it["schedule_id"]), it.get("workout_id")
        if wid not in ours:
            warnings.append(f"{it['day']}: workout {wid} is not one this app uploaded - left alone")
            continue
        now_is = _scheduled_workout_id(store, sid, today=anchor)
        if now_is is not None and now_is != wid:
            warnings.append(f"{it['day']}: schedule {sid} no longer holds workout {wid} - "
                            f"left alone, the calendar changed")
            continue
        try:
            garmin.unschedule(client, sid)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{it['day']}: could not take it off ({type(exc).__name__}: {exc})")
            continue
        log.info("unscheduled %s (workout %s) undoing proposal %s", sid, wid, proposal_id)
        it["schedule_id"] = None
        it["pushed"] = None
        store.record_workout(wid, name=planning.from_json(it["spec"]).name,
                             kind=it["spec"]["kind"], spec_json=json.dumps(it["spec"]),
                             schedule_id=None, scheduled_day=None)
        # The scheduling claim goes with it, so the proposal can be applied
        # again later without the claim table refusing the day it just freed.
        store.release_schedule_claim(proposal_id, p["items"].index(it))
        removed += 1

    start, end = anchor - timedelta(days=CALENDAR_WINDOW[0]), anchor + timedelta(days=CALENDAR_WINDOW[1])
    try:
        entries, complete = garmin.fetch_scheduled_workouts(client, start, end)
        if complete:
            store.replace_scheduled_workouts(entries, start, end)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"calendar mirror not refreshed ({type(exc).__name__})")

    out = _finish(store, proposal_id, p, warnings)
    out["undone"] = removed
    return out


def describe_undo(p: dict) -> str:
    if p.get("error") and not p.get("undone"):
        return p["error"]
    n = p.get("undone", 0)
    lines = [f"Taken off the calendar: {n} session(s). The workout(s) stay in your Garmin "
             f"library, so applying this proposal again puts them back on a day."
             if n else "Nothing was taken off the calendar."]
    lines.extend(f"  ! {w}" for w in p.get("warnings", []))
    return "\n".join(lines)


def workouts_of(p: dict) -> list[int]:
    """The Garmin workout ids a proposal put there (empty while open)."""
    return [int(it["workout_id"]) for it in p.get("items", []) if it.get("workout_id")]


def _is_placed(it: dict) -> bool:
    """A session counts as done only once it is BOTH uploaded and on a day.

    Uploaded alone is a workout in the athlete's library that no watch will
    ever show. Counting it as finished put the green "On Garmin" pill on a
    card for a session that never reached the calendar - the athlete could
    only learn the truth from `runcoach doctor`, not from the app."""
    return bool(it.get("workout_id")) and bool(it.get("schedule_id"))


def pending_of(p: dict) -> int:
    """Sessions of this proposal that are not yet uploaded AND scheduled."""
    return sum(1 for it in p.get("items", []) if not _is_placed(it))


def describe_result(p: dict) -> str:
    """One paragraph for the model and the athlete, read from the FLAGS each
    step wrote - never from the wording of a warning."""
    warnings = p.get("warnings", [])
    lines = []
    for it in p.get("items", []):
        if not it.get("workout_id"):
            continue
        spec = planning.from_json(it["spec"])
        if it.get("schedule_id"):
            where = f"scheduled for {it['day']}"
            tail = (", pushed to the watch." if it.get("pushed")
                    else ". Not pushed to the watch - it syncs from the calendar.")
        else:
            where = f"in your Garmin library, NOT scheduled for {it['day']}"
            tail = "."
        lines.append(f"On Garmin: \"{spec.name}\" (workout {it['workout_id']}) {where}{tail}")
    for r in p.get("replaces", []):
        if r.get("removed"):
            lines.append(f"Removed from the calendar: \"{r.get('title')}\" on {r.get('day')} "
                         f"(the workout stays in your library).")
    still = pending_of(p)
    if still and p.get("status") == "applied":
        lines.append(f"Still NOT on Garmin: {still} session(s) - apply this proposal again to "
                     f"add them; what is already there will not be written twice.")
    lines.extend(f"  ! {w}" for w in warnings)
    applied = [it for it in p.get("items", []) if it.get("workout_id")]
    if applied and not warnings and all(it.get("verified") for it in applied):
        lines.append("  read back from Garmin and verified: structure and targets as proposed.")
    if p.get("error"):
        lines.append(p["error"] if not lines else f"  ! {p['error']}")
    return "\n".join(lines) or (p.get("error") or "nothing to report")
