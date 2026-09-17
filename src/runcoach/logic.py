"""The deterministic core: readiness verdict, today's decision, interval
structure, run classification. Pure functions — no database, no network, no
LLM. The coach may phrase a recommendation, but the verdict comes from here,
so the app and the agent can never disagree about the facts."""

from __future__ import annotations

# ── Readiness ────────────────────────────────────────────────────────────────


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
        if acwr > 1.5:
            if acwr_source == "computed":
                flags.append(("easy", "acwr", f"ACWR {acwr} (high, computed - uncertain)"))
            else:
                flags.append(("rest", "acwr", f"ACWR {acwr} (>1.5 = injury risk)"))
        elif acwr > 1.3:
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

    # GO from here on. 48 h between hard stimuli, regardless of the weekly state.
    if days_since_hard is not None and days_since_hard <= 1:
        when = "today" if days_since_hard == 0 else "yesterday"
        return {"decision": "easy",
                "sentence": f"Easy today - the last hard session was {when}; "
                            "hard stimuli are 48 hours apart.",
                "reason": "48-hour rule", "week": week}

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
