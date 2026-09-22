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
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: What a session is for. `steady` exists because Garmin/Firstbeat only
#: re-measures VO2max from >= ~10 min of even effort near threshold; a spiky
#: interval session leaves the old estimate in place.
KINDS = ("easy", "long", "threshold", "vo2max", "steady")

#: Fallbacks when the store has no measured pace. Stated, never silent.
DEFAULT_EASY_PACE_S = 360        # 6:00/km
DEFAULT_LT_PACE_S = 300          # 5:00/km
MIN_WARMUP_M = 1000
MIN_COOLDOWN_M = 800
#: How much faster than threshold pace a VO2max rep runs (~3-5k race pace).
VO2MAX_PACE_FACTOR = 0.93
#: How much slower than threshold pace the recovery jog is.
RECOVERY_PACE_FACTOR = 1.30


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
        notes.append(f"no measured threshold pace - distances estimated at {lt // 60}:{lt % 60:02d}/km")
    return int(lt), int(round(lt * VO2MAX_PACE_FACTOR)), int(round(lt * RECOVERY_PACE_FACTOR)), notes


def _fit_reps(kind: str, distance_m: int, lt: int, fast: int, jog: int) -> tuple[int, int, int]:
    """(reps, work_seconds, recovery_seconds) whose distance leaves room for a
    warm-up and cool-down on this route. Reps shrink before rep length does:
    the stimulus per rep is the point, four short reps are not a VO2max session."""
    if kind == "vo2max":
        menu = [(5, 240), (4, 240), (4, 180), (3, 240), (3, 180)]
        rec = 150
        work_pace = fast
    else:  # threshold
        menu = [(4, 480), (3, 600), (3, 480), (2, 600), (2, 480)]
        rec = 120
        work_pace = lt
    budget = distance_m - MIN_WARMUP_M - MIN_COOLDOWN_M
    for reps, secs in menu:
        need = reps * (secs * 1000 / work_pace) + (reps - 1) * (rec * 1000 / jog)
        if need <= budget:
            return reps, secs, rec
    raise ValueError(
        f"{distance_m / 1000:.1f} km is too short for a {kind} session with a warm-up and "
        f"cool-down: the smallest structure needs about "
        f"{(MIN_WARMUP_M + MIN_COOLDOWN_M + menu[-1][0] * menu[-1][1] * 1000 / work_pace) / 1000:.1f} km")


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
    label = name

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
        work = max(12 * 60, (int(duration_min) * 60 - 20 * 60) if duration_min else 15 * 60)
        wu, cd = 10 * 60, 8 * 60
        if distance_km is not None:
            meters = int(round(distance_km * 1000))
            work_m = int(work * 1000 / lt)
            rest_m = meters - work_m
            if rest_m < MIN_WARMUP_M + MIN_COOLDOWN_M:
                raise ValueError(f"{distance_km:.1f} km is too short for a 12 min steady block "
                                 f"plus warm-up and cool-down")
            wu_m, cd_m = int(rest_m * 0.55), rest_m - int(rest_m * 0.55)
            blocks = [Step("warmup", meters=wu_m), Step("interval", seconds=work, target=target),
                      Step("cooldown", meters=cd_m)]
            secs = int(wu_m * jog / 1000) + work + int(cd_m * jog / 1000)
        else:
            blocks = [Step("warmup", seconds=wu), Step("interval", seconds=work, target=target),
                      Step("cooldown", seconds=cd)]
            secs = wu + work + cd
            meters = int(wu * 1000 / jog + work * 1000 / lt + cd * 1000 / jog)
        return SessionSpec(kind, label or f"Steady threshold {work // 60} min", blocks, secs, meters, notes)

    # vo2max | threshold
    work_target = hr_zone(5) if kind == "vo2max" else hr_zone(4)
    work_pace = fast if kind == "vo2max" else lt
    if distance_km is not None:
        meters = int(round(distance_km * 1000))
        reps, secs_each, rec = _fit_reps(kind, meters, lt, fast, jog)
        reps_m = int(reps * secs_each * 1000 / work_pace + (reps - 1) * rec * 1000 / jog)
        rest_m = meters - reps_m
        wu_m = max(MIN_WARMUP_M, int(rest_m * 0.55))
        cd_m = rest_m - wu_m
        blocks = [Step("warmup", meters=wu_m),
                  Repeat(reps, [Step("interval", seconds=secs_each, target=work_target),
                                Step("recovery", seconds=rec)]),
                  Step("cooldown", meters=cd_m)]
        secs = int(wu_m * jog / 1000) + reps * secs_each + (reps - 1) * rec + int(cd_m * jog / 1000)
    else:
        total = int(duration_min) * 60
        wu, cd = 10 * 60, 8 * 60
        rec = 150 if kind == "vo2max" else 120
        each = 240 if kind == "vo2max" else 480
        reps = max(2, min(6, (total - wu - cd + rec) // (each + rec)))
        blocks = [Step("warmup", seconds=wu),
                  Repeat(reps, [Step("interval", seconds=each, target=work_target),
                                Step("recovery", seconds=rec)]),
                  Step("cooldown", seconds=cd)]
        secs_each = each
        secs = wu + reps * each + (reps - 1) * rec + cd
        meters = int(wu * 1000 / jog + reps * each * 1000 / work_pace
                     + (reps - 1) * rec * 1000 / jog + cd * 1000 / jog)
    # The last recovery is dropped in the estimate above (Garmin runs it, the
    # athlete usually walks it off into the cool-down). The DTO keeps it.
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
        return f"{ind}{s.kind:<9} {amount:>9}  {tgt(s.target)}"

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
