-- What the athlete said about themselves: ill, pain, sore, stressed (the
-- Today tab's buttons and the `check_in` tool). A check-in is a state, not a
-- day: `since` the first day it was said, `last_said` the latest, `over` the
-- first day it no longer held as the athlete said (NULL while it holds, and
-- once it ran out without a new word - `logic.check_in_end` decides which). A
-- new row starts when the kind is said again after the last one ended.
-- `taken_back` marks a tap by mistake, undone the day it was made: the row
-- stays so `changed_at` still tells a coach card it is out of date.
CREATE TABLE check_ins (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('ill', 'pain', 'sore', 'stressed')),
    since      TEXT NOT NULL,
    last_said  TEXT NOT NULL,
    over       TEXT,
    note       TEXT,
    taken_back INTEGER NOT NULL DEFAULT 0 CHECK (taken_back IN (0, 1)),
    created_at TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    CHECK (last_said >= since),
    CHECK (over IS NULL OR over > since)
);
CREATE INDEX check_ins_kind_last ON check_ins (kind, last_said);
