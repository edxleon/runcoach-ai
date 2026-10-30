-- Garmin's training load balance ("load focus"): its 4-week load in three
-- bands - low aerobic, high aerobic, anaerobic - each with the target range
-- Garmin sets for this athlete, and the one-phrase verdict
-- (`AEROBIC_HIGH_SHORTAGE`, `BALANCED`, ...). Delivered with the training
-- status the sync already fetches, as of the requested day (recorded on a real
-- account: 2026-09-10 and 2026-10-29 carry different values). Shown as
-- Garmin's second opinion beside the app's own intensity minutes; no rule
-- reads it. Profile-like columns (`DailyMetrics.PROFILE_FIELDS`): written with
-- COALESCE, and they never make a day a measured day on their own.
ALTER TABLE daily_metrics ADD COLUMN load_aerobic_low REAL;
ALTER TABLE daily_metrics ADD COLUMN load_aerobic_high REAL;
ALTER TABLE daily_metrics ADD COLUMN load_anaerobic REAL;
ALTER TABLE daily_metrics ADD COLUMN load_aerobic_low_min INTEGER;
ALTER TABLE daily_metrics ADD COLUMN load_aerobic_low_max INTEGER;
ALTER TABLE daily_metrics ADD COLUMN load_aerobic_high_min INTEGER;
ALTER TABLE daily_metrics ADD COLUMN load_aerobic_high_max INTEGER;
ALTER TABLE daily_metrics ADD COLUMN load_anaerobic_min INTEGER;
ALTER TABLE daily_metrics ADD COLUMN load_anaerobic_max INTEGER;
ALTER TABLE daily_metrics ADD COLUMN load_balance TEXT;
