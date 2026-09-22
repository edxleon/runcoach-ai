-- Workouts THIS app uploaded to Garmin. Provenance, not a mirror: the calendar
-- mirror in `scheduled_workouts` is Garmin's truth and is replaced per sync;
-- this table says which templates are ours, so that "delete" can only ever
-- reach a workout runcoach created - never one of the athlete's own.
CREATE TABLE runcoach_workouts (
    workout_id      INTEGER PRIMARY KEY,           -- Garmin's id, from the upload
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,                 -- planning.KINDS
    spec_json       TEXT NOT NULL,                 -- planning.to_json(spec)
    created_at      TEXT NOT NULL,                 -- UTC
    schedule_id     INTEGER,                       -- Garmin's calendar id, if scheduled
    scheduled_day   TEXT                           -- local day it was put on
);
