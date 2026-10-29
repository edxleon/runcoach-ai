"""The athlete's habits, read from their own history - in code.

A planner that defaults to "four days, long run on Sunday" plans somebody
else's week for everyone who has not filled in a profile: the runner who has
run Tuesday, Thursday and Saturday for months gets Tuesday, Wednesday,
Thursday and Sunday. The history already says which days are theirs.

Pure functions over a list of activities (`{day, type, duration_s, hard}`);
the store reads them, `plan.propose_week` uses the result. Every value comes
with the evidence it rests on, and the evidence is what the proposal prints:
"long run on sat - your longest run fell on sat in 6 of 8 weeks".

The weeks read are the last `HABIT_WEEKS` Monday-weeks WITH a run, looking
back up to `LOOKBACK_WEEKS` - not simply the last eight weeks: after a pause
those are empty, and the habits would vanish exactly when the athlete starts
again. The caller passes `until` to read only the weeks before a pause while
the athlete is still coming back from it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import planning

#: How many weeks with a run the habits are read from.
HABIT_WEEKS = 8
#: How far back those weeks may lie: half a year.
LOOKBACK_WEEKS = 26
#: Fewer running weeks than this and a habit is a guess: the defaults stay.
MIN_RUNNING_WEEKS = 4
#: A weekday is "one of yours" when a run fell on it in this share of the weeks.
PREFERRED_SHARE = 0.4
#: The long-run day is the weekday of the week's longest run - if it is the
#: same weekday in at least this share of the weeks.
LONG_DAY_SHARE = 0.5
#: A week's longest run is its long run only if it is clearly longer than
#: the others: in a week of equal runs, "the longest" is whichever came first.
LONG_RUN_MARGIN = 1.2
#: Another sport on the same weekday in this share of the weeks keeps the
#: quality runs off that day and the day after it.
OTHER_SPORT_SHARE = 0.4
#: Shorter than this, another sport is a walk to the shop, not a session.
OTHER_SPORT_MIN_S = 20 * 60
#: Not "another sport" for this purpose: moving, not training.
_NOT_TRAINING = ("walking", "hiking", "breathwork", "meditation", "yoga")


@dataclass(frozen=True)
class Habits:
    days_per_week: int | None = None
    long_run_day: str | None = None
    #: Weekdays with a run in most weeks, most frequent first.
    preferred: tuple[str, ...] = ()
    #: Weekdays a quality run should not land on: another sport that day or
    #: the day before.
    avoid_hard: frozenset[str] = frozenset()
    #: The weekdays of the other sport itself, calendar order.
    sport_days: tuple[str, ...] = ()
    evidence: dict[str, str] = field(default_factory=dict)
    weeks: int = 0

    @property
    def known(self) -> bool:
        return self.weeks >= MIN_RUNNING_WEEKS


NONE = Habits()


def _is_run(a: dict) -> bool:
    return "running" in str(a.get("type") or "")


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def derive(activities: list[dict], *, today: date, until: date | None = None) -> Habits:
    """The habits in `activities` (all sports, any order), read from the last
    `HABIT_WEEKS` Monday-weeks with a run that ended before this week - the
    week being run is not a whole week yet. `until` ends the window earlier:
    the week holding that day is the last one read (a pause's start)."""
    last_monday = _monday(until) if until is not None else _monday(today) - timedelta(weeks=1)
    first_monday = _monday(today) - timedelta(weeks=LOOKBACK_WEEKS)
    by_week: dict[date, list[dict]] = {}
    for a in activities:
        day = a.get("day")
        if day is None:
            continue
        monday = _monday(day)
        if first_monday <= monday <= last_monday:
            by_week.setdefault(monday, []).append(a)
    running = sorted((m for m, acts in by_week.items() if any(_is_run(a) for a in acts)),
                     reverse=True)[:HABIT_WEEKS]
    n = len(running)
    if n < MIN_RUNNING_WEEKS:
        return Habits(weeks=n, evidence={"weeks": f"only {n} week(s) with a run in the last "
                                                  f"{LOOKBACK_WEEKS} - too few to read habits from"})
    span = f"of {n} weeks"
    run_days_per_week, run_weekdays, long_weekdays = [], Counter(), Counter()
    other_weekdays: Counter = Counter()
    for monday in running:
        acts = by_week[monday]
        runs = [a for a in acts if _is_run(a)]
        days = {a["day"] for a in runs}
        run_days_per_week.append(len(days))
        run_weekdays.update({d.weekday() for d in days})
        durations = sorted((int(a.get("duration_s") or 0) for a in runs), reverse=True)
        # Against the SECOND-longest: Sat 60 and Sun 59 is no long-run day.
        typical = durations[1] if len(durations) > 1 else 0
        if durations[0] > 0 and durations[0] >= LONG_RUN_MARGIN * typical:
            longest = max(runs, key=lambda a: int(a.get("duration_s") or 0))
            long_weekdays[longest["day"].weekday()] += 1
        other_weekdays.update({a["day"].weekday() for a in acts
                               if not _is_run(a)
                               and not any(t in str(a.get("type") or "") for t in _NOT_TRAINING)
                               and int(a.get("duration_s") or 0) >= OTHER_SPORT_MIN_S})
    evidence: dict[str, str] = {}

    ordered = sorted(run_days_per_week)
    # A half rounds UP, always: `round` sends 2.5 to 2 and 3.5 to 4.
    median = ordered[len(ordered) // 2] if len(ordered) % 2 else \
        int((ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2 + 0.5)
    days_per_week = max(2, min(planning.MAX_SESSIONS_PER_WEEK, median))
    evidence["days"] = (f"{median} run day(s) in a typical week (median {span})"
                        + (f", planned as {days_per_week}" if days_per_week != median else ""))

    preferred = tuple(planning.WEEKDAYS[w] for w, c in
                      sorted(run_weekdays.items(), key=lambda kv: (-kv[1], kv[0]))
                      if c / n >= PREFERRED_SHARE)
    # Every other day lands on every weekday about half the time: that is a
    # rotating rhythm, not weekdays of one's own, and "your days" would be all
    # of them.
    if len(preferred) > days_per_week + 1:
        evidence["rhythm"] = f"your runs fall on {len(preferred)} different weekdays ({span})"
        preferred = ()
    if preferred:
        evidence["preferred"] = "you run on " + ", ".join(
            f"{d} ({run_weekdays[planning.WEEKDAYS.index(d)]}/{n})" for d in preferred)

    long_run_day = None
    top, count = (long_weekdays.most_common(1) or [(None, 0)])[0]
    if top is not None and count / n >= LONG_DAY_SHARE:
        long_run_day = planning.WEEKDAYS[top]
        evidence["long"] = f"your longest run fell on {long_run_day} in {count} {span}"

    avoid: set[str] = set()
    sport_days = [w for w, c in sorted(other_weekdays.items()) if c / n >= OTHER_SPORT_SHARE]
    for w in sport_days:
        avoid.add(planning.WEEKDAYS[w])
        avoid.add(planning.WEEKDAYS[(w + 1) % 7])
    if sport_days:
        evidence["other"] = "another sport on " + ", ".join(
            f"{planning.WEEKDAYS[w]} ({other_weekdays[w]}/{n})" for w in sport_days)
    return Habits(days_per_week=days_per_week, long_run_day=long_run_day, preferred=preferred,
                  avoid_hard=frozenset(avoid), sport_days=tuple(planning.WEEKDAYS[w] for w in sport_days),
                  evidence=evidence, weeks=n)


def no_hard_after(sport_days) -> frozenset[str]:
    """Another sport keeps a quality run off its day and the day after."""
    out: set[str] = set()
    for d in sport_days:
        i = planning.WEEKDAYS.index(d)
        out |= {d, planning.WEEKDAYS[(i + 1) % 7]}
    return frozenset(out)


@dataclass(frozen=True)
class Week:
    """Where a week's runs may go: the athlete's settings (`profile.py`) where
    they set one, the history where they did not."""
    preferred: tuple[str, ...] = ()
    avoid_hard: frozenset[str] = frozenset()
    off_days: frozenset[str] = frozenset()
    #: Assumption lines for the values that came from the history.
    preferred_line: str | None = None
    avoid_line: str | None = None


def week(profile: dict, habits: Habits) -> Week:
    """The profile beats the history, key by key; a day off is never one of
    the athlete's days, whichever source named it."""
    off = frozenset(profile.get("off_days") or ())
    pref_line = avoid_line = None
    if profile.get("run_days") is not None:
        preferred = tuple(profile["run_days"])
    elif habits.known and habits.preferred:
        preferred = habits.preferred
        pref_line = f"easy runs on your days first - {habits.evidence['preferred']}"
    else:
        preferred = ()
    if profile.get("sport_days") is not None:
        avoid = no_hard_after(profile["sport_days"])
    elif habits.known and habits.avoid_hard:
        avoid = habits.avoid_hard
        avoid_line = (f"no quality run on {', '.join(sorted(avoid))} - {habits.evidence['other']}, "
                      f"that day and the day after")
    else:
        avoid = frozenset()
    dropped = [d for d in preferred if d in off]
    if pref_line and dropped:
        # None of their days left: there is nothing to put first.
        pref_line = (None if len(dropped) == len(preferred) else
                     pref_line + f"; {', '.join(dropped)} "
                                 f"{'is a day' if len(dropped) == 1 else 'are days'} off now")
    return Week(preferred=tuple(d for d in preferred if d not in off), avoid_hard=avoid,
                off_days=off, preferred_line=pref_line, avoid_line=avoid_line)


@dataclass(frozen=True)
class Setting:
    value: object
    #: "you" (the profile), "history" (habits.py), "default" (the planner's own)
    source: str
    evidence: str
    #: The history's value moved by a day off: the evidence says so.
    adjusted: bool = False


def effective(profile: dict, habits: Habits) -> dict[str, Setting]:
    """The week the planner goes by, key by key, with where each value comes
    from - the one reading the You tab, the facts and the coach all show, so
    none of them names a long-run day the planner has moved off a day off."""
    off = [d for d in planning.WEEKDAYS if d in set(profile.get("off_days") or ())]
    days, long_day, _ = resolve(days_per_week=None, long_run_day=None, profile=profile,
                                habits=habits)
    out: dict[str, Setting] = {}
    # The same cap `planning.build_week` applies: no more runs than free days.
    free = 7 - len(off)
    capped = f"; {', '.join(off)} {'is a day' if len(off) == 1 else 'are days'} off" if days > free else ""
    shown = min(days, free)
    if profile.get("days_per_week") is not None:
        out["days_per_week"] = Setting(shown, "you", "set by you")
    elif habits.known:
        out["days_per_week"] = Setting(shown, "history", habits.evidence["days"] + capped,
                                       adjusted=bool(capped))
    else:
        out["days_per_week"] = Setting(shown, "default", "the planner's default until your history "
                                       "shows it" + capped)
    if profile.get("long_run_day") is not None:
        out["long_run_day"] = Setting(long_day, "you", "set by you")
    elif habits.known and habits.long_run_day:
        moved = habits.long_run_day in off
        out["long_run_day"] = Setting(
            long_day, "history",
            f"your usual {habits.long_run_day} is a day off now" if moved else habits.evidence["long"],
            adjusted=moved)
    else:
        out["long_run_day"] = Setting(long_day, "default",
                                      "the planner's default until your history shows it")
    w = week(profile, habits)
    if profile.get("run_days") is not None:
        out["run_days"] = Setting(list(w.preferred), "you", "set by you")
    elif habits.known and habits.preferred:
        dropped = [d for d in habits.preferred if d in off]
        out["run_days"] = Setting(
            [d for d in planning.WEEKDAYS if d in w.preferred], "history",
            habits.evidence["preferred"] + (f"; {', '.join(dropped)} "
                                            f"{'is a day' if len(dropped) == 1 else 'are days'} off now"
                                            if dropped else ""),
            adjusted=bool(dropped))
    out["off_days"] = Setting(off, "you" if off else "default", "set by you" if off else "")
    if profile.get("sport_days") is not None:
        out["sport_days"] = Setting(list(profile["sport_days"]), "you", "set by you")
    elif habits.known and habits.sport_days:
        out["sport_days"] = Setting(list(habits.sport_days), "history", habits.evidence["other"])
    return out


#: Where the long run goes when its usual day is a day off: the weekend first.
_LONG_FALLBACK = ("sun", "sat", "fri", "thu", "wed", "tue", "mon")


def resolve(*, days_per_week: int | None, long_run_day: str | None, profile: dict,
            habits: Habits) -> tuple[int, str, list[str]]:
    """Days and long-run day for a week, and the lines that say where each came
    from: the caller's argument, else the profile, else the history, else the
    built-in default. An argument and a profile value need no line - the
    athlete said so; the history and the default are assumptions. A history
    or default long-run day the athlete has since made a day off gives way."""
    lines: list[str] = []
    off = set(profile.get("off_days") or ())
    if days_per_week is None:
        days_per_week = profile.get("days_per_week")
    if days_per_week is None and habits.known:
        days_per_week = habits.days_per_week
        lines.append(f"{days_per_week} running days - from your history: {habits.evidence['days']}")
    if days_per_week is None:
        days_per_week = planning.DEFAULT_DAYS_PER_WEEK
        lines.append(f"{days_per_week} running days - no days_per_week in the profile "
                     f"(runcoach profile --days-per-week N)")
    if long_run_day is None:
        long_run_day = profile.get("long_run_day")
    if long_run_day is None and habits.known and habits.long_run_day:
        if habits.long_run_day in off:
            usual = next(d for d in _LONG_FALLBACK if d not in off)
            lines.append(f"long run on {usual} - your usual {habits.long_run_day} is a day off now")
            long_run_day = usual
        else:
            long_run_day = habits.long_run_day
            lines.append(f"long run on {long_run_day} - {habits.evidence['long']}")
    if long_run_day is None:
        long_run_day = next(d for d in (planning.DEFAULT_LONG_RUN_DAY, *_LONG_FALLBACK)
                            if d not in off)
        lines.append(f"long run on {long_run_day} - no long_run_day in the profile "
                     f"(runcoach profile --long-run-day sun)")
    return int(days_per_week), str(long_run_day), lines
