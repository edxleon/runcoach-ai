"""Synthetic athletes: weeks of running, day by day, against the real tools.

The v0.3 review found 23 bugs, and nearly all of them were bugs of a COURSE,
not of a day: a pause, weeks that shrank, the first run back, a down week
right behind a return week, a measurement that never came due. Single
scenarios find those one at a time, after someone thinks of them. This runs
an athlete with a behaviour - follows the plan, skips some, falls ill, takes
three weeks off, always runs harder - through 16 weeks of the app, and then
checks what that athlete would expect of a coach over the whole course.

What it can and cannot tell you:
- It checks that the RULES behave over courses the way a runner expects. It
  cannot tell whether the training works: the body here is a toy, not
  physiology.
- It finds what its data model knows. The VO2max drift without runs was found
  on a real database, not here. The live test on real data stays.
- The expectations in `check` are written as a runner or coach would state
  them, never copied from `plan._week_plan`: a check that repeats a constant
  only proves the code agrees with itself.

    uv run python evals/sim.py pause_and_return [--weeks 16] [--seed 7]
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

#: A Monday. The course starts here, after six weeks of seeded history.
START = date(2026, 3, 2)
HISTORY_WEEKS = 6
HARD = ("threshold", "vo2max", "steady")

#: Toy training load per minute: enough for a Garmin-shaped ACWR, no more.
_LOAD_PER_MIN = {"easy": 1.0, "long": 1.1, "threshold": 1.7, "vo2max": 1.9, "steady": 1.6}


@dataclass(frozen=True)
class Persona:
    name: str
    base_week_min: int = 180
    days_per_week: int = 4
    #: Chance a planned session is run at all.
    compliance: float = 1.0
    #: `as_planned`, `harder` (30 % longer, easy turned into tempo, ignores the
    #: light) or `never_hard` (runs every hard session easy).
    effort: str = "as_planned"
    #: Garmin syncs the HR-zone detail of each run.
    zone_detail: bool = True
    #: Plans a week every Sunday and runs what is on the calendar; otherwise
    #: runs on fixed weekdays and asks the app each morning.
    plans_week: bool = True
    #: `rises` or `plateau` (VO2max never moves).
    vo2_response: str = "rises"
    #: (week, kind, days): kind `ill` or `pause`; week counts from START.
    events: tuple = ()
    seed: int = 1
    #: The weekdays this runner runs on (Monday = 0); empty = a default rhythm
    #: for `days_per_week`. The history before START is run on them.
    weekdays: tuple = ()
    #: The weekday of the long run.
    long_day: int = 6
    #: Other sports: (weekday, activity type, minutes, aerobic TE).
    other: tuple = ()
    #: True: the athlete filled in a profile, so the week is planned with their
    #: days and long-run day. False (most people): the planner reads the history.
    profile: bool = False
    #: Minutes this runner runs EVERY day, easy, whatever the calendar says
    #: (0 = runs the plan). The app still plans its weeks.
    own_rhythm: int = 0
    #: (weekday, minutes): no more time than this on that weekday - a run
    #: planned longer there is cut short. Never told to the app.
    short_day: tuple = ()
    #: A profile file as the athlete (or an editor) left it - written as is,
    #: past `profile.validate`, so a contradiction reaches the planner.
    profile_file: tuple = ()
    #: Says yes to the Sunday's week this many days later, through the real
    #: `plan.apply` against a Garmin double; 0 = the week is on the calendar
    #: at once, as after a timely apply.
    applies_late: int = 0
    #: Chance that the morning after a hard session something hurts - for
    #: 2 to 5 days, which the watch never sees. The runner says so on the
    #: Today tab, says it again each morning it still hurts, and says it is
    #: over the morning it is gone; every other time they forget, and the
    #: word runs out on its own.
    pain_after_hard: float = 0.0


PERSONAS = {p.name: p for p in (
    Persona("follower"),
    Persona("sporadic", compliance=0.6, seed=2),
    Persona("pause_and_return", events=((6, "pause", 21),), seed=3),
    Persona("sick", events=((5, "ill", 5),), seed=4),
    Persona("overreacher", effort="harder", seed=5),
    Persona("plateau", vo2_response="plateau", seed=6),
    Persona("no_zone_detail", zone_detail=False, seed=7),
    Persona("free_runner", plans_week=False, seed=8),
    # Runs Tuesday, Thursday and Saturday, long run Saturday - never told the app.
    Persona("saturday_long", days_per_week=3, base_week_min=150, weekdays=(1, 3, 5),
            long_day=5, seed=9),
    # Lifts on Tuesday evenings; runs Wednesday, Friday, Saturday, Sunday.
    Persona("gym_tuesday", weekdays=(2, 4, 5, 6), other=((1, "strength_training", 60, 3.0),),
            seed=10),
    # The same 55 easy minutes every day, more than the weeks before held.
    Persona("daily_even", days_per_week=7, weekdays=(0, 1, 2, 3, 4, 5, 6), base_week_min=280,
            effort="never_hard", own_rhythm=55, seed=11),
    # Thirty minutes on Tuesdays, never more; the app is never told.
    Persona("short_tuesday", weekdays=(1, 3, 5, 6), short_day=(1, 30), seed=12),
    # Five runs a week in the profile - and Monday, Wednesday and Friday off.
    Persona("contradictory", days_per_week=5, weekdays=(1, 3, 5, 6),
            profile_file=(("days_per_week", 5), ("off_days", ["mon", "wed", "fri"]),
                          ("long_run_day", "sun")), seed=13),
    # Says yes to the week on Wednesday: Monday and Tuesday are gone by then.
    Persona("late_yes", applies_late=3, seed=14),
    # A calf that hurts the day after one quality session in seven - HRV stays green.
    Persona("pain_after_quality", pain_after_hard=0.15, seed=15),
)}


@dataclass
class Run:
    kind: str
    minutes: int
    z4_s: int
    z5_s: int
    detail: bool


@dataclass
class DayRecord:
    day: date
    decision: str
    stimulus: str | None
    readiness: str
    ill: bool = False
    #: The runner's own word this morning: (kind, what they said) - `said`,
    #: `again` or `over` - and whether something hurt today.
    said: tuple | None = None
    hurts: bool = False
    #: The rule behind the decision (`decide_today`'s `reason`).
    reason: str | None = None
    ran: Run | None = None
    planned: tuple | None = None          # (kind, minutes) on today's calendar
    acwr: float | None = None
    #: On Sundays that plan: the proposal for the next week.
    week: dict | None = None
    #: Every third day: what `runspector today` prints that morning.
    morning: str | None = None
    #: The day a late yes was given: (proposal days, apply result, the Garmin
    #: double's schedule after it).
    applied: tuple | None = None


@dataclass
class Course:
    persona: Persona
    days: list[DayRecord] = field(default_factory=list)
    #: Every run, history included: day -> Run.
    runs: dict = field(default_factory=dict)


def jittered(p: Persona, seed: int) -> Persona:
    """The persona with its events moved by the seed, for the sweep: a pause
    in week 5, 6 or 8, of 12 or 30 days, finds what week 6 / 21 days does not."""
    if seed == p.seed:
        return p
    rng = random.Random(seed * 7919)
    ev = tuple((max(2, w + rng.randint(-3, 3)), k, max(3, d + rng.randint(-8, 12)))
               for w, k, d in p.events)
    return replace(p, events=ev, seed=seed)


# ── the toy body ─────────────────────────────────────────────────────────────

def _shape(kind: str, minutes: int) -> tuple[int, int]:
    """Seconds in Z4 and Z5 a session of this kind really spends."""
    if kind == "vo2max":
        return int(minutes * 60 * 0.10), int(minutes * 60 * 0.30)
    if kind == "threshold":
        return int(minutes * 60 * 0.45), 0
    if kind == "steady":
        return max(15 * 60, int(minutes * 60 * 0.35)), 0
    if kind == "tempo":
        return int(minutes * 60 * 0.40), 60
    return 0, 0


def _splits(aid: int, kind: str, minutes: int, z4: int, z5: int) -> list:
    """Garmin's laps for a structured workout: reps as ACTIVE/RECOVERY between a
    warm-up and a cool-down. Without them every threshold session would read as
    one continuous steady block - which the app rightly counts as a measurement."""
    from runspector.models import ActivitySplit

    if kind == "vo2max":
        reps, work, rest = max(2, z5 // 240), 240, 180
    elif kind == "threshold":
        reps, work, rest = max(2, z4 // 480), 480, 120
    elif kind == "steady":
        reps, work, rest = 1, z4, 0
    else:
        return []
    body = reps * work + (reps - 1) * rest
    warm = max(300, (minutes * 60 - body) // 2)
    types = ["INTERVAL_WARMUP"]
    durs = [warm]
    for i in range(reps):
        types.append("INTERVAL_ACTIVE")
        durs.append(work)
        if i < reps - 1:
            types.append("INTERVAL_RECOVERY")
            durs.append(rest)
    types.append("INTERVAL_COOLDOWN")
    durs.append(max(300, minutes * 60 - body - warm))
    return [ActivitySplit(activity_id=aid, split_index=i, split_type=t, duration_s=d)
            for i, (t, d) in enumerate(zip(types, durs, strict=False))]


class _Sim:
    def __init__(self, persona: Persona, weeks: int, home: Path):
        from runspector.store import Store

        self.p = persona
        self.weeks = weeks
        self.rng = random.Random(persona.seed)
        self.store = Store(home / "sim.db")
        self.course = Course(persona)
        self.aid = 8_000_000_000
        self.wid = 7_000_000
        self.vo2 = 48.0
        self.calendar: dict[date, tuple] = {}     # day -> (kind, minutes)
        #: day -> the planned session's easier option (minutes), if it has one.
        self.fallback: dict[date, int] = {}
        self.loads: dict[date, float] = {}
        self.events = {}
        #: The days something hurts (the toy body), and whether the runner
        #: will say "over" when it stops.
        self.pain: set[date] = set()
        self.pain_ends_said: dict[date, bool] = {}
        for w, kind, days in persona.events:
            first = START + timedelta(weeks=w)
            for i in range(days):
                self.events[first + timedelta(days=i)] = kind

    # The weekdays this runner runs on without a plan, Monday = 0.
    def _free_days(self) -> tuple[int, ...]:
        if self.p.weekdays:
            return tuple(self.p.weekdays)
        return {3: (1, 3, 6), 4: (1, 3, 5, 6), 5: (0, 1, 3, 5, 6)}.get(self.p.days_per_week,
                                                                       (1, 3, 5, 6))

    def other_sport(self, day: date) -> None:
        """The other sports of this weekday, as Garmin would record them."""
        from runspector.models import Activity

        for weekday, kind, minutes, te in self.p.other:
            if day.weekday() != weekday:
                continue
            self.aid += 1
            self.store.upsert_activity(Activity(
                activity_id=self.aid,
                start_time=datetime(day.year, day.month, day.day, 17, 0, tzinfo=UTC),
                activity_type=kind, name=kind, duration_s=minutes * 60, avg_hr=120,
                training_load=int(minutes * 0.8), aerobic_te=te, anaerobic_te=0.2))
            self.loads[day] = self.loads.get(day, 0) + minutes * 0.8

    def acwr(self, day: date) -> float | None:
        """Acute over chronic load the way Firstbeat weights it: exponentially,
        7 and 28 days, so a pause lets the chronic load fade instead of
        dropping out of a window all at once."""
        if not self.loads or (day - min(self.loads)).days < 28:
            return None          # Garmin reports no ratio before a month of history
        acute = chronic = 0.0
        d = min(self.loads)
        while d < day:
            load = self.loads.get(d, 0.0)
            acute += (load - acute) / 7
            chronic += (load - chronic) / 28
            d += timedelta(days=1)
        return min(3.0, round(acute / chronic, 2)) if chronic > 0.5 else None

    def morning(self, day: date) -> tuple[bool, float | None]:
        from runspector.models import DailyMetrics

        ill = self.events.get(day) == "ill"
        acwr = self.acwr(day)
        m = DailyMetrics(day=day)
        m.hrv_status = "LOW" if ill else "BALANCED"
        m.hrv_avg_ms = 44 if ill else 62
        m.sleep_seconds = 22000 if ill else 27000
        m.sleep_score = 48 if ill else 82
        m.body_battery_high = 35 if ill else 80
        m.resting_hr = 56 if ill else 48
        m.training_status = "PRODUCTIVE"
        m.acwr_ratio = acwr
        m.acwr_status = (None if acwr is None else "LOW" if acwr < 0.8 else "OPTIMAL"
                         if acwr <= 1.3 else "HIGH" if acwr <= 1.5 else "VERY_HIGH")
        m.vo2max = self._vo2_today(day)
        self.store.upsert_daily(m)
        self.today_metrics = m
        return ill, acwr

    def _vo2_today(self, day: date) -> float:
        """Firstbeat's value: it moves on a run, and drifts down without one -
        the trap the v0.3 reader found on a real account."""
        last = max((d for d in self.course.runs if d < day), default=None)
        if last is not None and (day - last).days >= 4 and (day - last).days % 3 == 0:
            self.vo2 = round(self.vo2 - 0.1, 1)
        return self.vo2

    def _after_run(self, day: date, run: Run) -> None:
        if self.p.vo2_response == "plateau":
            return
        stim = any(r.z5_s >= 180 for d, r in self.course.runs.items()
                   if 0 <= (day - d).days <= 10)
        if stim and self.rng.random() < 0.25:
            # The run moves the value; the rest of the morning's row stays.
            self.vo2 = round(self.vo2 + 0.1, 1)
            self.today_metrics.vo2max = self.vo2
            self.store.upsert_daily(self.today_metrics)

    def run(self, day: date, kind: str, minutes: int) -> Run:
        from runspector.models import Activity

        z4, z5 = _shape(kind, minutes)
        detail = self.p.zone_detail
        run = Run(kind, minutes, z4, z5, detail)
        self.aid += 1
        anaerobic = 2.6 if kind == "vo2max" else 1.0 if kind in ("threshold", "tempo") else 0.4
        self.store.upsert_activity(Activity(
            activity_id=self.aid,
            start_time=datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC),
            activity_type="running", name=f"{kind} run", distance_m=minutes * 180,
            duration_s=minutes * 60, avg_hr=150, max_hr=170,
            training_load=int(minutes * _LOAD_PER_MIN.get(kind, 1.5)),
            aerobic_te=3.6 if kind in HARD or kind == "tempo" else 2.6, anaerobic_te=anaerobic))
        splits = _splits(self.aid, kind, minutes, z4, z5)
        if splits:
            self.store.upsert_activity_splits(self.aid, splits)
        if detail:
            easy = max(0, minutes * 60 - z4 - z5)
            self.store.update_activity_detail(self.aid, {
                "hr_z1_s": 0, "hr_z2_s": easy, "hr_z3_s": 0, "hr_z4_s": z4, "hr_z5_s": z5,
                "hr_z4_low": 156, "hr_z5_low": 172, "splits": []})
        self.loads[day] = minutes * _LOAD_PER_MIN.get(kind, 1.5)
        self.course.runs[day] = run
        self._after_run(day, run)
        return run

    def seed_history(self) -> None:
        """Six ordinary weeks before START, so the course starts from a runner."""
        per = self.p.base_week_min // self.p.days_per_week
        for w in range(HISTORY_WEEKS, 0, -1):
            monday = START - timedelta(weeks=w)
            run_days = self._free_days()[: self.p.days_per_week]
            quality_day = next((d for d in run_days if d != self.p.long_day), run_days[0])
            for wd in range(7):
                d = monday + timedelta(days=wd)
                self.morning(d)
                self.other_sport(d)
                if wd == self.p.long_day and wd in run_days:
                    self.run(d, "long", int(per * 1.5))
                elif wd in run_days:
                    first = wd == quality_day
                    kind = "vo2max" if first and w % 2 else "threshold" if first else "easy"
                    self.run(d, kind, self._time_left(d, per))

    def _time_left(self, day: date, minutes: int) -> int:
        """`minutes`, or less on this runner's short day."""
        if self.p.short_day and day.weekday() == self.p.short_day[0]:
            return min(minutes, self.p.short_day[1])
        return minutes

    def plan_week(self, sunday: date) -> dict:
        from runspector import plan
        from runspector.models import ScheduledWorkout

        budget, lines, mode = plan._week_plan(self.store, sunday)
        from runspector import planning

        given = ({"days_per_week": self.p.days_per_week,
                  "long_run_day": planning.WEEKDAYS[self.p.long_day]} if self.p.profile else {})
        p = plan.propose_week(self.store, today=sunday, **given)
        sessions, fallbacks = [], {}
        for it in p["items"]:
            d = date.fromisoformat(it["day"])
            spec = it["spec"]
            mins = round(spec["estimated_seconds"] / 60)
            sessions.append((d, spec["kind"], mins))
            if spec.get("fallback_min"):
                self.fallback[d] = fallbacks[d] = spec["fallback_min"]
        if self.p.applies_late:
            # The yes comes later; until then nothing of this week is on the watch.
            self.pending = (p["id"], sunday + timedelta(days=self.p.applies_late), sessions)
            return {"budget": budget, "lines": lines, "mode": mode, "sessions": sessions,
                    "fallbacks": fallbacks, "id": p["id"],
                    "assumptions": [ln.split("assumption: ", 1)[1]
                                    for ln in str(p.get("preview") or "").splitlines()
                                    if "assumption: " in ln]}
        for d, kind, mins in sessions:
            self.calendar[d] = (kind, mins)
        # As after an apply: the app's own workouts on the Garmin calendar.
        items = []
        for d, kind, mins in sessions:
            self.wid += 1
            self.store.record_workout(self.wid, name=f"{kind} {mins} min", kind=kind,
                                      spec_json=json.dumps({"kind": kind}),
                                      schedule_id=self.wid, scheduled_day=d)
            items.append(ScheduledWorkout(schedule_id=self.wid, day=d, workout_id=self.wid,
                                          title=f"{kind} {mins} min", sport="running"))
        monday = sunday + timedelta(days=1)
        self.store.replace_scheduled_workouts(items, monday, monday + timedelta(days=6))
        return {"budget": budget, "lines": lines, "mode": mode, "sessions": sessions,
                "fallbacks": fallbacks,
                "assumptions": [ln.split("assumption: ", 1)[1]
                                for ln in str(p.get("preview") or "").splitlines()
                                if "assumption: " in ln]}

    def late_yes(self, day: date) -> tuple | None:
        """The Sunday's week, applied today through the real write path."""
        pending = getattr(self, "pending", None)
        if not pending or pending[1] != day:
            return None
        from runspector import plan

        if not hasattr(self, "garmin"):
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
            from conftest import FakeGarmin

            self.garmin = FakeGarmin()
        pid, _, sessions = pending
        self.pending = None
        before = set(self.garmin.data.get("schedule") or {})
        result = plan.apply(self.store, self.garmin, pid, today=day)
        placed = {date.fromisoformat(it["day"]) for it in result.get("items") or []
                  if it.get("workout_id")}
        for d, kind, mins in sessions:
            if d in placed:
                self.calendar[d] = (kind, mins)
        # Only what THIS yes put on the calendar; earlier weeks are there too.
        new = {k: v for k, v in (self.garmin.data.get("schedule") or {}).items() if k not in before}
        return (sessions, result, new)

    def check_in(self, day: date) -> tuple | None:
        """The runner's word on the Today tab this morning, before the app
        is read: something started hurting after yesterday's hard session,
        still hurts, or stopped. Through the real store, as the page does."""
        p = self.p
        if not p.pain_after_hard:
            return None
        yesterday = self.course.runs.get(day - timedelta(days=1))
        if (day not in self.pain and yesterday is not None and yesterday.kind in HARD
                and self.rng.random() < p.pain_after_hard):
            for k in range(self.rng.randint(2, 5)):
                self.pain.add(day + timedelta(days=k))
            last = max(d for d in self.pain if d >= day)
            self.pain_ends_said[last + timedelta(days=1)] = self.rng.random() < 0.7
        if day in self.pain:
            first = day - timedelta(days=1) not in self.pain
            self.store.say_check_in("pain", day, note="calf" if first else None)
            return ("pain", "said" if first else "again")
        if self.pain_ends_said.pop(day, False):
            self.store.end_check_in("pain", day)
            return ("pain", "over")
        return None

    def athlete(self, day: date, decision: str, stimulus: str | None, ill: bool) -> Run | None:
        """What this runner does today, given what the app said."""
        if ill or self.events.get(day) == "pause" or day in self.pain:
            return None
        if self.p.own_rhythm:
            return None if decision == "rest" else self.run(day, "easy", self.p.own_rhythm)
        planned = self.calendar.get(day)
        if self.p.plans_week:
            if planned is None or self.rng.random() > self.p.compliance:
                return None
            kind, mins = planned
        else:
            if day.weekday() not in self._free_days():
                return None
            mins = self.p.base_week_min // self.p.days_per_week
            kind = stimulus if decision == "hard" and stimulus in HARD else "easy"
            if day.weekday() == self.p.long_day:
                kind, mins = "long", int(mins * 1.5)
        mins = self._time_left(day, mins)
        if self.p.effort == "harder":
            return self.run(day, "tempo" if kind == "easy" else kind, int(mins * 1.3))
        # A runner who listens: a red light is a rest day, an easy light an easy run.
        if decision == "rest":
            return None
        if kind in HARD and (decision != "hard" or self.p.effort == "never_hard"):
            # The plan's own easier option where it gave one, else the same time easy.
            kind, mins = "easy", self._time_left(day, self.fallback.get(day, mins))
        return self.run(day, kind, mins)


def simulate(persona: Persona, weeks: int = 16, probe=None) -> Course:
    """Run `persona` through `weeks` of the app. Freezes `paths.today` and
    points RUNSPECTOR_HOME at a temp dir for the duration, as fixture_gen does.

    `probe(day, store)` is called at the end of every day - after the run,
    before a Sunday's planning - with the clock still on that day: the place
    to render what the tools say at a moment of the course."""
    from runspector import paths, snapshot, tools

    real_today = paths.today
    old_home = os.environ.get("RUNSPECTOR_HOME")
    os.environ.setdefault("RUNSPECTOR_TZ", "Europe/Berlin")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["RUNSPECTOR_HOME"] = tmp
        clock = [START]
        paths.today = lambda: clock[0]
        try:
            if persona.profile_file:
                paths.profile_path().write_text(json.dumps(dict(persona.profile_file)),
                                                encoding="utf-8")
            sim = _Sim(persona, weeks, Path(tmp))
            sim.seed_history()
            if persona.plans_week:
                clock[0] = START - timedelta(days=1)
                sim.plan_week(clock[0])
            for i in range(weeks * 7):
                day = START + timedelta(days=i)
                clock[0] = day
                ill, acwr = sim.morning(day)
                said = sim.check_in(day)
                snap = snapshot.assemble(sim.store, today=day)
                dec = snap["decision_today"]
                stim = (dec.get("stimulus") or {}).get("kind")
                rec = DayRecord(day=day, decision=dec["decision"], stimulus=stim,
                                readiness=tools.get_training_readiness(sim.store),
                                ill=ill, planned=sim.calendar.get(day), acwr=acwr,
                                said=said, hurts=day in sim.pain, reason=dec.get("reason"))
                if i % 3 == 0:
                    rec.morning = tools.today_text(sim.store)[0]
                rec.applied = sim.late_yes(day)
                rec.ran = sim.athlete(day, rec.decision, stim, ill)
                if not ill and sim.events.get(day) != "pause":
                    sim.other_sport(day)
                if probe is not None:
                    probe(day, sim.store)
                if day.weekday() == 6 and persona.plans_week:
                    rec.week = sim.plan_week(day)
                sim.course.days.append(rec)
            return sim.course
        finally:
            paths.today = real_today
            if old_home is None:
                os.environ.pop("RUNSPECTOR_HOME", None)
            else:
                os.environ["RUNSPECTOR_HOME"] = old_home


# ── what a runner expects of a coach ─────────────────────────────────────────

def _window(course: Course, end: date, days: int = 7) -> int:
    """Minutes actually run in the `days` days up to and including `end`."""
    return sum(r.minutes for d, r in course.runs.items() if 0 <= (end - d).days < days)


def _gaps(course: Course) -> list[tuple[date, date]]:
    """(last run before, first run after) for every break of 10+ days."""
    days = sorted(course.runs)
    return [(a, b) for a, b in zip(days, days[1:], strict=False) if (b - a).days >= 10]


def check(course: Course) -> list[str]:
    """Every expectation broken, one line each, with the day and the numbers."""
    p, out = course.persona, []
    days = course.days
    gaps = _gaps(course)
    event_days = {START + timedelta(weeks=w, days=i) for w, _, n in p.events for i in range(n)}

    def back_in(d: date) -> bool:
        """Ten days into a break, or in the first week back from one. Not the
        first days of a break: nobody, the app included, knows yet that a
        pause has begun."""
        return any(last + timedelta(days=10) <= d <= first + timedelta(days=6) for last, first in gaps)

    def ran_lately(d: date) -> int:
        """The bigger of the last two weeks that had running in them, looking
        back up to eight weeks: after a break the reference is the running
        before it, not the zero of the break."""
        weeks_run = [m for m in (_window(course, d - timedelta(days=7 * k)) for k in range(8))
                     if m > 0]
        return max(weeks_run[:2], default=0)

    for _i, r in enumerate(days):
        # The app always answers, with its decision spelled out.
        if "The app's decision for today:" not in r.readiness:
            out.append(f"{r.day}: readiness text carries no decision")
        # The morning text says the call the page shows, for the day it is.
        if r.morning is not None:
            from runspector import tools

            word = tools._CALL.get(r.decision)
            first = r.morning.splitlines()[0] if r.morning else ""
            if word and not first.startswith(f"{r.day:%a %d %b}: {word}"):
                out.append(f"{r.day}: the morning text says {first!r}, the call is {r.decision}")
        # Never hard when the body says ill.
        if r.ill and r.decision == "hard":
            out.append(f"{r.day}: ill (HRV low, short sleep) and the app said hard")
        # Never two hard days in a row.
        prev = course.runs.get(r.day - timedelta(days=1))
        if r.decision == "hard" and prev and (prev.kind in HARD or prev.z5_s >= 180
                                              or prev.minutes >= 70):
            out.append(f"{r.day}: hard right after yesterday's {prev.kind} run")
        # The first week back is easy, whatever the light says.
        if back_in(r.day) and r.decision == "hard":
            out.append(f"{r.day}: hard day in a break or the first week back")
        # The app does not name VO2max work when it cannot see the zones of a
        # run that in truth had them.
        if r.stimulus == "vo2max":
            hidden = [d for d, run in course.runs.items()
                      if 0 <= (r.day - d).days < 7 and not run.detail and run.z5_s >= 180]
            if hidden:
                out.append(f"{r.day}: names VO2max though {hidden[0]} had Z5 it could not see")

    # A runner who said it hurts is not sent out while it does, and comes
    # back easy: at least as many days without a hard session as it hurt.
    for k, r in enumerate(days):
        if r.hurts and r.decision != "rest":
            out.append(f"{r.day}: the runner said it hurts and the app said {r.decision}")
        if r.hurts and r.morning is not None and "You said: in pain" not in r.morning:
            out.append(f"{r.day}: the morning text does not say what the runner said")
        if r.hurts and "The athlete's own word: in pain" not in r.readiness:
            out.append(f"{r.day}: the coach is not told the runner said it hurts")
        if r.hurts and (k + 1 == len(days) or not days[k + 1].hurts):
            hurt = 1
            while k - hurt >= 0 and days[k - hurt].hurts:
                hurt += 1
            hard = [d.day for d in days[k + 1:k + 1 + hurt] if d.decision == "hard"]
            if hard:
                out.append(f"{r.day}: {hurt} days of pain ended, and the app said hard on {hard}")

    # A runner who listens is not kept off their feet by numbers: no more than
    # three rest calls in a row unless the body itself says ill - or it hurts.
    # Keyed on the runner's own pain, not on the app's label for the day: a
    # word the app never lets run out must still show up here.
    streak = 0
    for r in days:
        streak = streak + 1 if r.decision == "rest" and not r.ill and not r.hurts else 0
        if streak == 4:
            out.append(f"{r.day}: fourth rest call in a row without illness "
                       f"(ACWR {r.acwr})")

    weeks = [r for r in days if r.week]
    for j, r in enumerate(weeks):
        w, total = r.week, sum(m for _, _, m in r.week["sessions"])
        ran_last2 = ran_lately(r.day)
        last_run = max((d for d in course.runs if d <= r.day), default=None)
        before = next((b for b, f in gaps if b < r.day <= f + timedelta(days=13)), None)
        if before is None and last_run and (r.day - last_run).days >= 9:
            before = last_run
        if before is not None:
            # In a break and the two weeks after it the yardstick is the
            # running before it: a return at most at the size of those weeks.
            ran_last2 = max(_window(course, before - timedelta(days=7 * k)) for k in range(4))
        # A week never jumps far beyond what was actually run lately.
        if ran_last2 and total > max(1.3 * ran_last2, ran_last2 + 30):
            out.append(f"{r.day}: plans {total} min after running at most {ran_last2} "
                       f"in a week lately ({w['lines'][:1]})")
        # No single session far beyond the longest run of the last four weeks.
        longest = max((run.minutes for d, run in course.runs.items()
                       if 0 <= (r.day - d).days < 28), default=0)
        for d, kind, mins in w["sessions"]:
            if longest and mins > max(1.5 * longest, longest + 20):
                out.append(f"{r.day}: {kind} of {mins} min on {d}, longest lately {longest}")
        # The week planned during a break or right after it has no hard session.
        monday = r.day + timedelta(days=1)
        last = max((d for d in course.runs if d <= r.day), default=None)
        # (The return week itself is the first week back; the hard sessions may
        # come back in the week after it.)
        first_back = next((f for _, f in gaps if f <= r.day and monday <= f + timedelta(days=6)),
                          None)
        if last is not None and ((monday - last).days >= 10 or first_back is not None):
            hard = [(d, k) for d, k, _ in w["sessions"] if k in HARD]
            if hard:
                out.append(f"{r.day}: week planned in or right after a break "
                           f"(last run {last}) holds hard sessions {hard}")
        # Every hard session and long run says in advance what to do on a day
        # it is too much: an easy run, never longer than the session itself.
        for d, kind, mins in w["sessions"]:
            if kind in HARD or kind == "long":
                fb = w.get("fallbacks", {}).get(d)
                if not fb or fb > mins:
                    out.append(f"{r.day}: the {kind} of {mins} min on {d} has no sound easier "
                               f"option ({fb})")
        # A week is mostly easy running: never more hard sessions than other ones.
        kinds = [k for _, k, _ in w["sessions"]]
        n_hard = sum(k in HARD for k in kinds)
        if n_hard > len(kinds) - n_hard:
            out.append(f"{r.day}: a week of {kinds} - more hard sessions than easy ones")
        # A step up is up: a week the line calls a step is never smaller than
        # the week planned before it. (Not for the runner whose calf takes
        # weeks out: their weeks shrink to the shortest useful sessions, and
        # there a week built from the same budget comes out a few minutes
        # smaller - a steady block shorter than the threshold session it
        # replaces, or one day fewer. The step floor compares budgets, not the
        # sessions built from them: a planner limit `docs/roadmap.md` "Smaller"
        # names, not one of the check-in; seen on seeds 1008 and 1011.)
        if j and w["mode"] == "step" and weeks[j - 1].week["mode"] in ("step", "hold") \
                and not p.pain_after_hard:
            before = sum(m for _, _, m in weeks[j - 1].week["sessions"])
            # ...when that week was run: a plan skipped does not lift the next one;
            # and not when the session cap bites (a long run skipped shrinks it).
            capped = any(a.startswith("no session over") for a in w["assumptions"])
            if total < before - 5 and _window(course, r.day) >= 0.9 * before and not capped:
                out.append(f"{r.day}: a 'step' week of {total} min after a planned {before}")
        # Running harder than planned slows the plan down.
        if p.effort == "harder" and r.acwr and r.acwr > 1.3 and j:
            before = sum(m for _, _, m in weeks[j - 1].week["sessions"])
            if total > before:
                out.append(f"{r.day}: ACWR {r.acwr} and the plan still grows {before} -> {total}")

    # Regular lighter weeks, never two in a row - for a runner who follows the plan
    # through a quiet course (a pause or illness makes its own light weeks).
    if p.plans_week and p.compliance == 1.0 and not p.events and p.effort == "as_planned" \
            and not p.applies_late and not p.pain_after_hard:
        totals = [sum(m for _, _, m in r.week["sessions"]) for r in weeks]
        light = [b < 0.85 * a for a, b in zip(totals, totals[1:], strict=False)]
        if sum(light) < len(totals) // 6:
            out.append(f"too few lighter weeks across {len(totals)}: {totals}")
        if any(a and b for a, b in zip(light, light[1:], strict=False)):
            out.append(f"two lighter weeks in a row: {totals}")
        if totals and max(totals[-4:]) <= max(totals[:4]):
            out.append(f"no progress over the course: {totals}")

    # The VO2max work does not disappear: a runner who runs what the app says
    # does a Z5 session at least every two weeks outside a break.
    # (A late yes loses the first days of every week: that runner does not run
    # the whole plan, like one who skips sessions. Nor does one whose calf
    # takes sessions out - and after each pain the light reads the lower load
    # as ACWR < 0.8 and keeps the days easy until the ratio recovers, which
    # `docs/roadmap.md` "Smaller" names.)
    if p.effort == "as_planned" and p.compliance == 1.0 and p.zone_detail and not p.applies_late \
            and not p.pain_after_hard:
        for r in days[14:]:
            if any(r.day - timedelta(days=k) in event_days or back_in(r.day - timedelta(days=k))
                   for k in range(21)):
                continue
            if not any(run.z5_s >= 180 for d, run in course.runs.items()
                       if 0 <= (r.day - d).days < 14):
                out.append(f"{r.day}: no Z5 session in the last 14 days")
                break
    # After a break, following the plan brings the runner back: within eight
    # weeks of the first run back the planned weeks reach 70 % of before.
    if p.plans_week and p.compliance == 1.0:
        for last, first in gaps:
            before = max((_window(course, last - timedelta(days=7 * k)) for k in range(4)),
                         default=0)
            later = [sum(m for _, _, m in r.week["sessions"]) for r in weeks
                     if first < r.day <= first + timedelta(weeks=8)]
            # Only a course that ran all eight weeks after the return can tell.
            if before and len(later) >= 8 and max(later) < 0.7 * before:
                out.append(f"back from {first}: weeks {later} never reach 70 % of {before} min")

    # The week lands on the runner's own days, though they never said which:
    # the long run on their long-run day, most sessions on days they run.
    if p.plans_week and not p.profile and p.weekdays and p.compliance == 1.0 and not p.events:
        from runspector import planning

        theirs = {planning.WEEKDAYS[w] for w in p.weekdays}
        for r in weeks:
            if r.day < START + timedelta(weeks=4):
                continue
            ses = r.week["sessions"]
            longs = [d for d, k, _ in ses if k == "long"]
            if longs and longs[0].weekday() != p.long_day:
                out.append(f"{r.day}: long run on {longs[0]:%a}, the runner's is "
                           f"{planning.WEEKDAYS[p.long_day]}")
            on_theirs = sum(planning.WEEKDAYS[d.weekday()] in theirs for d, _, _ in ses)
            if len(ses) <= len(theirs) and on_theirs < 0.75 * len(ses):
                out.append(f"{r.day}: {on_theirs} of {len(ses)} sessions on the runner's days "
                           f"{sorted(theirs)}: {[(f'{d:%a}', k) for d, k, _ in ses]}")
    # A regular other sport keeps the quality runs off its day and the next.
    if p.other and p.plans_week and not p.events:
        blocked = {w for w, *_ in p.other} | {(w + 1) % 7 for w, *_ in p.other}
        for r in weeks:
            if r.day < START + timedelta(weeks=4):
                continue
            clash = [(f"{d:%a}", k) for d, k, _ in r.week["sessions"]
                     if k in HARD and d.weekday() in blocked]
            if clash:
                out.append(f"{r.day}: quality on or right after the other sport: {clash}")

    # A heavy week of the same run every day is not answered with a step up:
    # the runner's days were all alike and more than their weeks before held,
    # so the coach plans no step and never more than the last seven days ran.
    if p.own_rhythm:
        def load(d: date) -> float:
            run = course.runs.get(d)
            return run.minutes * _LOAD_PER_MIN.get(run.kind, 1.5) if run else 0.0
        for r in weeks:
            last7 = [course.runs.get(r.day - timedelta(days=k)) for k in range(7)]
            if not all(x is not None and x.minutes == p.own_rhythm for x in last7):
                continue
            week_load = sum(load(r.day - timedelta(days=k)) for k in range(7))
            before = [sum(load(r.day - timedelta(days=7 * w + k)) for k in range(7))
                      for w in range(1, 5)]
            if week_load < sum(before) / 4:
                continue
            total = sum(m for _, _, m in r.week["sessions"])
            ran = _window(course, r.day)
            if r.week["mode"] == "step" or total > ran:
                out.append(f"{r.day}: seven alike days, heavier than the weeks before, and the "
                           f"plan is a {r.week['mode']} of {total} min after {ran} run "
                           f"({r.week['lines'][:1]})")

    # A day this runner has little time on is planned as short as it is, and
    # without the week's hard sessions while another day can take them.
    if p.short_day and p.plans_week:
        wd, limit = p.short_day
        for r in weeks:
            ses = r.week["sessions"]
            hard_elsewhere = any(k in HARD for d, k, _ in ses if d.weekday() != wd)
            for d, k, m in ses:
                if d.weekday() != wd:
                    continue
                if m > limit + 1:
                    out.append(f"{r.day}: {k} of {m} min on {d:%a}, a day with {limit} min")
                if k in HARD and hard_elsewhere:
                    out.append(f"{r.day}: the hard {k} on {d:%a}, the short day")

    # Days off in the profile are never planned on, even when the profile asks
    # for more runs than its free days hold - and the week says so.
    if p.profile_file:
        prof = dict(p.profile_file)
        off = set(prof.get("off_days") or [])
        from runspector import planning

        for r in weeks:
            ses = r.week["sessions"]
            on_off = [(f"{d:%a}", k) for d, k, _ in ses if planning.WEEKDAYS[d.weekday()] in off]
            if on_off:
                out.append(f"{r.day}: sessions on the athlete's days off: {on_off}")
            if len(ses) > 7 - len(off):
                out.append(f"{r.day}: {len(ses)} sessions in a week with {7 - len(off)} free days")
            asked = prof.get("days_per_week") or 0
            if asked > 7 - len(off) and not any("days off" in a for a in r.week["assumptions"]):
                out.append(f"{r.day}: {asked} runs asked, {7 - len(off)} free days, and the week "
                           f"does not say why it planned fewer")

    # A yes given late puts nothing on a day that has passed, says which days
    # it skipped, and puts the rest of the week where it belongs.
    for r in days:
        if not r.applied:
            continue
        sessions, result, schedule = r.applied
        past = [d for d in schedule.values() if str(d.get("date")) < r.day.isoformat()]
        if past:
            out.append(f"{r.day}: the late yes scheduled into the past: {past}")
        gone = [d for d, _, _ in sessions if d < r.day]
        said = " ".join(result.get("warnings") or []) + " " + str(result.get("error") or "")
        for d in gone:
            if d.isoformat() not in said:
                out.append(f"{r.day}: the late yes does not say {d} was skipped ({said.strip()!r})")
        ahead = {d.isoformat() for d, _, _ in sessions if d >= r.day}
        placed = {str(e.get("date")) for e in schedule.values()}
        if not ahead <= placed:
            out.append(f"{r.day}: days still ahead were not put on the watch: "
                       f"{sorted(ahead - placed)}")

    # A plateau gets measured: a steady block within six weeks of the course.
    if p.vo2_response == "plateau":
        steady = [d for d, run in course.runs.items() if run.kind == "steady" and d >= START]
        if not steady or steady[0] > START + timedelta(weeks=6):
            out.append(f"plateau: first steady block {steady[:1] or 'never'}")
    return out


# ── a readable course ────────────────────────────────────────────────────────

def table(course: Course) -> str:
    rows = ["week of     | planned (mode, min)         | ran  | days hard/easy/rest | notes"]
    for k in range(0, len(course.days), 7):
        wk = course.days[k:k + 7]
        sunday = wk[-1]
        ran = sum(r.ran.minutes for r in wk if r.ran)
        counts = {x: sum(r.decision == x for r in wk) for x in ("hard", "easy", "rest")}
        plan_txt = ""
        if sunday.week:
            total = sum(m for _, _, m in sunday.week["sessions"])
            kinds = "/".join(k[:3] for _, k, _ in sunday.week["sessions"])
            plan_txt = f"{sunday.week['mode']:7} {total:4} {kinds}"
        notes = ", ".join(sorted({"ill" for r in wk if r.ill}
                                 | {"pain" for r in wk if r.hurts}
                                 | {"off" for r in wk if not r.ran and r.planned}))
        rows.append(f"{wk[0].day} | {plan_txt:27} | {ran:4} | "
                    f"{counts['hard']}/{counts['easy']}/{counts['rest']:<15} | {notes}")
    return "\n".join(rows)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("persona", choices=sorted(PERSONAS))
    ap.add_argument("--weeks", type=int, default=16)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--days", metavar="FROM:TO", help="also print every day in this ISO range")
    a = ap.parse_args()
    p = PERSONAS[a.persona]
    course = simulate(jittered(p, a.seed) if a.seed is not None else p, a.weeks)
    print(table(course))
    if a.days:
        lo, hi = (date.fromisoformat(x) for x in a.days.split(":"))
        print()
        for r in course.days:
            if lo <= r.day <= hi:
                ran = f"{r.ran.kind} {r.ran.minutes}" if r.ran else "-"
                cal = f"{r.planned[0]} {r.planned[1]}" if r.planned else "-"
                print(f"{r.day} {r.day:%a} {r.decision:7} {str(r.stimulus):9} cal {cal:14} "
                      f"ran {ran:14} acwr {r.acwr}{'  ILL' if r.ill else ''}"
                      f"{f'  said {r.said[0]} {r.said[1]}' if r.said else ''}")
                if r.week:
                    w = r.week
                    print(f"    plans {w['mode']}: " + ", ".join(
                        f"{d:%a} {k} {m}" for d, k, m in w["sessions"]))
                    print(f"    {w['lines'][0][:160] if w['lines'] else ''}")
    broken = check(course)
    print("\n" + ("\n".join(broken) if broken else "every expectation held"))
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
