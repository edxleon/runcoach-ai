-- One row per session a proposal is allowed to upload, CLAIMED before the
-- upload happens. It is the write path's only piece of concurrency control.
--
-- Why not the proposal file: `plan.apply` read the file, checked `status ==
-- "open"`, uploaded, then wrote the file back. Two yeses at the same moment -
-- a click on the card and an answer in a Claude Code session, or two browser
-- tabs - both read "open" and both uploaded. The athlete ends up with the same
-- session twice on the watch, and the app knows about one of them. A file
-- cannot decide that race; this table can, because the INSERT either wins or
-- does not.
--
-- `state` is what separates the two ways a claim can outlive its apply:
--
--   in_flight  the upload was started and has not answered yet. A process that
--              dies here leaves a row nothing will ever complete, so a claim
--              older than `store.CLAIM_STALE_MINUTES` is reaped and the session
--              can be applied again - nothing was created, or the reply that
--              said otherwise is long gone.
--   unknown    the upload failed in a way that does NOT prove Garmin created
--              nothing (a read timeout). Never reaped automatically: retrying
--              is exactly what would put the session on the watch twice. It is
--              reported by `runcoach doctor` for a human to resolve.
--   done       `workout_id` is set; this session is on Garmin.
CREATE TABLE proposal_items (
    proposal_id  TEXT    NOT NULL,          -- plan.new_id()
    item_index   INTEGER NOT NULL,          -- position in the proposal's items[]
    claimed_at   TEXT    NOT NULL,          -- UTC, when the claim was taken
    state        TEXT    NOT NULL DEFAULT 'in_flight',
    workout_id   INTEGER,                   -- filled once Garmin accepted it
    PRIMARY KEY (proposal_id, item_index)
);
