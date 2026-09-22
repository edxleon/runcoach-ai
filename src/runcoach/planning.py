"""Structured sessions as pure data: warm-up, work, recovery, cool-down.

The plan is CODE, the coach chooses. `build_session` turns (kind, distance or
duration, the athlete's zones) into a step tree with explicit targets; the
agent decides which kind fits today and explains it, exactly as it does with
the readiness verdict. Nothing here touches Garmin - `garmin.to_running_workout`
converts a spec to the vendor DTO, and only `plan.apply` ever sends it.

Two rules from the cockpit this app was extracted from, learned the hard way:

* A recovery step never carries a target. The community tooling set the work
  zone on the jog as well and drove the athlete into zone 5 between reps.
* "Same route" means the WARM-UP and COOL-DOWN absorb the arithmetic: the reps
  are prescribed by the stimulus (minutes at intensity), the remaining distance
  is what the athlete jogs before and after. A 4 min rep at 4:45/km plus a
  2:30 jog covers about 1.2 km; a 10 km route with 4 of those leaves ~5 km to
  split around them.

Every number that is not measured is an ASSUMPTION and is listed as one, so
the coach can say so instead of presenting it as fact.

That rule used to cover only the athlete's MISSING measurements, never the
constants below - the rep menus, the pace factors, the default week. They are
graded in `skills/zones.md`'s source table like everything else the app
believes, and the honest grade for the default week is "one athlete's week,
the author's": `_week_sizes` scales it to the athlete's own last four weeks
before anyone else sees it, and says so in the proposal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: What a session is for. `steady` exists because Garmin/Firstbeat only
#: re-measures VO2max from >= ~10 min of even effort near threshold; a spiky
#: interval session leaves the old estimate in place.
KINDS = ("easy", "long", "threshold", "vo2max", "steady")

#: Fallback when the store has no measured pace. Stated, never silent. Every
#: other pace in here is derived from it, so there is one number to be wrong.
DEFAULT_LT_PACE_S = 300          # 5:00/km
MIN_WARMUP_M = 1000
MIN_COOLDOWN_M = 800
#: Floors for a timed session. Warm-up and cool-down absorb whatever the
#: requested duration leaves over, but never shrink below these.
MIN_WARMUP_S = 480
MIN_COOLDOWN_S = 300
#: The shortest block Garmin/Firstbeat will re-measure VO2max from.
STEADY_MIN_S = 12 * 60
#: How much faster than threshold pace a VO2max rep runs (~3-5k race pace).
VO2MAX_PACE_FACTOR = 0.93
#: How much slower than threshold pace the recovery jog is.
RECOVERY_PACE_FACTOR = 1.30

#: (reps, seconds) options per kind, longest first, with the recovery jog that
#: goes between them. Reps shrink before rep length does: four short reps are
#: not a VO2max session. Shared by the distance fit and the duration fit, so a
#: 10 km route and a 50 min budget cannot disagree about what a session is.
REP_MENU = {
    "vo2max": ([(5, 240), (4, 240), (4, 180), (3, 240), (3, 180)], 150),
    "threshold": ([(4, 480), (3, 600), (3, 480), (2, 600), (2, 480)], 120),
}


@dataclass(slots=True)
class Step:
    """One executable step. Exactly one of `seconds`/`meters` is set."""

    kind: str                      # warmup | interval | recovery | cooldown
    seconds: int | None = None
    meters: int | None = None
    target: dict | None = None     # see `hr_zone` / `hr_bpm` / `pace` below
    note: str | None = None


@dataclass(slots=True)
class Repeat:
    iterations: int
    steps: list[Step]


@dataclass(slots=True)
class SessionSpec:
    kind: str
    name: str
    blocks: list                   # Step | Repeat, in order
    estimated_seconds: int
    estimated_meters: int
    assumptions: list[str] = field(default_factory=list)

    def flat_steps(self) -> list[Step]:
        out: list[Step] = []
        for b in self.blocks:
            if isinstance(b, Repeat):
                out.extend(b.steps)
            else:
                out.append(b)
        return out


# ── targets ──────────────────────────────────────────────────────────────────

def hr_zone(zone: int) -> dict:
    return {"type": "hr_zone", "zone": int(zone)}


def hr_bpm(lo: int, hi: int) -> dict:
    return {"type": "hr_bpm", "lo": int(lo), "hi": int(hi)}


def pace(lo_s_per_km: int, hi_s_per_km: int) -> dict:
    """`lo` is the SLOWER bound (more seconds), `hi` the faster one."""
    return {"type": "pace", "lo_s_per_km": int(lo_s_per_km), "hi_s_per_km": int(hi_s_per_km)}


def easy_cap(zones: dict | None) -> tuple[dict | None, str | None]:
    """The HR band an easy run is held in. Derived from the athlete's own
    bounds (~0.83 x the zone-5 floor, or ~0.85 x LTHR), never from an age
    formula. Returns `(target, assumption)`."""
    z = zones or {}
    if z.get("z5_low"):
        hi = round(0.83 * z["z5_low"])
        return hr_bpm(hi - 18, hi), None
    if z.get("lthr_bpm"):
        hi = round(0.85 * z["lthr_bpm"])
        return hr_bpm(hi - 18, hi), "easy cap derived from LTHR (0.85 x), no zone bounds on record"
    return None, ("no HR bounds on record - easy run carries no HR cap; "
                  "a first hard run gives Garmin the zones")


# ── the builder ──────────────────────────────────────────────────────────────

def _paces(zones: dict | None) -> tuple[int, int, int, list[str]]:
    """(lt, vo2max, easy) in s/km, plus the assumptions made."""
    z = zones or {}
    notes: list[str] = []
    lt = z.get("lt_pace_s_per_km")
    if not lt:
        lt = DEFAULT_LT_PACE_S
        easy = int(round(lt * RECOVERY_PACE_FACTOR))
        notes.append(f"no measured threshold pace - threshold estimated at "
                     f"{lt // 60}:{lt % 60:02d}/km, easy at {easy // 60}:{easy % 60:02d}/km")
    return int(lt), int(round(lt * VO2MAX_PACE_FACTOR)), int(round(lt * RECOVERY_PACE_FACTOR)), notes


def _fit_reps(kind: str, distance_m: int, lt: int, fast: int, jog: int) -> tuple[int, int, int]:
    """(reps, work_seconds, recovery_seconds) whose distance leaves room for a
    warm-up and cool-down on this route. Reps shrink before rep length does:
    the stimulus per rep is the point, four short reps are not a VO2max session."""
    menu, rec = REP_MENU[kind]
    work_pace = fast if kind == "vo2max" else lt
    budget = distance_m - MIN_WARMUP_M - MIN_COOLDOWN_M
    for reps, secs in menu:
        need = reps * (secs * 1000 / work_pace) + reps * (rec * 1000 / jog)
        if need <= budget:
            return reps, secs, rec
    # The smallest structure INCLUDING its recovery jogs. Quoting the reps
    # alone named a distance that fails too - an error message that sends the
    # athlete back with a number the code will refuse again.
    smallest = menu[-1][0] * (menu[-1][1] * 1000 / work_pace) + menu[-1][0] * (rec * 1000 / jog)
    raise ValueError(
        f"{distance_m / 1000:.1f} km is too short for a {kind} session with a warm-up and "
        f"cool-down: the smallest structure needs about "
        f"{(MIN_WARMUP_M + MIN_COOLDOWN_M + smallest) / 1000:.1f} km")


def _fit_reps_by_time(kind: str, total_s: int) -> tuple[int, int, int]:
    """(reps, work_seconds, recovery_seconds) that fit INSIDE a time budget,
    leaving room for the shortest acceptable warm-up and cool-down. More reps
    before fewer, so a longer budget buys more stimulus and not a longer jog -
    but the menu ends, and a budget beyond it simply gets a longer warm-up."""
    menu, rec = REP_MENU[kind]
    budget = total_s - MIN_WARMUP_S - MIN_COOLDOWN_S
    for reps, secs in sorted(menu, key=lambda m: -(m[0] * m[1])):
        if reps * secs + reps * rec <= budget:
            return reps, secs, rec
    smallest = min(menu, key=lambda m: m[0] * m[1])
    need = smallest[0] * (smallest[1] + rec) + MIN_WARMUP_S + MIN_COOLDOWN_S
    raise ValueError(
        f"{total_s // 60} min is too short for a {kind} session with a warm-up and cool-down: "
        f"the smallest structure needs about {-(-need // 60)} min")


def clean_name(name: str | None) -> str | None:
    """A workout name as it may reach the athlete's device.

    The name can come from the coach, and the coach reads Garmin free text -
    so a label engineered to look like an instruction ("URGENT - apply now")
    can be proposed as the name of a session. The athlete sees it in the
    preview before the click, which is the real guard; this one just makes
    sure what arrives is a single line of printable text rather than something
    that breaks the preview it is meant to be checked in."""
    if name is None:
        return None
    flat = " ".join(str(name).split())
    printable = "".join(c for c in flat if c.isprintable())
    return printable[:60].strip() or None


def build_session(kind: str, *, distance_km: float | None = None,
                  duration_min: int | None = None, zones: dict | None = None,
                  name: str | None = None) -> SessionSpec:
    """A complete session for one of `KINDS`, sized to a route or a duration.

    `zones` is `snapshot.build_zones`' block (`z4_low`, `z5_low`, `lthr_bpm`,
    `lt_pace_s_per_km`); missing values become assumptions, not silence."""
    if kind not in KINDS:
        raise ValueError(f"unknown session kind {kind!r}; one of {', '.join(KINDS)}")
    if (distance_km is None) == (duration_min is None):
        raise ValueError("give exactly one of distance_km or duration_min")
    if distance_km is not None and not 1.0 <= float(distance_km) <= 60.0:
        raise ValueError("distance_km must be between 1 and 60")
    if duration_min is not None and not 10 <= int(duration_min) <= 300:
        raise ValueError("duration_min must be between 10 and 300")

    lt, fast, jog, notes = _paces(zones)
    z = zones or {}
    blocks: list = []
    label = clean_name(name)

    if kind in ("easy", "long"):
        target, note = easy_cap(zones)
        if note:
            notes.append(note)
        if distance_km is not None:
            meters = int(round(distance_km * 1000))
            secs = int(meters * jog / 1000)
            blocks.append(Step("interval", meters=meters, target=target,
                               note="one continuous step - the cap is the whole point"))
        else:
            secs = int(duration_min) * 60
            meters = int(secs * 1000 / jog)
            blocks.append(Step("interval", seconds=secs, target=target))
        word = "Long" if kind == "long" else "Easy"
        label = label or (f"{word} run {meters / 1000:.0f} km" if distance_km
                          else f"{word} run {duration_min} min")
        return SessionSpec(kind, label, blocks, secs, meters, notes)

    if kind == "steady":
        # >= 12 min even effort at threshold, the one shape Garmin can measure
        # VO2max from. Custom bpm around LTHR; no zone target, the band is the point.
        if z.get("lthr_bpm"):
            target = hr_bpm(z["lthr_bpm"] - 3, z["lthr_bpm"] + 8)
        else:
            target = pace(lt + 10, lt - 5)
            notes.append("no measured LTHR - steady block set by pace instead of heart rate")
        if distance_km is not None:
            work = 15 * 60
            meters = int(round(distance_km * 1000))
            work_m = int(work * 1000 / lt)
            rest_m = meters - work_m
            if rest_m < MIN_WARMUP_M + MIN_COOLDOWN_M:
                raise ValueError(f"{distance_km:.1f} km is too short for a 12 min steady block "
                                 f"plus warm-up and cool-down")
            # The same floor the interval branches apply. Without it the 55/45
            # split put a 990 m warm-up in front of a threshold block at the
            # smallest route that passes the check above - under the minimum
            # this module documents, for one session type only.
            wu_m = max(MIN_WARMUP_M, int(rest_m * 0.55))
            cd_m = rest_m - wu_m
            blocks = [Step("warmup", meters=wu_m), Step("interval", seconds=work, target=target),
                      Step("cooldown", meters=cd_m)]
            secs = int(wu_m * jog / 1000) + work + int(cd_m * jog / 1000)
        else:
            # The requested duration is the budget, not a hint: warm-up and
            # cool-down take what the steady block leaves. Asking for 10 min
            # used to return a 30 min session without a word.
            secs = int(duration_min) * 60
            work = secs - MIN_WARMUP_S - MIN_COOLDOWN_S
            if work < STEADY_MIN_S:
                raise ValueError(
                    f"{duration_min} min is too short for a steady session: the block itself is "
                    f"at least {STEADY_MIN_S // 60} min, plus a warm-up and cool-down - ask for "
                    f"{(STEADY_MIN_S + MIN_WARMUP_S + MIN_COOLDOWN_S) // 60} min or more")
            wu = max(MIN_WARMUP_S, int((secs - work) * 0.55))
            cd = secs - work - wu
            blocks = [Step("warmup", seconds=wu), Step("interval", seconds=work, target=target),
                      Step("cooldown", seconds=cd)]
            meters = int(wu * 1000 / jog + work * 1000 / lt + cd * 1000 / jog)
        return SessionSpec(kind, label or f"Steady threshold {work // 60} min", blocks, secs, meters, notes)

    # vo2max | threshold
    work_target = hr_zone(5) if kind == "vo2max" else hr_zone(4)
    work_pace = fast if kind == "vo2max" else lt
    if distance_km is not None:
        meters = int(round(distance_km * 1000))
        reps, secs_each, rec = _fit_reps(kind, meters, lt, fast, jog)
        reps_m = int(reps * secs_each * 1000 / work_pace + reps * rec * 1000 / jog)
        rest_m = meters - reps_m
        wu_m = max(MIN_WARMUP_M, int(rest_m * 0.55))
        cd_m = rest_m - wu_m
        blocks = [Step("warmup", meters=wu_m),
                  Repeat(reps, [Step("interval", seconds=secs_each, target=work_target),
                                Step("recovery", seconds=rec)]),
                  Step("cooldown", meters=cd_m)]
        secs = int(wu_m * jog / 1000) + reps * (secs_each + rec) + int(cd_m * jog / 1000)
    else:
        # Same rep menu as the route fit, and the requested duration is the
        # budget: warm-up and cool-down absorb what the reps leave. The version
        # with a fixed 10 min warm-up and a cap of six reps answered "90 min"
        # with 54 and "10 min" with 28, in both cases silently.
        secs = int(duration_min) * 60
        reps, secs_each, rec = _fit_reps_by_time(kind, secs)
        rest = secs - reps * (secs_each + rec)
        wu = max(MIN_WARMUP_S, int(rest * 0.55))
        cd = rest - wu
        blocks = [Step("warmup", seconds=wu),
                  Repeat(reps, [Step("interval", seconds=secs_each, target=work_target),
                                Step("recovery", seconds=rec)]),
                  Step("cooldown", seconds=cd)]
        meters = int(wu * 1000 / jog + reps * secs_each * 1000 / work_pace
                     + reps * rec * 1000 / jog + cd * 1000 / jog)
    # EVERY recovery counts, the last one too: the repeat block Garmin runs
    # contains `reps` of them. Leaving one out of the arithmetic made a session
    # for a "10 km route" cover 10.4 km - the athlete finishes the cool-down
    # four hundred metres past the end of the route they named.
    label = label or f"{'VO2max' if kind == 'vo2max' else 'Threshold'} {reps}x{secs_each // 60} min"
    return SessionSpec(kind, label, blocks, secs, meters, notes)


def describe(spec: SessionSpec) -> str:
    """The preview a human confirms - and the model reads. Plain lines."""
    def tgt(t: dict | None) -> str:
        if not t:
            return "no target"
        if t["type"] == "hr_zone":
            return f"HR zone {t['zone']}"
        if t["type"] == "hr_bpm":
            return f"HR {t['lo']}-{t['hi']} bpm"
        lo, hi = t["lo_s_per_km"], t["hi_s_per_km"]
        return f"pace {lo // 60}:{lo % 60:02d}-{hi // 60}:{hi % 60:02d}/km"

    def one(s: Step, ind: str = "") -> str:
        amount = f"{s.meters / 1000:.1f} km" if s.meters else f"{s.seconds // 60}:{s.seconds % 60:02d} min"
        # `note` is the step's reason. It was written and read by nothing, so
        # the one line explaining why an easy run has no structure never
        # reached the athlete it was written for.
        return (f"{ind}{s.kind:<9} {amount:>9}  {tgt(s.target)}"
                + (f"  ({s.note})" if s.note else ""))

    lines = [f"{spec.name}  ({spec.kind}; ~{spec.estimated_meters / 1000:.1f} km, "
             f"~{spec.estimated_seconds // 60} min)"]
    for b in spec.blocks:
        if isinstance(b, Repeat):
            lines.append(f"  {b.iterations}x")
            lines.extend(one(s, "    ") for s in b.steps)
        else:
            lines.append(one(b, "  "))
    for a in spec.assumptions:
        lines.append(f"  assumption: {a}")
    return "\n".join(lines)


def to_json(spec: SessionSpec) -> dict:
    def step(s: Step) -> dict:
        return {"kind": s.kind, "seconds": s.seconds, "meters": s.meters,
                "target": s.target, "note": s.note}
    return {"kind": spec.kind, "name": spec.name,
            "blocks": [{"repeat": b.iterations, "steps": [step(s) for s in b.steps]}
                       if isinstance(b, Repeat) else step(b) for b in spec.blocks],
            "estimated_seconds": spec.estimated_seconds,
            "estimated_meters": spec.estimated_meters,
            "assumptions": list(spec.assumptions)}


def from_json(d: dict) -> SessionSpec:
    def step(x: dict) -> Step:
        return Step(x["kind"], x.get("seconds"), x.get("meters"), x.get("target"), x.get("note"))
    blocks = [Repeat(b["repeat"], [step(s) for s in b["steps"]]) if "repeat" in b else step(b)
              for b in d["blocks"]]
    return SessionSpec(d["kind"], d["name"], blocks, int(d["estimated_seconds"]),
                       int(d["estimated_meters"]), list(d.get("assumptions") or []))


# ── a week, and the readiness swap ───────────────────────────────────────────

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_DAYS_PER_WEEK = 4
DEFAULT_LONG_RUN_DAY = "sun"
#: The kinds that cost 48 h before the next one. `long` is easy intensity in
#: a polarised week and is spaced by a different rule (a fresh day before it).
HARD_KINDS = ("threshold", "vo2max")
#: A week never has more sessions than this: one rest day is not negotiable.
MAX_SESSIONS_PER_WEEK = 6
#: Default sizes when the athlete gives none (minutes).
WEEK_MINUTES = {"vo2max": 50, "threshold": 55, "easy": 45, "long": 90}

#: Offsets from the long run, in days, in the order the sessions are placed.
#: Q1 two days after the long run, Q2 two days after Q1 - 48 h between hard
#: stimuli - and nothing hard on the day before the long run (L+6) or after it
#: (L+1). Easy days fill in between, nearest the quality sessions first, so the
#: rest days end up next to the long run where the body wants them.
_QUALITY_OFFSETS = {1: ((3, "vo2max"),), 2: ((2, "vo2max"), (4, "threshold"))}
_EASY_OFFSETS = (3, 5, 1, 6)
#: How a calendar entry the app did NOT create is guessed to be hard. Titles
#: are the athlete's free text (or a plan's): a guess, flagged as one.
_HARD_TITLE_RE = re.compile(r"vo2|interval|threshold|tempo|schwelle|repeat|\d+\s*[x×]\s*\d+", re.I)


#: Floors for easy and long, where the builder itself has none.
_SOFT_FLOOR_MINUTES = {"easy": 20, "long": 40}


def min_minutes(kind: str) -> int:
    """The shortest session of this kind the builder can actually produce.

    DERIVED from the same constants `build_session` uses, not written down a
    second time: a hand-kept floor of 30 min for threshold was one minute under
    what the builder needs, and the week scaler asked for a session the builder
    then refused."""
    if kind in REP_MENU:
        menu, rec = REP_MENU[kind]
        reps, secs = min(menu, key=lambda m: m[0] * m[1])
        need = reps * (secs + rec) + MIN_WARMUP_S + MIN_COOLDOWN_S
    elif kind == "steady":
        need = STEADY_MIN_S + MIN_WARMUP_S + MIN_COOLDOWN_S
    else:
        return _SOFT_FLOOR_MINUTES.get(kind, 20)
    return -(-need // 60)          # round UP: one second short is a refusal
#: How far the week may be scaled from the built-in shape before the shape
#: itself is the wrong answer.
_SCALE_LIMITS = (0.5, 2.0)


def _week_sizes(kinds: list[str], total_minutes: int | None,
                override: dict | None) -> tuple[dict, str | None]:
    """Minutes per session kind for this week, scaled to what the athlete
    actually runs.

    The defaults are one athlete's week. Handed unchanged to someone running
    90 minutes a week they are a 160 % step in one go - and the size of the
    step is the best-evidenced injury factor in the whole file. So the week is
    scaled to the budget the caller derived from the athlete's own weeks, with
    floors, and the scaling is reported rather than performed quietly."""
    sizes = {**WEEK_MINUTES, **(override or {})}
    if not total_minutes:
        return sizes, None
    planned = sum(sizes[k] for k in kinds)
    if planned <= 0:
        return sizes, None
    lo, hi = _SCALE_LIMITS
    raw = total_minutes / planned
    factor = max(lo, min(hi, raw))
    scaled = {k: max(min_minutes(k), int(round(v * factor))) for k, v in sizes.items()}
    got = sum(scaled[k] for k in kinds)
    note = (f"sessions scaled to {int(round(factor * 100))} % of the built-in week "
            f"(~{got} min across {len(kinds)} days)")
    if raw < lo:
        note += (f" - your recent weeks are smaller than {int(lo * 100)} % of it, so this week is "
                 f"still a step up; drop a day if it is too much")
    elif raw > hi:
        note += f" - capped at {int(hi * 100)} %, the shape does not grow past that"
    return scaled, note


def build_week(start, *, days_per_week: int = DEFAULT_DAYS_PER_WEEK,
               long_run_day: str = DEFAULT_LONG_RUN_DAY, zones: dict | None = None,
               minutes: dict | None = None,
               total_minutes: int | None = None) -> tuple[list[tuple], list[str]]:
    """Seven days from `start`: a polarised week as `[(date, SessionSpec), …]`
    sorted by day, plus the assumptions that shaped it.

    Two quality sessions (one VO2max, one threshold) 48 h apart, the long run on
    the weekday the athlete named, easy runs between, the rest is rest. The
    rhythm is CYCLIC: with the long run on Sunday and the week starting Monday,
    "two days after the long run" is Tuesday - counted from last Sunday's run."""
    from datetime import timedelta

    if not 3 <= int(days_per_week) <= 7:
        raise ValueError("days_per_week must be between 3 and 7")
    if long_run_day not in WEEKDAYS:
        raise ValueError(f"long_run_day must be one of {', '.join(WEEKDAYS)}")
    assumptions: list[str] = []
    n = int(days_per_week)
    if n > MAX_SESSIONS_PER_WEEK:
        assumptions.append(f"{n} days asked, {MAX_SESSIONS_PER_WEEK} planned: one rest day "
                           f"is not negotiable")
        n = MAX_SESSIONS_PER_WEEK
    window = [start + timedelta(days=i) for i in range(7)]
    long_date = next(d for d in window if WEEKDAYS[d.weekday()] == long_run_day)

    def at(offset: int):
        d = long_date + timedelta(days=offset)
        return d if d <= window[-1] else d - timedelta(days=7)

    plan: dict = {long_date: "long"}
    for offset, kind in _QUALITY_OFFSETS[1 if n == 3 else 2]:
        plan[at(offset)] = kind
    for offset in _EASY_OFFSETS:
        if len(plan) >= n:
            break
        if at(offset) not in plan:      # a 3-day week has its quality day here
            plan[at(offset)] = "easy"

    sizes, scale_note = _week_sizes(list(plan.values()), total_minutes, minutes)
    if scale_note:
        assumptions.append(scale_note)

    sessions = []
    for day in sorted(plan):
        kind = plan[day]
        spec = build_session(kind, duration_min=sizes[kind], zones=zones)
        sessions.append((day, spec))
    # The zone assumptions are the same for every session; say them once.
    seen: set[str] = set()
    for _, spec in sessions:
        for a in spec.assumptions:
            if a not in seen:
                seen.add(a)
                assumptions.append(a)
        spec.assumptions = []
    return sessions, assumptions


def swap_for_readiness(decision: str, scheduled: list[dict], own_kinds: dict) -> dict:
    """What today's readiness decision means for what is on the calendar.

    `decision` is `logic.decide_today`'s word (`hard` | `easy` | `rest` |
    `unknown`), `scheduled` today's calendar entries (`schedule_id`,
    `workout_id`, `title`), `own_kinds` the kinds of the workouts this app
    created, by workout id. A hard entry on an easy or rest day is the one to
    replace; the answer names it and the kind that should take its place
    (`easy`, or nothing on a rest day). A calendar entry the app did not create
    is judged by its title - free text, so it can only ever be a guess, and the
    coach says so."""
    if decision not in ("easy", "rest"):
        return {"replace": [], "kind": None, "guessed": []}
    replace, guessed = [], []
    for entry in scheduled:
        kind = own_kinds.get(entry.get("workout_id"))
        if kind is not None:
            if kind in HARD_KINDS:
                replace.append(entry)
        elif _HARD_TITLE_RE.search(str(entry.get("title") or "")):
            replace.append(entry)
            guessed.append(entry)
    return {"replace": replace, "kind": "easy" if decision == "easy" else None, "guessed": guessed}
