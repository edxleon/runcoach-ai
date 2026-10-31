"""The deterministic core: readiness verdict, today's decision, interval
structure, run classification. Pure functions — no database, no network, no
LLM. The coach may phrase a recommendation, but the verdict comes from here,
so the app and the agent can never disagree about the facts."""

from __future__ import annotations

from datetime import date, timedelta

# ── Readiness ────────────────────────────────────────────────────────────────

#: ACWR bands (zones.md): above ELEVATED the load axis dampens a day to EASY and
#: holds a week flat; above SPIKE it rests the day and unloads the week. One
#: pair of numbers for the day rule here and the week rule in `planning.brake`.
ACWR_ELEVATED = 1.3
ACWR_SPIKE = 1.5

#: Training monotony (Foster 1998): a week's mean daily load over its standard
#: deviation, rest days counting as 0. Foster's own thresholds were each
#: athlete's; 2.0 is the warning line practitioners use. It only ever holds a
#: week, and only a heavy one (`planning.monotony_brake`) - the pairing Foster
#: found behind most illnesses was high monotony AND high load, not either alone.
MONOTONY_HIGH = 2.0
#: A week of identical days has no spread and so no finite monotony.
MONOTONY_MAX = 10.0


def monotony(daily_loads: list[float]) -> dict | None:
    """Monotony and strain of seven daily loads, oldest first: `monotony` =
    mean / standard deviation (population SD over the seven days, a rest day
    is a 0), `strain` = the week's load x monotony. `None` without seven
    values or without any load - an empty week is not a monotonous one."""
    if len(daily_loads) != 7:
        return None
    total = float(sum(daily_loads))
    if total <= 0:
        return None
    mean = total / 7
    sd = (sum((x - mean) ** 2 for x in daily_loads) / 7) ** 0.5
    mono = MONOTONY_MAX if sd == 0 else min(MONOTONY_MAX, mean / sd)
    return {"monotony": round(mono, 2), "load": round(total), "strain": round(total * mono)}


def readiness_verdict(
    *, hrv_status, sleep_score, body_battery_high, acwr, resting_hr, resting_hr_baseline,
    acwr_source=None, days_since_hard=None,
) -> tuple[str, list[str], list[dict]]:
    """Rule-based daily verdict GO / EASY / REST from recovery and load signals.
    Transparent and conservative on purpose (rather EASY than overtraining).

    `acwr_source` ('garmin' | 'computed' | None) controls how sharply the load
    axis counts: a self-computed ACWR (plain sum ratio) is not risk-equivalent
    to Garmin's EWMA ratio, so it may only dampen (EASY), never drive REST.

    Returns `(verdict, reasons, reason_flags)`. `reason_flags` carries the rank
    (`rest`/`easy`) and a stable `key` per signal, so a UI can colour and sort
    without matching on prose."""
    flags: list[tuple[str, str, str]] = []   # (level, key, text)

    # Garmin's own word for "no HRV measured" is the string "NONE". The ingest
    # maps it to NULL, but rows written before that fix still carry it, and a
    # present-looking label would count as a signal and defeat the thin-data
    # guard below — same recovery state, GO instead of EASY.
    if hrv_status == "NONE":
        hrv_status = None

    if hrv_status in ("LOW", "POOR"):
        flags.append(("rest", "hrv", f"HRV {hrv_status}"))
    elif hrv_status == "UNBALANCED":
        flags.append(("easy", "hrv", "HRV unbalanced"))

    if sleep_score is not None:
        if sleep_score < 50:
            flags.append(("rest", "sleep_score", f"Sleep score {sleep_score}"))
        elif sleep_score < 65:
            flags.append(("easy", "sleep_score", f"Sleep score {sleep_score}"))

    if body_battery_high is not None:
        if body_battery_high < 40:
            flags.append(("rest", "body_battery", f"Body Battery only {body_battery_high}"))
        elif body_battery_high < 60:
            flags.append(("easy", "body_battery", f"Body Battery {body_battery_high}"))

    if acwr is not None:
        if acwr > ACWR_SPIKE:
            if acwr_source == "computed":
                flags.append(("easy", "acwr", f"ACWR {acwr} (high, computed - uncertain)"))
            else:
                flags.append(("rest", "acwr", f"ACWR {acwr} (>1.5 = injury risk)"))
        elif acwr > ACWR_ELEVATED:
            flags.append(("easy", "acwr", f"ACWR {acwr} (elevated)"))
        elif acwr < 0.8:
            # Do not call it detraining right after a hard session: strength and
            # anaerobic work carry little HR-based load, so ACWR reads low even
            # though a real stimulus was set.
            if days_since_hard is None or days_since_hard > 2:
                flags.append(("easy", "acwr", f"ACWR {acwr} (detraining range - stimulus missing)"))

    if resting_hr is not None and resting_hr_baseline is not None:
        delta = round(resting_hr - resting_hr_baseline)
        if delta >= 7:
            flags.append(("rest", "resting_hr", f"Resting HR +{delta} above baseline"))
        elif delta >= 4:
            flags.append(("easy", "resting_hr", f"Resting HR +{delta} above baseline"))

    present = sum(x is not None for x in (hrv_status, sleep_score, body_battery_high))
    if resting_hr is not None and resting_hr_baseline is not None:
        present += 1

    if any(lvl == "rest" for lvl, _, _ in flags):
        verdict = "REST"
    elif any(lvl == "easy" for lvl, _, _ in flags):
        verdict = "EASY"
    else:
        verdict = "GO"
    reasons = [text for _, _, text in flags] or ["Recovery in the green"]
    reason_flags = [{"level": lvl, "key": key, "text": text} for lvl, key, text in flags]

    # Confidence guard: a GO from 0–1 signals is deceptively green (watch off at
    # night, fresh database). Downgrade and say why instead of faking certainty.
    if verdict == "GO" and present < 2:
        verdict = "EASY"
        reasons = [f"thin data (only {present} signal(s)) - no clear green"]
        reason_flags = [{"level": "easy", "key": "data", "text": reasons[0]}]

    return verdict, reasons, reason_flags


def decide_today(
    verdict: str | None, *, days_since_hard: int | None, hard_min: int, target_min: int,
    quality_min: int | None = None, quality_s: int | None = None,
    week_start: str | None = None, stimulus: dict | None = None,
    comeback_state: dict | None = None, today: date | None = None,
    check_ins: dict | None = None,
) -> dict:
    """`_decide_today`, plus WHICH hard session when the day is hard.

    `stimulus` is `next_stimulus`'s answer; it rides along on the `hard`
    branch only, as a recommendation under the decision, so every surface
    that reads the decision dict - the Today tab, the readiness tool, the
    coach - names the same session. The decision itself is untouched: the
    light and the week's share decide hard/easy/rest, the stimulus never does.

    `check_ins` is `check_in_state`'s answer: what the athlete said about
    themselves. Its own step HERE, after the light, and not a signal inside
    `readiness_verdict` - the athlete says it in the morning, often before
    the watch has synced a value, and a day without today's values has no
    verdict to put a flag on. So an open `ill` or `pain` rests the day even
    when the light cannot be called."""
    out = _decide_today(verdict, days_since_hard=days_since_hard, hard_min=hard_min,
                        target_min=target_min, quality_min=quality_min, quality_s=quality_s,
                        week_start=week_start)
    if check_ins and today is not None and _apply_check_ins(out, check_ins, today):
        return out
    # After a pause the first runs back are easy, whatever the light and the
    # week's share say: "below its hard share" after three weeks off is a week
    # of nothing, not a reason for intervals. The same threshold that makes
    # the planner's return week, so the day and the week cannot disagree.
    if out["decision"] == "hard" and in_comeback(comeback_state) and today is not None:
        out.update(decision="easy", reason="pause",
                   sentence=(f"Easy today - {_comeback_words(comeback_state, today)}; the first "
                             f"runs back are easy, the hard sessions come back the week after."))
    if stimulus and out["decision"] == "hard":
        out["stimulus"] = {"kind": stimulus.get("kind"), "reason": stimulus.get("reason")}
    return out


# ── The athlete's own word ───────────────────────────────────────────────────

#: What the athlete can say about themselves - the Today tab's buttons, the
#: `check_in` tool. The watch sees an illness only once HRV reacts and never
#: sees a sore calf; these are the athlete's word for both.
CHECK_IN_KINDS = ("ill", "pain", "sore", "stressed")
#: The two that stop the run: the day is REST while one is open, whatever the
#: light says. Running through a cold or a pain is the mistake a coach exists
#: to prevent; the app does not try to tell a head cold from a fever.
STOPS_RUNNING = ("ill", "pain")
#: How long a check-in holds without a new word, counted from the last day it
#: was said. A cold or a pain said once is most likely still there the next
#: two mornings; a sore leg (muscle soreness peaks 24-72 h after the session
#: and fades within days) gets two. Past that the app cannot know, and an
#: open state nobody confirms would keep a well athlete off their feet - so it
#: runs out, and for `ill` and `pain` the easy days below follow as if the
#: athlete had said it was over.
CHECK_IN_LAPSE_DAYS = {"ill": 3, "pain": 3, "sore": 2, "stressed": 3}
#: After `ill` or `pain`: as many days without a hard session as it was open
#: (the practitioners' "one easy day for every day sick"), at most a week -
#: past that the pause itself makes the return week (`comeback`).
CHECK_IN_AFTER_MAX_DAYS = 7
#: The words for each kind in a sentence: "you said you are ill".
CHECK_IN_WORDS = {"ill": "ill", "pain": "in pain", "sore": "sore", "stressed": "stressed"}


def _day(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def check_in_end(kind: str, last_said, over=None) -> date:
    """The first day a check-in no longer holds: the day the athlete said it
    was over, or the day it runs out without a new word."""
    return _day(over) if over else _day(last_said) + timedelta(days=CHECK_IN_LAPSE_DAYS[kind])


def check_in_continues(kind: str, since, last_said, over, day: date, first: date) -> bool:
    """Does `kind` said on `day` (beginning `first`) continue this episode
    rather than start a new one? Yes while it holds, when the new beginning
    reaches back into it, on the day it ran out or was ended - and for `ill`
    and `pain` while its easy days are still running. A cold that ran out
    without a word and is said again is the same cold: as a new one its days
    before were dropped, and too few easy days followed six days ill."""
    end = check_in_end(kind, last_said, over)
    if first < end or day <= end:
        return True
    if kind not in STOPS_RUNNING:
        return False
    days = max(1, (end - _day(since)).days)
    return day < end + timedelta(days=min(days, CHECK_IN_AFTER_MAX_DAYS))


def check_in_state(rows: list[dict], today: date) -> dict | None:
    """What the athlete's check-ins mean today: `open` (each kind that holds
    today, newest first) and `after` (the easy days behind an `ill` or `pain`
    that ended), or `None` when nothing applies.

    `rows`: dicts with `kind`, `since`, `last_said`, `over` (None while open)
    and `note`. A row that has not started yet (said for a later day) is
    ignored."""
    open_: dict[str, dict] = {}
    after: dict | None = None
    for r in rows:
        kind = r.get("kind")
        if kind not in CHECK_IN_KINDS:
            continue
        since, last = _day(r["since"]), _day(r["last_said"])
        if since > today:
            continue
        end = check_in_end(kind, last, r.get("over"))
        if since <= today < end:
            item = {"kind": kind, "since": since.isoformat(), "last_said": last.isoformat(),
                    "note": r.get("note"), "until": end.isoformat(),
                    "over": _day(r["over"]).isoformat() if r.get("over") else None}
            if kind not in open_ or open_[kind]["since"] < item["since"]:
                open_[kind] = item
        elif kind in STOPS_RUNNING and end <= today:
            days = max(1, (end - since).days)
            until = end + timedelta(days=min(days, CHECK_IN_AFTER_MAX_DAYS))
            if today < until and (after is None or after["until"] < until.isoformat()):
                after = {"kind": kind, "since": since.isoformat(), "ended": end.isoformat(),
                         "days": days, "until": until.isoformat(), "ran_out": not r.get("over")}
    if not open_ and after is None:
        return None
    return {"open": sorted(open_.values(), key=lambda c: c["since"], reverse=True),
            "after": after}


def check_in_hard_from(state: dict | None, today: date) -> date | None:
    """The first day the athlete's word allows a hard session again, as far as
    `today` can see: after an `ill` or `pain` that ended, the end of its easy
    days; for one that still holds, the end of the easy days had the athlete
    said it was over tomorrow - the earliest the day rule could allow one; for
    `sore` and `stressed`, the day they run out. `None` when nothing holds a
    hard session back. The week planner reads it, so a week planned now never
    gives the watch a session the day's decision is bound to call off."""
    if not state:
        return None
    days = []
    if state.get("after"):
        days.append(_day(state["after"]["until"]))
    tomorrow = today + timedelta(days=1)
    for c in state.get("open") or []:
        if c["kind"] in STOPS_RUNNING:
            held = max(1, (tomorrow - _day(c["since"])).days)
            days.append(tomorrow + timedelta(days=min(held, CHECK_IN_AFTER_MAX_DAYS)))
        else:
            days.append(_day(c["until"]))
    return max(days) if days else None


#: `check_in_blocks`' answer when nothing may be run on that day at all.
NO_RUN_TODAY = "no run today"


def check_in_blocks(state: dict | None, today: date, day: date, *, hard: bool) -> str | None:
    """Why the athlete's word allows no session (`hard` or not) on `day`, as
    `today` sees it, or `None` when it allows one. THE gate: a single
    proposal and the week planner both ask it, so neither can file what the
    other refuses. Ill or in pain today: no run today at all. A hard session
    before `check_in_hard_from`: none."""
    if not state:
        return None
    if day == today and any(c["kind"] in STOPS_RUNNING for c in state.get("open") or []):
        return NO_RUN_TODAY
    first = check_in_hard_from(state, today)
    if hard and first is not None and day < first:
        return f"no hard session before {first:%a %d %b}"
    return None


def check_in_words(state: dict | None, today: date) -> str | None:
    """What holds a hard session back, as the planner's lines say it: "you
    said you are in pain (since Fri 12 Jun)", "you were ill for 4 days"."""
    if not state:
        return None
    opened = state.get("open") or []
    stop = next((c for c in opened if c["kind"] in STOPS_RUNNING), None)
    if stop:
        return f"you said you are {CHECK_IN_WORDS[stop['kind']]}{_since_words(stop, today)}"
    a = state.get("after")
    if a:
        return (f"you were {CHECK_IN_WORDS[a['kind']]} for {a['days']} "
                f"day{'s' if a['days'] != 1 else ''} until {_day(a['ended']):%a %d %b}")
    if opened:
        return f"you said you are {CHECK_IN_WORDS[opened[0]['kind']]}{_since_words(opened[0], today)}"
    return None


def _since_words(item: dict, today: date) -> str:
    since = _day(item["since"])
    return "" if since >= today else f" (since {since:%a %d %b})"


def _apply_check_ins(out: dict, state: dict, today: date) -> bool:
    """`decide_today`'s step for the athlete's word, in place. True when it
    decided the day outright (an open `ill` or `pain`): nothing after it may
    turn that back. The others only take a hard session away, never add one."""
    opened = state.get("open") or []
    stop = next((c for c in opened if c["kind"] in STOPS_RUNNING), None)
    if stop:
        said = "you are ill" if stop["kind"] == "ill" else "something hurts"
        out.update(decision="rest", reason=f"check-in {stop['kind']}",
                   sentence=(f"Rest today - you said {said}{_since_words(stop, today)}. No run "
                             f"until it is over, and the first days back are easy."))
        return True
    if out["decision"] != "hard":
        return False
    soft = next((c for c in opened if c["kind"] not in STOPS_RUNNING), None)
    if soft:
        why = ("your legs are sore" if soft["kind"] == "sore" else "you are stressed")
        tail = ("the hard session waits until they are not" if soft["kind"] == "sore"
                else "stress slows recovery, so the hard session waits")
        out.update(decision="easy", reason=f"check-in {soft['kind']}",
                   sentence=f"Easy today - you said {why}{_since_words(soft, today)}; {tail}.")
        return False
    after = state.get("after")
    if after:
        was = "ill" if after["kind"] == "ill" else "in pain"
        out.update(decision="easy", reason=f"after {after['kind']}",
                   sentence=(f"Easy today - you were {was} for {after['days']} "
                             f"day{'s' if after['days'] != 1 else ''}; as many easy days follow "
                             f"before the hard sessions come back, from "
                             f"{_day(after['until']):%a %d %b}."))
    return False


def _decide_today(
    verdict: str | None, *, days_since_hard: int | None, hard_min: int, target_min: int,
    quality_min: int | None = None, quality_s: int | None = None,
    week_start: str | None = None,
) -> dict:
    """Referee between the daily verdict and the weekly hard-share target —
    ONE decision for today: `hard` | `easy` | `rest` | `unknown`.

    The app has two cards that can contradict each other: the readiness light
    pushes towards rest, the weekly-share card (week is below its hard-minutes
    target) pushes towards more hard work. **Recovery wins.** Rest flags mean
    injury/overload risk, and catching up only makes that worse; open hard
    minutes stay open until the light is green again.

    TWO numbers, because one scalar cannot referee polarisation:

    * `hard_min` = minutes ABOVE THE EASY ZONES (Z3 + Z4 + Z5) of the current
      week, against `target_min` (a share of the same week's measured zone
      time). This is the LOAD side — it decides whether there is room for more.
      Z3 belongs in it: those minutes cost recovery, and leaving them out made
      the grey-zone athlete the one most likely to be told to go hard again.
    * `quality_min` = minutes above threshold (Z4 + Z5). This is the STIMULUS
      side. Counting Z3 towards the load told an athlete with 60 min of Z3 and
      nothing above threshold that "the hard share is reached, no further hard
      stimulus needed" — absolving the exact pattern `zones.md` calls the
      classic recreational mistake. A full load bucket does not mean the week
      contained a stimulus.

    The quality test is a FLOOR, not a ratio: anything finer would have to take a
    side in the polarised-vs-pyramidal question, and a pyramidal week with a real
    Z4 block is not a mistake. Under a minute above threshold across a whole week
    is unambiguous in every model - it is HR drift on a hill, not a session.

    `target_min == 0` means: no measured zone time in this week yet.
    `quality_min is None` means the caller did not supply it — the rule then
    behaves as before and says nothing about quality."""
    week = {"hard_min": int(hard_min), "target_min": int(target_min), "week_start": week_start}
    if quality_min is not None:
        # Carried through to every surface: the Today tab, the Trend chart and
        # the coach's decision block all read this dict, and a chart that shows
        # a different numerator from the sentence beside it is the one failure
        # this whole layer exists to prevent.
        week["quality_min"] = int(quality_min)

    if verdict not in ("GO", "EASY", "REST"):
        return {"decision": "unknown", "sentence": "Not enough data for a verdict.",
                "reason": "no verdict", "week": week}

    # Whether the week is really behind is decided by the numbers, not by the
    # rule branch — a sentence that contradicts the number printed next to it
    # costs more credibility than it adds in explanation.
    behind = target_min > 0 and hard_min < target_min
    # "No quality at all this week" - the stimulus bucket is empty although the
    # load bucket is not. Computed ONCE here so every branch can speak about it:
    # the first version asked the question only on a green light, so an athlete
    # with 60 min of Z3 and nothing above threshold was still told, on an amber
    # day, that "the week's hard share is already reached anyway" - the exact
    # sentence the quality axis was added to abolish.
    # Seconds, against a FLOOR - not "> 0". Both extremes were wrong once:
    # asking the rounded minute made 29 s of drift read as "none above
    # threshold", and then asking for strictly zero seconds put a cliff at one
    # second, so a single beat of HR drift on a hill silently switched the
    # grey-zone correction off while the sentence the athlete reads stayed
    # byte-identical. A minute is the smallest amount that is a stimulus rather
    # than noise, and it is the unit every surface displays.
    quality = quality_s if quality_s is not None else (
        quality_min * 60 if quality_min is not None else None)
    no_quality = (quality is not None and quality < QUALITY_FLOOR_SECONDS
                  and hard_min > 0 and not behind)
    # ...and a CEILING on the load. The quality branch may prescribe hard work,
    # so it needs the same restraint as every other rule that can: at double the
    # week's target or more, the honest reading is not "you are missing a
    # stimulus" but "you are doing far too much of the wrong thing", and adding
    # a hard session to that is the one case where this rule gave worse advice
    # than the version before it.
    far_over = target_min > 0 and hard_min >= 2 * target_min

    if verdict == "REST":
        sentence = "No hard session today - the light says rest."
        if behind:
            sentence = ("No hard session today - the light says rest, "
                        "even though the week is below its hard share.")
        return {"decision": "rest", "sentence": sentence, "reason": "verdict REST", "week": week}

    if verdict == "EASY":
        sentence = ("Easy only today - the light says take it easy; the week's "
                    "hard share is already reached anyway.")
        if no_quality and far_over:
            # The SAME reading the green branch gives this week. Without the
            # `far_over` arm here, one week was "adding a hard session on top is
            # not the fix" on a green day and "the quality session is still
            # open" on an amber one - two diagnoses of one week, decided by
            # today's light.
            sentence = ("Easy only today - the light says take it easy. Note for the "
                        "week: it is far over its share of non-easy minutes and every "
                        "one of them is in zone 3. What that asks for is easier easy "
                        "days, not another session.")
        elif no_quality:
            sentence = ("Easy only today - the light says take it easy. Note for the "
                        "week: the minutes above easy are there, but all of them are "
                        "in zone 3 and none above threshold - the quality session is "
                        "still open, for a day when the light is green.")
        elif behind:
            sentence = ("Easy only today - the week's hard minutes stay open and are "
                        "made up once the light is green again.")
        elif target_min <= 0:
            sentence = "Easy only today - the light says take it easy."
        return {"decision": "easy", "sentence": sentence, "reason": "verdict EASY", "week": week}

    # GO from here on. Hard stimuli sit HARD_SPACING_DAYS apart, regardless of
    # the weekly state.
    if days_since_hard is not None and days_since_hard < HARD_SPACING_DAYS:
        when = "today" if days_since_hard == 0 else "yesterday"
        hours = HARD_SPACING_DAYS * 24
        return {"decision": "easy",
                "sentence": f"Easy today - the last hard session was {when}; "
                            f"hard stimuli are {hours} hours apart.",
                "reason": f"{hours}-hour rule", "week": week}

    if target_min <= 0:
        return {"decision": "hard",
                "sentence": "Today is the day for the hard session - first session "
                            "of the week, light is green.",
                "reason": "GO, first session of the week", "week": week}

    if hard_min < target_min:
        return {"decision": "hard",
                "sentence": "Today is the day for the hard session - the week is "
                            "below its hard share.",
                "reason": "GO, week below share", "week": week}

    # Load bucket full, stimulus bucket empty: what is missing is quality, not
    # volume. The light is green and the 48 hours are clear, so this is the day
    # for it - and saying "no further hard stimulus needed" here would be the
    # app endorsing a week of grey-zone running.
    if no_quality and not far_over:
        return {"decision": "hard",
                "sentence": "Today is the day for the hard session - the week has its "
                            "minutes above easy, but all of them are in zone 3 and none "
                            "above threshold. What is missing is quality, not volume.",
                "reason": "GO, share reached but no quality", "week": week}

    if no_quality:      # ...and far over the target: too much of the wrong thing
        return {"decision": "easy",
                "sentence": "Easy today - the week is far over its share of non-easy "
                            "minutes and every one of them is in zone 3. Adding a hard "
                            "session on top is not the fix; the easy days need to get "
                            "easier first.",
                "reason": "GO, far over share, all grey zone", "week": week}

    return {"decision": "easy",
            "sentence": "Easy today - the week's hard share is reached, no further "
                        "hard stimulus needed.",
            "reason": "GO, share reached", "week": week}


# ── What comes next ──────────────────────────────────────────────────────────

#: Z5 seconds in ONE run from which a VO2max stimulus counts as set. Three
#: minutes: one rep of the shortest structure the planner builds (3x3), and
#: well above what a hill puts into zone 5 for a few seconds.
STIMULUS_Z5_MIN_S = 180
#: How long a stimulus holds. Seven days: one quality session of each kind per
#: week is the polarised default, so a Z5 session a week ago was LAST week's.
STIMULUS_WINDOW_DAYS = 7
#: Without a new VO2max value for this long, the next quality session is a
#: steady block - the one shape Garmin re-measures from (zones.md).
MEASUREMENT_DUE_DAYS = 28
#: ...and once a steady block was scheduled, the next reading gets this long
#: before another one is asked for: a plateau is not a missing measurement,
#: and without this a flat line would ask for a measurement every week.
MEASUREMENT_RETRY_DAYS = 21
#: Days without a run after which the next week is a return week, not a step.
RETURN_AFTER_DAYS = 10
#: How long the return phase lasts after the first run back: these days are
#: easy, and a week planned to start inside them is a return week...
RETURN_PHASE_DAYS = 7
#: ...and it lasts until there are this many runs back, too: one jog after
#: two weeks off does not make the next week an ordinary one.
RETURN_PHASE_RUNS = 2


def comeback(run_days: list[date], today: date, prior: date | None = None) -> dict | None:
    """The pause the athlete is in or coming back from - ONE definition, read
    by the day's decision, the next stimulus and the week planner alike, so
    the day and the week cannot disagree about it.

    `run_days`: days with a run, ascending; `prior`: the last run before them.
    Returns `None` when there is no pause in sight, else a dict:

    * `phase`: `pause` (no run for `RETURN_AFTER_DAYS`), `return` (the first
      `RETURN_PHASE_DAYS` after the first run back, and until there are
      `RETURN_PHASE_RUNS` runs back) or `settled` (back for longer than that -
      the week planner still uses `first_back`);
    * `runs_back`: how many days with a run since the first run back;
    * `pause_days`: how long the gap was (or is, in a pause);
    * `first_back`: the first run after the gap (`None` in a pause);
    * `last_before`: the last run before the gap."""
    days = ([prior] if prior else []) + list(run_days)
    if not days:
        return None
    last = days[-1]
    if (today - last).days >= RETURN_AFTER_DAYS:
        return {"phase": "pause", "pause_days": (today - last).days, "first_back": None,
                "last_before": last}
    i = len(days) - 1
    while i > 0 and (days[i] - days[i - 1]).days < RETURN_AFTER_DAYS:
        i -= 1
    if i == 0:
        return None
    first_back, last_before = days[i], days[i - 1]
    runs_back = len(days) - i
    back = (today - first_back).days < RETURN_PHASE_DAYS or runs_back < RETURN_PHASE_RUNS
    return {"phase": "return" if back else "settled",
            "pause_days": (first_back - last_before).days, "first_back": first_back,
            "last_before": last_before, "runs_back": runs_back}


def in_comeback(state: dict | None) -> bool:
    """A pause or the first days back: no hard session today."""
    return bool(state) and state["phase"] in ("pause", "return")


def _comeback_words(state: dict, today: date) -> str:
    if state["phase"] == "pause":
        return f"{state['pause_days']} days without a run"
    if state.get("runs_back", 0) < RETURN_PHASE_RUNS and \
            (today - state["first_back"]).days >= RETURN_PHASE_DAYS:
        return f"one run back after {state['pause_days']} days without a run"
    back = (today - state["first_back"]).days
    when = "today" if back == 0 else "yesterday" if back == 1 else f"{back} days ago"
    return f"back {when} after {state['pause_days']} days without a run"


def measurement_due(last_change_day: date | None, first_value_day: date | None,
                    last_steady_day: date | None, today: date) -> dict:
    """Is it time for a steady block so Garmin can re-measure VO2max?

    `last_change_day`: the last day the stored VO2max CHANGED (`None` if it
    never did in the window), `first_value_day`: the first day with a value
    (Garmin leaves most days empty, so the span is counted in days of the
    calendar, never in rows), `last_steady_day`: the last steady block -
    scheduled by this app or run without it.

    A flat line cannot tell "measured again, same value" from "not measured",
    so the wording is "no new value for N days", never "not measured". And a
    steady block scheduled recently is given its chance first, or a true
    plateau would ask for a measurement every single week."""
    if first_value_day is None:
        return {"due": False, "days": None, "reason": "no VO2max on record"}
    if last_change_day is None:
        days = (today - first_value_day).days
        if days < MEASUREMENT_DUE_DAYS:
            return {"due": False, "days": days, "reason": f"only {days} days of VO2max on record"}
    else:
        days = (today - last_change_day).days
        if days < MEASUREMENT_DUE_DAYS:
            return {"due": False, "days": days, "reason": f"VO2max changed {days} day(s) ago"}
    if last_steady_day is not None and (today - last_steady_day).days < MEASUREMENT_RETRY_DAYS:
        since = (today - last_steady_day).days
        when = (f"is scheduled for {last_steady_day.isoformat()}" if since < 0
                else f"was run or scheduled {since} day(s) ago")
        return {"due": False, "days": days,
                "reason": f"no new VO2max value for {days} days, but a steady block {when}; "
                          f"that reading gets its chance first"}
    return {"due": True, "days": days,
            "reason": f"no new VO2max value for {days} days; >= 12 min of even effort at "
                      f"threshold is what Garmin measures from"}


def _is_run_row(r: dict) -> bool:
    return "running" in str(r.get("type") or r.get("activity_type") or "").lower()


def next_stimulus(runs: list[dict], *, today: date, measurement: dict | None = None,
                  comeback_state: dict | None = None, planned_ahead: dict | None = None) -> dict:
    """WHICH quality session a hard day calls for - a recommendation, never
    the decision (`decide_today` says hard/easy/rest; this says which hard).

    `runs`: recent sessions in the snapshot's shape (`day`, `type`, `zones_s`,
    `has_detail`, `anaerobic_te`). Returns `{"kind", "reason"}` with kind
    `steady` | `vo2max` | `threshold` | `unknown`.

    The order: in a pause or the first days back (`comeback`) easy running,
    nothing hard. Then VO2max if no run of the last 7 days set a Z5
    stimulus (>= 3 min in zone 5, or anaerobic TE >= 2: the same stimulus
    `coach.md` tells the model to look for before claiming "too little VO2max
    work"). With the Z5 stimulus set, a due measurement takes the hard day -
    the steady block goes where threshold would go, exactly as in the week
    planner, so a plateau never starves the VO2max work. Else threshold. A run
    without zone detail says so instead of counting as "no stimulus": absence
    of data is not absence of training. `planned_ahead` (kind -> day of this
    app's own sessions on the calendar in the next days) moves the answer to
    the other quality kind when the named one is already planned."""
    if in_comeback(comeback_state):
        return {"kind": "easy",
                "reason": f"{_comeback_words(comeback_state, today)}; the first runs back are easy"}
    due = bool(measurement and measurement.get("due"))
    lo = today - timedelta(days=STIMULUS_WINDOW_DAYS - 1)
    best: tuple[int, int, float] | None = None       # (days ago, z5 seconds, anaerobic TE)
    undetailed = 0
    for r in runs or []:
        if not r.get("day") or not _is_run_row(r):
            continue
        day = date.fromisoformat(r["day"])
        if not lo <= day <= today:
            continue
        z5 = int((r.get("zones_s") or {}).get("z5") or r.get("hr_z5_s") or 0)
        an = float(r.get("anaerobic_te") or 0.0)
        if z5 >= STIMULUS_Z5_MIN_S or an >= QUALITY_ANAEROBIC_TE:
            ago = (today - day).days
            if best is None or ago < best[0]:
                best = (ago, z5, an)
        elif not r.get("has_detail"):
            undetailed += 1
    planned = planned_ahead or {}
    if best is not None:
        ago, z5, an = best
        when = "today" if ago == 0 else "yesterday" if ago == 1 else f"{ago} days ago"
        what = (f"{round(z5 / 60)} min in zone 5" if z5 >= STIMULUS_Z5_MIN_S
                else f"anaerobic TE {an:.1f}")
        if due:
            out = {"kind": "steady", "reason": f"{measurement['reason']} - and the Z5 stimulus "
                                               f"is set ({what}, {when})"}
        else:
            out = {"kind": "threshold",
                   "reason": f"the Z5 stimulus is set ({what}, {when}); threshold is the other "
                             f"quality session of a polarised week"}
    elif undetailed:
        return {"kind": "unknown",
                "reason": f"{undetailed} run(s) of the last {STIMULUS_WINDOW_DAYS} days have no "
                          f"zone detail yet; sync, then ask again"}
    else:
        out = {"kind": "vo2max",
               "reason": f"no Z5 stimulus in the last {STIMULUS_WINDOW_DAYS} days; reps of 3-5 "
                         f"min in zone 5 are what moves VO2max"
                         + ("; the measurement follows once it is set" if due else "")}
    if out["kind"] in planned:
        other = "threshold" if out["kind"] == "vo2max" else "vo2max"
        if other not in planned:
            return {"kind": other,
                    "reason": f"{out['kind']} is already on the calendar for "
                              f"{planned[out['kind']]}; {other} is the other quality session"}
    return out


# ── Interval structure ───────────────────────────────────────────────────────


def interval_facts(splits: list[dict]) -> dict:
    """Interval structure from splits — ONE source of truth for app and coach.

    Garmin auto-segments runs; reading splits naively quickly turns "5×4 min"
    into "5 minutes". `label` is the human short form ("5×4′"), `kind` says how
    reliable the statement is: `intervals` (real ACTIVE splits), `steady`
    (splits present, none active) or `unknown` (no splits at all — then say
    nothing rather than claim "steady run")."""
    def _typed(suffix: str) -> list[dict]:
        return [s for s in splits if (s.get("split_type") or "").upper().endswith(suffix)]

    def _avg(rows: list[dict], key: str) -> float | None:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    active = _typed("ACTIVE")
    recovery = _typed("RECOVERY")
    rep_s = _avg(active, "duration_s")

    # ONE active split is only a structure if something else surrounds it.
    # Garmin likes to mark a plain steady run entirely as ACTIVE — that used to
    # become "1×42′" for a 42-minute easy run.
    total_s = sum(s["duration_s"] for s in splits if s.get("duration_s")) or 0
    active_s = sum(s["duration_s"] for s in active if s.get("duration_s")) or 0
    share = (active_s / total_s) if total_s else 1.0
    structured = len(active) >= 2 or (len(active) == 1 and share < 0.7)

    if structured:
        kind = "intervals"
        if rep_s and abs(rep_s / 60 - round(rep_s / 60)) < 0.12 and rep_s >= 55:
            label = f"{len(active)}×{round(rep_s / 60)}′"
        elif rep_s and rep_s >= 90:
            label = f"{len(active)}×{int(rep_s // 60)}:{round(rep_s % 60):02d}"
        elif rep_s:
            label = f"{len(active)}×{round(rep_s)}″"
        else:
            label = f"{len(active)} reps"
    elif splits:
        kind, label = "steady", "Steady run"
    else:
        kind, label = "unknown", None

    return {
        "kind": kind,
        "label": label,
        "has_intervals": structured,
        "rep_count": len(active) if structured else 0,
        "avg_rep_duration_s": rep_s if structured else None,
        "avg_rep_distance_m": _avg(active, "distance_m"),
        "avg_active_hr": _avg(active, "avg_hr"),
        "max_active_hr": max((r["max_hr"] for r in active
                              if r.get("max_hr") is not None), default=None),
        "avg_recovery_hr": _avg(recovery, "avg_hr"),
        "split_count": len(splits),
    }


# ── Run classification ───────────────────────────────────────────────────────

#: Below this much time above threshold in a whole WEEK, the week contains no
#: stimulus — it is HR drift on a hill, not a session. A floor rather than
#: "more than zero": at one second the grey-zone correction switched off while
#: the sentence the athlete reads ("of which 0 above threshold") stayed
#: byte-identical. A minute is also the unit every surface displays.
QUALITY_FLOOR_SECONDS = 60

#: Days between two hard stimuli: 48 hours. One constant for the day's
#: decision, the week planner and the calendar fit (zones.md: a convention).
HARD_SPACING_DAYS = 2
HARD_AEROBIC_TE = 3.0           # Garmin: "improving" and above
QUALITY_ANAEROBIC_TE = 2.0      # real interval/tempo stimulus from here
LONG_RUN_MIN_SECONDS = 70 * 60  # a 70+ min run is a key session whatever its TE


def is_running(activity: dict) -> bool:
    """`"running" in type` (not `==`) so trail_/track_/treadmill_running count."""
    return "running" in str(activity.get("activity_type") or "").lower()


def is_hard(activity: dict) -> bool:
    """ONE definition of a hard day. The 48-hour rule, the digest label and the
    web app all call this — a session the app labels hard must be the same
    session the spacing rule sees, or the two contradict each other on one
    screen.

    Two ways to be hard. A high training effect counts in ANY sport: a hard bike
    session or a heavy gym hour is a systemic stimulus too. Sheer LENGTH counts
    only for a run — 70 minutes on the legs wants recovery whatever the wrist
    says about it, but an 80-minute walk or a long easy commute ride is not a
    reason to cancel tomorrow's intervals, and it used to be exactly that.

    Mirrored in SQL by `store._HARD_SQL` and in JavaScript by `isHardSession`;
    both are pinned by tests against this function."""
    return ((activity.get("aerobic_te") or 0.0) >= HARD_AEROBIC_TE
            or (activity.get("anaerobic_te") or 0.0) >= QUALITY_ANAEROBIC_TE
            or (is_running(activity)
                and (activity.get("duration_s") or 0) >= LONG_RUN_MIN_SECONDS))


def run_kind(activity: dict) -> str | None:
    """'Quality' | 'Long Run' | 'Easy' for runs, None otherwise — a LABEL on top
    of `is_hard`, never a second definition of it: an easy run is one that is not
    hard, and a hard run is a long run if it was long, else quality work."""
    if not is_running(activity):
        return None
    if not is_hard(activity):
        return "Easy"
    if (activity.get("anaerobic_te") or 0.0) >= QUALITY_ANAEROBIC_TE:
        return "Quality"          # real intensity outranks length as a description
    return "Long Run" if (activity.get("duration_s") or 0) >= LONG_RUN_MIN_SECONDS else "Quality"
