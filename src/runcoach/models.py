"""Data model. Plain dataclasses on the ingest path — value ranges are clamped
once, in `garmin.py`, instead of being validated twice."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime


@dataclass(slots=True)
class DailyMetrics:
    """One day of recovery + training signals. Everything except `day` is
    optional: Garmin does not report every metric every day (NULL means
    "not measured", never 0)."""

    day: date

    sleep_seconds: int | None = None
    deep_sleep_seconds: int | None = None
    light_sleep_seconds: int | None = None
    rem_sleep_seconds: int | None = None
    awake_seconds: int | None = None
    sleep_score: int | None = None

    hrv_avg_ms: int | None = None
    hrv_status: str | None = None

    stress_avg: int | None = None
    stress_max: int | None = None
    rest_stress_minutes: int | None = None

    body_battery_high: int | None = None
    body_battery_low: int | None = None

    resting_hr: int | None = None
    steps: int | None = None

    # VO2max keeps its decimal: Firstbeat reports 47.9 / 48.0 and that is the
    # resolution in which day-to-day changes happen. Never round it.
    vo2max: float | None = None
    training_status: str | None = None
    acute_load: int | None = None
    chronic_load: int | None = None
    acwr_ratio: float | None = None
    acwr_status: str | None = None
    intensity_moderate_min: int | None = None
    intensity_vigorous_min: int | None = None

    # Lactate threshold: a slowly moving PROFILE value that Garmin re-measures
    # on hard runs. It lives on the row of the day it was MEASURED (written by
    # `Store.upsert_lactate_history`), never carried forward onto every synced
    # day — that would stamp today's value onto historical rows.
    lthr_bpm: int | None = None
    lt_speed_mps: float | None = None
    lt_measured_on: date | None = None

    # Race predictions (seconds). Profile values as well.
    race_5k_s: int | None = None
    race_10k_s: int | None = None
    race_hm_s: int | None = None
    race_m_s: int | None = None

    source: str = "garmin"

    #: Columns whose Garmin endpoint FAILED on this fetch — "we did not learn",
    #: not "there was none". `Store.upsert_daily` keeps the stored value for
    #: these instead of overwriting it with NULL. Not a column: excluded from
    #: `column_names()` below, which is what builds the INSERT.
    unknown: frozenset = frozenset()

    #: Fields that do NOT make a day a measured day. Profile values arrive on
    #: every sync whether or not the watch was worn; counting them would create
    #: ghost rows for days without any data.
    PROFILE_FIELDS = ("lthr_bpm", "lt_speed_mps", "lt_measured_on",
                      "race_5k_s", "race_10k_s", "race_hm_s", "race_m_s")

    @classmethod
    def column_names(cls) -> list[str]:
        """Column names in dataclass order — the single source for INSERT/UPSERT.
        `unknown` is bookkeeping, not data, and is left out."""
        return [f.name for f in fields(cls) if f.name != "unknown"]

    def has_any_metric(self) -> bool:
        skip = {"day", "source", "unknown", *self.PROFILE_FIELDS}
        return any(getattr(self, f.name) is not None
                   for f in fields(self) if f.name not in skip)


@dataclass(slots=True)
class Activity:
    """One workout. `activity_id` is Garmin's id, so the upsert is idempotent.

    Summary only, on purpose: the detail columns (HR zones, cadence, weather)
    live on the table but not here — otherwise a plain summary re-sync would
    reset them to NULL. Detail is written via `Store.update_activity_detail`."""

    activity_id: int
    start_time: datetime

    activity_type: str | None = None
    name: str | None = None
    distance_m: int | None = None
    duration_s: int | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    calories: int | None = None
    training_load: int | None = None
    aerobic_te: float | None = None
    anaerobic_te: float | None = None
    te_label: str | None = None
    vo2max: float | None = None

    source: str = "garmin"

    @classmethod
    def column_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]


@dataclass(slots=True)
class ActivitySplit:
    """One split/lap of a workout (interval structure). `split_type` is Garmin's
    label (INTERVAL_WARMUP/ACTIVE/RECOVERY/COOLDOWN or an RWD_RUN lap)."""

    activity_id: int
    split_index: int

    split_type: str | None = None
    distance_m: int | None = None
    duration_s: int | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    elevation_gain_m: int | None = None

    @classmethod
    def column_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]


@dataclass(slots=True)
class ScheduledWorkout:
    """A workout scheduled in the Garmin calendar. Mirrored with full replace
    per sync window: Garmin is the truth, a deleted entry must disappear here."""

    schedule_id: int
    day: date
    workout_id: int | None = None
    title: str | None = None
    sport: str | None = None
