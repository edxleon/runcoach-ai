"""MCP server (stdio): the same data core the web app uses, exposed as tools
for Claude Code / Claude Desktop — and for the coach agent the app spawns.

    claude mcp add runcoach -- runcoach mcp

Tool descriptions say WHEN to use a tool and what comes back, because the
description is the only thing the model sees when choosing.
"""

from __future__ import annotations

import os
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import plan, tools
from .store import TREND_METRICS, Store

mcp = FastMCP("runcoach")
_store: Store | None = None

Metric = Literal["sleep_seconds", "sleep_score", "hrv_avg_ms", "stress_avg",
                 "body_battery_high", "resting_hr", "steps"]
# A raised error, not a bare `assert`: `python -O` drops asserts, and this one is
# the only thing keeping the tool's advertised enum in step with the column list
# the store will accept. Silently dropped, the MCP server would offer a metric
# that every call then rejects.
if set(Metric.__args__) != set(TREND_METRICS):
    raise RuntimeError(
        f"Metric literal drifted from TREND_METRICS: "
        f"{sorted(set(Metric.__args__) ^ set(TREND_METRICS))}")

Day = Annotated[str | None, Field(description="ISO date YYYY-MM-DD. Omit for the latest.",
                                  pattern=r"^\d{4}-\d{2}-\d{2}$")]


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.environ.get("RUNCOACH_DB") or None)
    return _store


@mcp.tool()
def get_training_readiness() -> str:
    """Today's readiness verdict GO / EASY / REST with the signals behind it (HRV
    status, sleep score, Body Battery, resting HR vs 27-day baseline, ACWR, days
    since the last hard workout). Rule-based and conservative. START HERE for
    "should I train today?". Flags stale data explicitly."""
    return tools.get_training_readiness(store())


@mcp.tool()
def get_recovery_summary(
    period_days: Annotated[int, Field(ge=1, le=90, description="Window length in days.")] = 7,
) -> str:
    """Averages of sleep, HRV, resting HR, stress, Body Battery and steps over the
    last N days plus a snapshot of the latest day. Use for "how has my recovery
    been?". Aggregates only — never a day-by-day list."""
    return tools.get_recovery_summary(store(), period_days)


@mcp.tool()
def get_daily_metrics(day: Day = None) -> str:
    """Every recorded recovery value for ONE day (default: latest day with data).
    Use when a single night/day is in question, not for trends."""
    return tools.get_daily_metrics(store(), day)


@mcp.tool()
def get_trend(
    metric: Metric,
    period_days: Annotated[int, Field(ge=7, le=365, description="Window length in days.")] = 28,
) -> str:
    """Weekly averages of ONE recovery metric over the last N days — for "is my
    HRV / resting HR / sleep trending up or down?"."""
    return tools.get_trend(store(), metric, period_days)


@mcp.tool()
def get_training_load(
    period_days: Annotated[int, Field(ge=14, le=90, description="Window length in days.")] = 28,
) -> str:
    """Training-load picture: ACWR with its SOURCE (Garmin's EWMA ratio, or a
    self-computed fallback that is less reliable), Garmin training status, VO2max
    with change, and weekly load buckets. VO2max change is only reported when the
    value actually varied (Garmin carries the last value forward)."""
    return tools.get_training_load(store(), period_days)


@mcp.tool()
def get_recent_activities(
    period_days: Annotated[int, Field(ge=7, le=90, description="Window length in days.")] = 14,
) -> str:
    """Workout digest: totals per sport plus the latest workouts, each run
    classified Quality / Long Run / Easy (from training effect and duration). Use
    to see what was actually trained before recommending the next session."""
    return tools.get_recent_activities(store(), period_days)


@mcp.tool()
def get_intensity_distribution(
    period_days: Annotated[int, Field(ge=7, le=90, description="Window length in days.")] = 28,
) -> str:
    """Time in heart-rate zones across all runs (easy Z1-2 / moderate Z3 / hard
    Z4-5 / Z5) with percentages — the basis for the 80/20 polarisation question.
    States how many runs have zone detail, so an incomplete picture is visible."""
    return tools.get_intensity_distribution(store(), period_days)


@mcp.tool()
def analyze_workout(
    day: Day = None,
    activity_id: Annotated[int | None, Field(description="Garmin activity id; wins over `day`.")] = None,
) -> str:
    """Deep dive into ONE run: time in each HR zone, interval structure (rep count,
    rep length, work vs recovery HR), weather, performance condition, plus
    rule-based notes. Default: the latest run with detail. The interval structure
    is Garmin's auto-detection — if the athlete states a different structure,
    the athlete is right."""
    return tools.analyze_workout(store(), day, activity_id)


@mcp.tool()
def get_vo2max_history() -> str:
    """VO2max over the last 8 weeks as STEPS (only the days the value really
    changed — Garmin carries it forward in between) plus a descriptive comparison
    of the last 28 days with the 28 before (distance, Z5 minutes, easy share,
    temperature). Use for "why is my VO2max moving?". Descriptive, not causal."""
    return tools.get_vo2max_history(store())


@mcp.tool()
def sync_garmin(
    days: Annotated[int, Field(ge=1, le=7, description="How many days back to re-fetch.")] = 1,
) -> str:
    """Pull the latest days and workouts from Garmin Connect NOW. Call this first
    when today's run or last night's sleep is missing. Takes 10-40 s. Read-only
    towards Garmin; writes only to the local database."""
    return tools.sync_garmin(store(), days)


Kind = Literal["easy", "long", "threshold", "vo2max", "steady"]


@mcp.tool()
def propose_workout(
    kind: Kind,
    distance_km: Annotated[float | None, Field(ge=1, le=60, description=(
        "Route length in km, e.g. 10 for a fixed 10 km loop. Give this OR duration_min."))] = None,
    duration_min: Annotated[int | None, Field(ge=10, le=300, description=(
        "Session length in minutes. Give this OR distance_km."))] = None,
    day: Day = None,
    name: Annotated[str | None, Field(max_length=60, description=(
        "Workout name on the watch. Default: kind and structure, e.g. 'VO2max 4x4 min'."))] = None,
    replaces_schedule_id: Annotated[int | None, Field(ge=1, description=(
        "A calendar entry this session REPLACES (the number after 'schedule' in "
        "get_training_readiness). Applying unschedules it first; the workout stays in the "
        "library. Use for the readiness swap: an easy run instead of the hard session the "
        "calendar had on a red or amber day."))] = None,
) -> str:
    """Build a structured session for the athlete's route or time budget and file
    it as a PROPOSAL - warm-up, work reps with targets, recovery jogs, cool-down -
    sized so the whole thing adds up to the route. Targets come from the
    athlete's own Garmin zones; anything not measured is listed as an assumption.
    Use when the athlete asks for a workout ("plan me intervals for my 10 km",
    "an easy 45 minutes", "a threshold session") or when the readiness verdict
    calls for a different session than the one on the calendar.
    Kinds: easy/long (one capped step), threshold (Z4 reps), vo2max (Z5 reps),
    steady (>= 12 min even effort at threshold - the only shape Garmin measures
    VO2max from). `day` defaults to today.
    Returns the preview and a proposal id. NOTHING is written to Garmin: show
    the preview, and only if the athlete says yes call apply_workout with the
    id. On an impossible request (route too short) returns why, not a session."""
    return tools.propose_workout(store(), kind, distance_km, duration_min, day, name,
                                 replaces_schedule_id)


@mcp.tool()
def propose_week(
    start_day: Annotated[str | None, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description=(
        "First day of the week to plan, YYYY-MM-DD, today or up to 8 days ahead. "
        "Default: the coming Monday (today, if today is Monday)."))] = None,
    days_per_week: Annotated[int | None, Field(ge=3, le=7, description=(
        "Running days. Default: the athlete's profile, else 4 (listed as an assumption)."))] = None,
    long_run_day: Annotated[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"] | None,
                            Field(description=(
        "Weekday of the long run. Default: the profile, else sun (listed as an assumption)."))] = None,
) -> str:
    """Build a polarised training week and file it as ONE proposal: a VO2max
    session and a threshold session 48 h apart, the long run on its weekday,
    easy runs between, nothing hard the day before or after the long run, at
    least one rest day. Sizes come from defaults (50/55/45/90 min), targets
    from the athlete's own zones. Use when the athlete asks for their week
    ("plan my week", "what should next week look like"). What the calendar
    already holds in that week is listed - the package ADDS to it.
    Returns the preview of every session and one proposal id; apply_workout
    with that id puts the whole week on Garmin after the athlete's yes.
    Nothing is written here."""
    return tools.propose_week(store(), start_day, days_per_week, long_run_day)


@mcp.tool()
def apply_workout(
    proposal_id: Annotated[str, Field(pattern=plan.PROPOSAL_ID_PATTERN,
                                      description="The id propose_workout returned.")],
) -> str:
    """WRITE a proposed session to the athlete's Garmin account: upload the
    workout, schedule it on the proposal's day, push it to the watch, then read
    it back and verify structure and targets. Call ONLY after the athlete has
    seen the preview and explicitly agreed - a request ("plan me 10 km") is not
    agreement, "yes, put it on the watch" is. Not available to the app's own
    card runs; there the athlete clicks. Returns what is now on Garmin, with
    any warning (not pushed because the watch is offline; a mismatch found on
    read-back). A proposal can be applied once; an unknown or used id says so
    and lists the open ones."""
    return tools.apply_workout(store(), proposal_id)


def main() -> None:
    mcp.run()
