-- runcoach schema v1 (SQLite).
-- Dates are ISO-8601 TEXT: `day` = local calendar day, timestamps = UTC.
-- Value ranges are clamped once at ingest (garmin.py); the CHECKs below are
-- the backstop for the few columns where a wrong value would mislead the coach.

CREATE TABLE daily_metrics (
    day                     TEXT PRIMARY KEY,
    sleep_seconds           INTEGER,
    deep_sleep_seconds      INTEGER,
    light_sleep_seconds     INTEGER,
    rem_sleep_seconds       INTEGER,
    awake_seconds           INTEGER,
    sleep_score             INTEGER CHECK (sleep_score BETWEEN 0 AND 100),
    hrv_avg_ms              INTEGER,
    hrv_status              TEXT CHECK (hrv_status IN ('BALANCED','UNBALANCED','LOW','POOR','NONE')),
    stress_avg              INTEGER,
    stress_max              INTEGER,
    rest_stress_minutes     INTEGER,
    body_battery_high       INTEGER CHECK (body_battery_high BETWEEN 0 AND 100),
    body_battery_low        INTEGER CHECK (body_battery_low BETWEEN 0 AND 100),
    resting_hr              INTEGER CHECK (resting_hr BETWEEN 20 AND 220),
    steps                   INTEGER,
    vo2max                  REAL,
    training_status         TEXT,
    acute_load              INTEGER,
    chronic_load            INTEGER,
    acwr_ratio              REAL CHECK (acwr_ratio BETWEEN 0 AND 3),
    acwr_status             TEXT,
    intensity_moderate_min  INTEGER,
    intensity_vigorous_min  INTEGER,
    -- Profile values: live on the day they were measured, never carried forward.
    lthr_bpm                INTEGER,
    lt_speed_mps            REAL,
    lt_measured_on          TEXT,
    race_5k_s               INTEGER,
    race_10k_s              INTEGER,
    race_hm_s               INTEGER,
    race_m_s                INTEGER,
    source                  TEXT NOT NULL DEFAULT 'garmin',
    synced_at               TEXT NOT NULL
);

CREATE TABLE activities (
    activity_id             INTEGER PRIMARY KEY,
    start_time              TEXT NOT NULL,          -- UTC
    -- Local calendar day, materialised on write: a late-evening run must not
    -- slide into the next day and skew the 7/28-day load windows.
    local_day               TEXT NOT NULL,
    activity_type           TEXT,
    name                    TEXT,
    distance_m              INTEGER,
    duration_s              INTEGER,
    avg_hr                  INTEGER,
    max_hr                  INTEGER,
    calories                INTEGER,
    training_load           INTEGER,
    aerobic_te              REAL CHECK (aerobic_te BETWEEN 0 AND 6),
    anaerobic_te            REAL CHECK (anaerobic_te BETWEEN 0 AND 6),
    te_label                TEXT,
    vo2max                  REAL,
    -- Detail (own write path, see Store.update_activity_detail)
    hr_z1_s                 INTEGER,
    hr_z2_s                 INTEGER,
    hr_z3_s                 INTEGER,
    hr_z4_s                 INTEGER,
    hr_z5_s                 INTEGER,
    hr_z4_low               INTEGER,
    hr_z5_low               INTEGER,
    avg_cadence             INTEGER,
    temperature_c           INTEGER,
    humidity_pct            INTEGER,
    performance_condition   INTEGER,
    detail_synced_at        TEXT,
    source                  TEXT NOT NULL DEFAULT 'garmin',
    synced_at               TEXT NOT NULL
);
CREATE INDEX activities_local_day_idx ON activities (local_day);

CREATE TABLE activity_splits (
    activity_id             INTEGER NOT NULL REFERENCES activities (activity_id) ON DELETE CASCADE,
    split_index             INTEGER NOT NULL,
    split_type              TEXT,
    distance_m              INTEGER,
    duration_s              INTEGER,
    avg_hr                  INTEGER,
    max_hr                  INTEGER,
    elevation_gain_m        INTEGER,
    PRIMARY KEY (activity_id, split_index)
);

CREATE TABLE scheduled_workouts (
    schedule_id             INTEGER PRIMARY KEY,
    workout_id              INTEGER,
    day                     TEXT NOT NULL,
    title                   TEXT,
    sport                   TEXT,
    synced_at               TEXT NOT NULL
);
CREATE INDEX scheduled_workouts_day_idx ON scheduled_workouts (day);
