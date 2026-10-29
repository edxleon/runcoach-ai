"""What the coach has learned about the athlete from their data - layer 1 of
the athlete's memory, computed in code every time it is read.

Stored facts go stale: "you run too much zone 3" is wrong four weeks after
the athlete fixed it, and it keeps steering every answer. So nothing here is
stored. Each fact is the value the planner and the snapshot already compute,
with the evidence it rests on and the day it is as of - and a CHANGE line
when the value moved and has held since ("long run on sat since about 3 weeks
ago, before: sun"). A change counts only when the new value held at two weekly
anchors in a row; one odd week is not a new habit.

The facts go to three places: the "You" tab, the athlete block of every card
prompt (`agent.build_prompt`), and the MCP tool `get_athlete_profile`. They
are context for the coach, not decisions: today's numbers still come from the
tools, and the verdict is still `logic.py`'s.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date, timedelta

from . import paths
from .store import Store

#: How many weekly anchors back a change is looked for.
CHANGE_WEEKS = 8
#: Zone 3 share is tracked in steps of this many percent: 41 % and 43 % are
#: the same pattern, 32 % and 45 % are not.
GREY_STEP = 10


@dataclass(frozen=True)
class Fact:
    key: str
    label: str
    value: str
    evidence: str
    source: str                 # "your history", "Garmin", "the app"
    as_of: str
    change: str | None = None

    def line(self) -> str:
        text = f"- {self.label}: {self.value} - {self.evidence}"
        return text + (f" ({self.change})" if self.change else "")


def _change(series: list) -> str | None:
    """`series[0]` is now, `series[k]` the value k weeks ago. A change line when
    the value now held at the last two anchors, and the value before it held at
    two anchors too - else one odd week would be reported as a new habit."""
    if len(series) < 3 or series[0] is None or series[1] != series[0]:
        return None
    for k in range(2, len(series)):
        if series[k] != series[0]:
            before = series[k]
            if before is None or (k + 1 < len(series) and series[k + 1] != before):
                return None
            return f"since about {k - 1} week{'s' if k > 2 else ''} ago; before: {before}"
    return None


def _grey_bucket(dist: dict) -> str | None:
    pct = dist.get("moderate_pct")
    if pct is None or not dist.get("with_detail"):
        return None
    low = int(pct // GREY_STEP) * GREY_STEP
    return f"{low}-{low + GREY_STEP} % in zone 3"


def _running_weeks(store: Store, today: date, weeks: int = 8) -> int:
    monday = today - timedelta(days=today.weekday())
    days = store.run_days(monday - timedelta(weeks=weeks), monday - timedelta(days=1))
    return len({d - timedelta(days=d.weekday()) for d in days})


def learned(store: Store, today: date, snap: dict | None = None) -> list[Fact]:
    """The facts as of `today`. `snap` is the snapshot when the caller already
    has one (the web server does); else it is assembled here."""
    from . import plan, snapshot

    snap = snap if snap is not None else snapshot.assemble(store, today=today)
    anchors = [today - timedelta(weeks=k) for k in range(CHANGE_WEEKS + 1)]
    habits_at = [plan.athlete_habits(store, a) for a in anchors]
    now = habits_at[0]
    facts: list[Fact] = []
    iso = today.isoformat()

    if now.known:
        facts.append(Fact("days", "Running days", f"{now.days_per_week} a week",
                          now.evidence["days"], "your history", iso,
                          _change([h.days_per_week if h.known else None for h in habits_at])))
        if now.preferred:
            facts.append(Fact("weekdays", "Your days", ", ".join(now.preferred),
                              now.evidence["preferred"], "your history", iso,
                              _change([(", ".join(sorted(h.preferred)) or "no fixed weekdays")
                                       if h.known else None for h in habits_at])))
        elif "rhythm" in now.evidence:
            facts.append(Fact("weekdays", "Your days", "no fixed weekdays",
                              now.evidence["rhythm"], "your history", iso))
        if now.long_run_day:
            facts.append(Fact("long_run", "Long run", now.long_run_day, now.evidence["long"],
                              "your history", iso,
                              _change([h.long_run_day if h.known else None for h in habits_at])))
        if now.avoid_hard:
            facts.append(Fact("other_sport", "Other sport", now.evidence["other"].removeprefix(
                "another sport on "), "no quality run that day or the day after",
                "your history", iso))
    else:
        facts.append(Fact("days", "Running days", "not known yet", now.evidence.get(
            "weeks", "too few weeks with a run"), "your history", iso))

    # No change line: 7 and 8 of 8 weeks are the same habit.
    facts.append(Fact("consistency", "Consistency",
                      f"{_running_weeks(store, today)} of the last 8 weeks with a run",
                      "Monday-weeks, the running week not counted", "your history", iso))

    dists = [store.get_intensity_distribution(a - timedelta(days=27), a) for a in anchors]
    d0 = dists[0]
    if d0.get("with_detail"):
        facts.append(Fact(
            "intensity", "Intensity (28 days)",
            f"easy {round(d0['easy_pct'])} %, zone 3 {round(d0['moderate_pct'])} %, "
            f"hard {round(d0['hard_pct'])} % (zone 5 {round(d0['vo2max_pct'])} %)",
            f"{d0['with_detail']} of {d0['total_runs']} runs with zone detail; a polarised week is "
            f"~80 % easy", "your history", iso, _change([_grey_bucket(d) for d in dists])))

    vo2 = snap.get("vo2max") or {}
    if vo2.get("current") is not None:
        trend = vo2.get("change_56d")
        meas = vo2.get("measurement") or {}
        value = f"{vo2['current']}" + (f" ({trend:+} in 8 weeks)" if trend is not None else "")
        evidence = (f"last new value from a run {vo2['last_change_days']} days ago"
                    if vo2.get("last_change_days") is not None else "no new value from a run on record")
        if meas.get("due"):
            evidence += "; a measurement is due"
        facts.append(Fact("vo2max", "VO2max", value, evidence, "Garmin", iso))

    balances = [store.latest_load_balance(a) for a in anchors]
    b0 = balances[0]
    if b0 and b0.get("load_balance"):
        facts.append(Fact("load_focus", "Garmin's load focus",
                          b0["load_balance"].lower().replace("_", " "),
                          f"Garmin's 4-week estimate as of {b0['day']} - its opinion, checked "
                          f"against the minutes above", "Garmin", str(b0["day"]),
                          _change([(b or {}).get("load_balance") for b in balances])))

    readiness = store.get_readiness(today) or {}
    sig = readiness.get("signals") or {}
    if sig.get("resting_hr_baseline") is not None:
        facts.append(Fact("resting_hr", "Resting HR baseline", f"{sig['resting_hr_baseline']} bpm",
                          "mean of the 27 nights before the last one", "Garmin", iso))
    zones = snap.get("zones") or {}
    if zones.get("max_hr_observed"):
        facts.append(Fact("max_hr", "Highest heart rate seen", f"{zones['max_hr_observed']} bpm",
                          f"on {zones.get('max_hr_day') or '?'}", "your history", iso))
    if zones.get("source"):
        facts.append(Fact("zones", "Heart-rate zones", zones["source"],
                          f"zone 4 from {zones.get('z4_low')} bpm, zone 5 from "
                          f"{zones.get('z5_low')} bpm", "Garmin", str(zones.get("as_of_day") or iso)))
    return facts


def render_text(facts: list[Fact], cap: int) -> str:
    """The facts as lines for a prompt, cut at a whole fact: what does not fit
    is named, never half a sentence."""
    out, used = [], 0
    for i, f in enumerate(facts):
        line = f.line()
        if used + len(line) + 1 > cap:
            out.append(f"(+{len(facts) - i} more on the You tab)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def fingerprint(facts: list[Fact]) -> str:
    """Changes when a fact's VALUE changes - the trigger of the coach's read."""
    return hashlib.sha1("\n".join(f"{f.key}={f.value}" for f in facts).encode()).hexdigest()[:16]


_CACHE: dict = {}


def _stamp(path) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def cached(store: Store, today: date, snap: dict | None = None) -> list[Fact]:
    """`learned`, recomputed only when something it reads can have changed: the
    database (and its WAL), the profile, the account settings, or the day."""
    key = (store.path, _stamp(store.path), _stamp(store.path + "-wal"), today,
           _stamp(paths.profile_path()), _stamp(paths.account_path()))
    hit = _CACHE.get(store.path)
    if hit and hit[0] == key:
        return hit[1]
    result = learned(store, today, snap)
    _CACHE[store.path] = (key, result)
    return result
