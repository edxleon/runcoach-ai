-- Garmin's own Training Readiness (0-100 and its level, `HIGH`, `MODERATE`,
-- ...) for the morning, on the watches that compute it (keys from
-- garminconnect's typed `TrainingReadiness` model; older watches - a fenix 6S
-- checked 2026-10-30 - return nothing at all). Shown beside the app's light as
-- Garmin's opinion; no rule reads it, and the decision never depends on it.
-- Profile-like columns (`DailyMetrics.PROFILE_FIELDS`): written onto today's
-- row, they never make a day a measured day on their own.
ALTER TABLE daily_metrics ADD COLUMN garmin_readiness INTEGER;
ALTER TABLE daily_metrics ADD COLUMN garmin_readiness_level TEXT;

-- Body weight in kg from Garmin's weigh-ins (`latestWeight.weight` of each
-- day, recorded in grams on a real account): VO2max is per kilogram, so a
-- weight change moves it without any change in fitness. Its OWN table: a
-- weigh-in is a measurement of its own, and on a real account 4 of 5 fell on
-- days with no watch row (a first sync fetches 30 days, later ones 3) - kept
-- as a column of `daily_metrics`, they were lost.
CREATE TABLE weigh_ins (
    day       TEXT PRIMARY KEY,
    weight_kg REAL NOT NULL,
    synced_at TEXT NOT NULL
);
