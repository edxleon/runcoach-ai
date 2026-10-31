// Page arithmetic (static/logic.js) and the locale-bound formatters of ui.js.
//
// logic.js holds everything the page computes; app.js holds everything the page
// renders. That boundary is what makes this file possible at all — importing
// app.js would start the app.
import test from "node:test";
import assert from "node:assert/strict";
import {
  isoLocal, weekStart, addDays, barLabel, weekdayShort, dataAge,
  isHardSession, hardSessionToday, HARD_AEROBIC_TE, HARD_ANAEROBIC_TE, HARD_DURATION_S,
  lightAddendum, shortTitle, weekLabel, isRunning, isStrength, bandSplit, zoneTimeParts,
  groupRunsByWeek, NO_DATE, volumeComparison, volumeAverageKm, weekIncomplete,
  intensitySeries, intensityHits, chartPoints, chartScale, axisX, stepPath, stepMarks,
  barScale, carryForward, hasNoData, isFirstRun, showSyncFailure,
} from "../src/runspector/web/static/logic.js";
import { fmtKm, fmtNum, fmtDelta, fmtDay, fmtDayShort, fmtMinSec, fmtPace, fmtDur,
         fmtHours, plural, esc } from "../src/runspector/web/static/ui.js";

/* ── Hard sessions ────────────────────────────────────────────────────── */

/* These three numbers are a COPY of `src/runspector/logic.py: is_hard`. This is
   the test that keeps the copy honest: if the server's predicate moves, the
   line below has to move with it — and the page is not allowed to drift
   silently into a second meaning of "hard". */
test("isHardSession pins the server's three thresholds", () => {
  assert.equal(HARD_AEROBIC_TE, 3.0);
  assert.equal(HARD_ANAEROBIC_TE, 2.0);
  assert.equal(HARD_DURATION_S, 4200);             // 70 minutes
});

test("isHardSession: each threshold is inclusive at its boundary", () => {
  const run = (o) => ({ type: "running", aerobic_te: 1.0, anaerobic_te: 0.2, duration_s: 1800, ...o });
  assert.equal(isHardSession(run({ anaerobic_te: 2.0 })), true, "anaerobic 2.0 is hard");
  assert.equal(isHardSession(run({ anaerobic_te: 1.9 })), false, "anaerobic 1.9 is not");
  assert.equal(isHardSession(run({ aerobic_te: 3.0 })), true, "aerobic 3.0 is hard");
  assert.equal(isHardSession(run({ aerobic_te: 2.9 })), false, "aerobic 2.9 is not");
  assert.equal(isHardSession(run({ duration_s: 4200 })), true, "4200 s is hard");
  assert.equal(isHardSession(run({ duration_s: 4199 })), false, "4199 s is not");
});

test("isHardSession: a long easy RUN is hard by duration alone", () => {
  // The case the old frontend rule got wrong: a 75-minute easy long run with
  // TE 2.1/0.1 counted as "not hard", while the server had long since started
  // the 48-hour clock on it.
  const longEasy = { type: "running", aerobic_te: 2.1, anaerobic_te: 0.1, duration_s: 4500 };
  assert.equal(isHardSession(longEasy), true);
  assert.equal(isHardSession({ type: "running", aerobic_te: 2.1, anaerobic_te: 0.1,
                               duration_s: 3000 }), false);
});

test("isHardSession: length counts only for a RUN", () => {
  // An 80-minute walk or a long easy commute ride is not a reason to cancel
  // tomorrow's intervals; a hard effort in any sport still is.
  const long = { aerobic_te: 1.2, anaerobic_te: 0.1, duration_s: 4800 };
  assert.equal(isHardSession({ ...long, type: "walking" }), false);
  assert.equal(isHardSession({ ...long, type: "cycling" }), false);
  assert.equal(isHardSession({ ...long, type: "trail_running" }), true);
  assert.equal(isHardSession({ type: "cycling", aerobic_te: 3.4, duration_s: 600 }), true);
});

test("isHardSession: missing values are not hard, and never throw", () => {
  assert.equal(isHardSession(null), false);
  assert.equal(isHardSession({}), false);
  assert.equal(isHardSession({ aerobic_te: null, anaerobic_te: null, duration_s: null }), false);
});

test("hardSessionToday: only today's newest run, server signal wins", () => {
  const today = "2026-09-09";
  const easy = { activity_id: 1, day: today, aerobic_te: 1.0, anaerobic_te: 0.1, duration_s: 1800 };
  const hard = { ...easy, activity_id: 2, anaerobic_te: 2.4 };
  const yesterday = { ...hard, day: "2026-09-08" };

  assert.equal(hardSessionToday([easy], {}, today), null, "an easy run today is not it");
  assert.equal(hardSessionToday([hard], {}, today), hard);
  assert.equal(hardSessionToday([yesterday], {}, today), null, "yesterday does not count");
  assert.equal(hardSessionToday([], {}, today), null);
  assert.equal(hardSessionToday(null, null, today), null);
  // What the server computed anyway takes priority — otherwise the signal cell
  // and the coach line could disagree on the same screen.
  assert.equal(hardSessionToday([easy], { days_since_hard_workout: 0 }, today), easy);
  assert.equal(hardSessionToday([easy], { days_since_hard_workout: 2 }, today), null);
});

/* ── Dates ────────────────────────────────────────────────────────────── */

test("weekStart: Monday of the week, local", () => {
  assert.equal(weekStart("2026-09-07"), "2026-09-07", "a Monday is its own week start");
  assert.equal(weekStart("2026-09-13"), "2026-09-07", "Sunday belongs to the week before");
  assert.equal(weekStart("2026-09-14"), "2026-09-14");
  assert.equal(weekStart("2026-03-01"), "2026-02-23", "across a month boundary");
});

test("addDays: ISO in, ISO out, month and year boundaries hold", () => {
  assert.equal(addDays("2026-09-07", 6), "2026-09-13");
  assert.equal(addDays("2026-09-07", 0), "2026-09-07");
  assert.equal(addDays("2026-02-28", 1), "2026-03-01", "2026 is not a leap year");
  assert.equal(addDays("2026-12-31", 1), "2027-01-01");
  assert.equal(addDays("2026-09-07", -7), "2026-08-31");
  // Noon anchoring: a DST switch must not move the date by a day.
  assert.equal(addDays("2026-10-24", 1), "2026-10-25");
});

test("isoLocal reads the LOCAL day, not the UTC one", () => {
  // 00:30 local on 8 Sep is still 7 Sep in UTC east of Greenwich — the whole
  // reason this is not `toISOString().slice(0, 10)`.
  const d = new Date(2026, 8, 8, 0, 30, 0);
  assert.equal(isoLocal(d), "2026-09-08");
});

test("barLabel and weekdayShort", () => {
  assert.equal(barLabel("2026-09-07"), "07/09");
  assert.match(weekdayShort("2026-09-07"), /^Mon/);
});

test("dataAge names how old the state is", () => {
  assert.equal(dataAge({}, "2026-09-09"), null, "no day → no claim");
  assert.equal(dataAge(null, "2026-09-09"), null);
  assert.deepEqual(dataAge({ day: "2026-09-09" }, "2026-09-09"), { day: "2026-09-09", days: 0 });
  assert.deepEqual(dataAge({ day: "2026-09-08" }, "2026-09-09"), { day: "2026-09-08", days: 1 });
  assert.deepEqual(dataAge({ day: "2026-09-02" }, "2026-09-09"), { day: "2026-09-02", days: 7 });
});

/* ── Today's decision ─────────────────────────────────────────────────── */

test("lightAddendum speaks only when the light brakes", () => {
  assert.match(lightAddendum({ decision: "rest" }), /readiness light decides.*rest/);
  assert.match(lightAddendum({ decision: "easy" }), /readiness light decides.*easy/);
  assert.equal(lightAddendum({ decision: "hard" }), "");
  assert.equal(lightAddendum({ decision: "unknown" }), "");
  assert.equal(lightAddendum({}), "");
  assert.equal(lightAddendum(null), "");
});

/* ── Sessions ─────────────────────────────────────────────────────────── */

test("sport classification is substring-based, not equality", () => {
  assert.ok(isRunning("trail_running") && isRunning("treadmill_running"));
  assert.ok(!isRunning("indoor_cycling") && !isRunning(null));
  assert.ok(isStrength("strength_training") && !isStrength("running"));
});

test("shortTitle drops the HR cap, weekLabel names the type", () => {
  assert.equal(shortTitle("Easy run HR <= 145"), "Easy run");
  assert.equal(shortTitle("Easy run HR ≤145"), "Easy run");
  assert.equal(shortTitle(null), "");
  assert.equal(weekLabel("Threshold 3x8"), "Tempo");
  assert.equal(weekLabel("4x4 min VO2max"), "VO2max");
  assert.equal(weekLabel("Long run 90 min"), "Long");
  assert.equal(weekLabel("Recovery jog"), "Easy");
  assert.equal(weekLabel("Strength full body"), "Gym");
  assert.equal(weekLabel("Fartlekkerei everywhere"), "Fartlekk…", "a long first word is cut");
  assert.equal(weekLabel(""), "Run");
});

test("bandSplit folds five zones into three bands", () => {
  assert.deepEqual(bandSplit({ z1: 100, z2: 200, z3: 300, z4: 40, z5: 60 }),
                   { easy: 300, mid: 300, hard: 100, total: 700 });
  assert.deepEqual(bandSplit({}), { easy: 0, mid: 0, hard: 0, total: 0 });
});

test("zoneTimeParts drops the empty zones", () => {
  assert.deepEqual(zoneTimeParts({ z1: 60, z2: 0, z3: 0, z4: 120, z5: 0 }),
                   ["Z1 1:00", "Z4 2:00"]);
  assert.deepEqual(zoneTimeParts({}), []);
});

test("groupRunsByWeek cuts the list into consecutive weeks", () => {
  const runs = [{ day: "2026-09-13" }, { day: "2026-09-08" }, { day: "2026-08-31" }, {}];
  const g = groupRunsByWeek(runs);
  assert.deepEqual(g.map(x => [x.wk, x.runs.length]),
                   [["2026-09-07", 2], ["2026-08-31", 1], [NO_DATE, 1]]);
  assert.deepEqual(groupRunsByWeek([]), []);
});

/* ── Weekly volume ────────────────────────────────────────────────────── */

const W = (start, km, partial = false) => ({ week_start: start, distance_m: km * 1000, partial });
const weeks = [W("2026-07-13", 55), W("2026-07-20", 56), W("2026-07-27", 56), W("2026-08-03", 34),
               W("2026-08-10", 29), W("2026-08-17", 21), W("2026-08-24", 21), W("2026-08-31", 21),
               W("2026-09-07", 10, true)];

test("volumeComparison: last four full weeks against the best block before", () => {
  const u = volumeComparison(weeks, "2026-09-07");
  assert.deepEqual(u, { current_km: 23, best_km: 50.3, best_start: "2026-07-13", percent: 46 });
});

test("volumeComparison: too few weeks → null", () => {
  assert.equal(volumeComparison(weeks.slice(0, 4), "2026-09-07"), null);
  assert.equal(volumeComparison([], "x"), null);
  assert.equal(volumeComparison(null, "x"), null);
});

test("volumeComparison: the running week never counts, flagged partial or not", () => {
  const u = volumeComparison(weeks.map(w => ({ ...w, partial: false })), "2026-09-07");
  assert.equal(u.current_km, 23);
});

test("weekIncomplete: partial OR currently running", () => {
  assert.equal(weekIncomplete(W("2026-09-07", 10), "2026-09-07"), true, "the running week");
  assert.equal(weekIncomplete(W("2026-08-31", 10, true), "2026-09-07"), true, "flagged partial");
  assert.equal(weekIncomplete(W("2026-08-31", 10), "2026-09-07"), false);
});

test("volumeAverageKm counts empty weeks but no incomplete ones", () => {
  const ws = [W("2026-08-24", 40), W("2026-08-31", 0), W("2026-09-07", 10)];
  assert.equal(volumeAverageKm(ws, "2026-09-07"), 20, "0 km is a result, not a gap");
  assert.equal(volumeAverageKm([W("2026-09-07", 10)], "2026-09-07"), null);
  assert.equal(volumeAverageKm([], "2026-09-07"), null);
});

/* ── Intensity per week ───────────────────────────────────────────────── */

/* The three series are READ from the snapshot, not derived here:
   `above_easy_min` (Z3+Z4+Z5), `quality_min` (Z4+Z5), `target_min`.
   `snapshot.annotate_weeks` is the one place they are computed, and
   `tests/test_js_python_contract.py` asserts this function only reads.

   Two rounds of drift produced that rule. First this block asserted
   `mins == [10, 20]` for weeks whose above-easy time is 20 and 20 minutes,
   while its own comment claimed the Z3+Z4+Z5 reading — the test pinned the bug
   and narrated the fix. Then, with the numerator unified, the TARGET was still
   computed twice and the chart drew a bar three times over its own line beside
   a sentence saying the week was below its share. */
const IW = (start, above_easy_min, quality_min, target_min, partial = false) =>
  ({ week_start: start, above_easy_min, quality_min, target_min, partial });

test("intensitySeries reads the server's three series without recomputing", () => {
  const ws = [IW("2026-08-31", 20, 10, 16), IW("2026-09-07", 20, 20, 14)];
  const { mins, quality, targets, max } = intensitySeries(ws, 0.20);
  assert.deepEqual(mins, [20, 20], "above_easy_min, the same numerator as the Today tab");
  assert.deepEqual(quality, [10, 20], "quality_min - the stimulus inside the bar");
  assert.deepEqual(targets, [16, 14], "target_min, verbatim from the snapshot");
  assert.equal(max, 20);
  // The two weeks carry the same load and a very different stimulus. If the
  // series ever collapse into one number again, this line goes red.
  assert.notDeepEqual(mins, quality, "load and quality are two series, not one");
  // The `share` argument is for the caption only. Passing a different one must
  // not move a single bar — if it does, something started dividing again.
  assert.deepEqual(intensitySeries(ws, 0.99).targets, [16, 14]);
  // A week the server gave no target gets no line at all — an unreachable line
  // would be an alarm on a property of the source.
  assert.deepEqual(intensitySeries([IW("2026-09-07", 0, 0, 0)], 0.20).targets, [0]);
  assert.equal(intensitySeries([], 0.20).max, 1, "the axis never collapses to zero");
});

test("intensityHits counts only the finished weeks", () => {
  // One week below its line (5 of 13 min) and one above (20 of 14) - with a
  // fixture where both weeks clear the line, "counts only the finished weeks"
  // would pass no matter what the counting did.
  const ws = [IW("2026-08-31", 5, 5, 13), IW("2026-09-07", 20, 20, 14)];
  const { mins, targets } = intensitySeries(ws, 0.20);
  assert.deepEqual(mins, [5, 20]);
  assert.deepEqual(intensityHits(ws, mins, targets, "2026-09-14"), { hits: 1, full: 2 });
  assert.deepEqual(intensityHits(ws, mins, targets, "2026-09-07"), { hits: 0, full: 1 },
                   "the running week is neither a hit nor a miss");
});

/* ── Chart geometry ───────────────────────────────────────────────────── */

test("chartPoints keeps gaps as gaps", () => {
  assert.deepEqual(chartPoints([null, 50, null, 51]), [[1, 50], [3, 51]]);
  assert.deepEqual(chartPoints([]), []);
  assert.deepEqual(chartPoints(null), []);
});

test("chartScale: minSpan keeps a small movement small", () => {
  const box = { H: 118, PT: 10, PB: 18 };          // 90 px of drawable height
  const s = chartScale([[0, 50], [1, 51]], { minSpan: 3, ...box });
  assert.deepEqual([s.lo, s.hi, s.mid], [50, 51, 50.5]);
  assert.deepEqual([s.y0, s.y1], [49, 52], "a 1.0 move is drawn on a 3.0 axis");
  assert.equal(s.ys(50.5), 55, "the middle sits in the middle");
  assert.equal(s.ys(52), 10, "the top of the axis is the top of the box");
  assert.equal(s.ys(49), 100);
  // A real spread wider than minSpan wins.
  const wide = chartScale([[0, 40], [1, 60]], { minSpan: 3, ...box });
  assert.deepEqual([wide.y0, wide.y1], [40, 60]);
});

test("chartScale: invert puts the SMALLER value on top (pace)", () => {
  const box = { H: 118, PT: 10, PB: 18 };
  const s = chartScale([[0, 280], [1, 300]], { minSpan: 20, invert: true, ...box });
  assert.ok(s.ys(280) < s.ys(300), "faster is higher on the card");
});

test("stepPath holds the old value until the jump", () => {
  const xs = i => i * 10, ys = v => v;
  assert.equal(stepPath([[0, 1], [2, 3]], xs, ys), "M 0 1 L 20 1 L 20 3");
  assert.equal(stepPath([[0, 1]], xs, ys), "M 0 1");
});

test("stepMarks marks real measurements only", () => {
  const pts = [[0, 50], [1, 50], [2, 51], [3, 51]];
  assert.deepEqual(stepMarks(pts), [[2, 51]], "a carried-forward value is not a measurement");
  assert.deepEqual(stepMarks(pts, true), [[0, 50], [2, 51]], "markAll adds the first point");
});

test("axisX spreads the indices across the drawable width", () => {
  const xs = axisX(5, { W: 320, PL: 34, PR: 10 });
  assert.equal(xs(0), 34);
  assert.equal(xs(4), 310);
});

test("barScale: one slot per week", () => {
  const { bw, xOf, yOf, hOf } = barScale(4, 100, { W: 320, H: 116, PT: 16, PB: 28 });
  assert.equal(bw, 78);
  assert.deepEqual([xOf(0), xOf(1)], [4, 82]);
  assert.equal(hOf(100), 72, "a full bar uses the whole drawable height");
  assert.equal(yOf(100), 16);
  assert.equal(yOf(0), 88, "zero sits on the baseline");
});

test("carryForward holds a measurement, but invents nothing before the first", () => {
  const hist = [{ day: "2026-09-04", v: 1 }, { day: "2026-09-10", v: 2 }];
  assert.deepEqual(carryForward(["2026-09-01", "2026-09-05", "2026-09-10", "2026-09-11"], hist, "v"),
                   [null, 1, 2, 2]);
  assert.deepEqual(carryForward([], hist, "v"), []);
  assert.deepEqual(carryForward(["2026-09-05"], [], "v"), [null]);
});

/* ── Formatters (ui.js) ───────────────────────────────────────────────── */

test("formatters: decimal point, day before month, no raw pass-through", () => {
  assert.equal(fmtKm(12345), "12.3 km");
  assert.equal(fmtNum(1234.5, 1), "1,234.5");
  assert.equal(fmtDelta(0.8), "+0.8");
  assert.equal(fmtDelta(-0.34), "−0.3");
  assert.equal(fmtDelta(0), "±0");
  assert.match(fmtDay("2026-09-07"), /^Mon,? 7 Sep/);
  assert.match(fmtDayShort("2026-09-07"), /^7 Sep/);
  assert.equal(fmtDay("<img>"), "–", "an unparsable date is never passed through raw");
  assert.equal(fmtMinSec(291), "4:51");
  assert.equal(fmtPace(291), "4:51 /km");
  assert.equal(fmtPace(null), "–");
  assert.equal(fmtDur(3725), "1:02:05");
  assert.equal(fmtHours(27240), "7 h 34 min");
  assert.equal(plural(1, "run", "runs"), "1 run");
  assert.equal(plural(0, "run", "runs"), "0 runs");
  assert.equal(esc(`<a href="x">'&`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;");
});

/* ── Is there anything in the store, and is this a first run? ──────────────
   Both predicates gate user-visible state: the freshness dot, the coach
   buttons (a locked button is a spared agent run) and the first-run card.
   They were computed inside app.js, which no test can import — a review
   removed every one of these four repairs and the suite stayed green. */

const S = (o) => ({ demo: false, garmin_session: true, degraded: [],
                    data_through: null, counts: { days: 0, runs: 0, weeks: 9 }, ...o });

test("hasNoData: empty means empty, not unreadable", () => {
  // The state the first-run card is for.
  assert.equal(hasNoData(S({ garmin_session: false })), true);

  // `counts.weeks` is 9 on a TRULY empty database — get_weekly_volume emits a
  // bucket per week whether or not anything is in it. Reading emptiness off
  // `counts` at all is what this guards.
  assert.equal(hasNoData(S({ counts: { days: 0, runs: 0, weeks: 9 } })), true,
               "nine empty week buckets are not data");

  // A read failed: we do not know what is in there, so we do not claim it is
  // empty. Measured before the fix: a healthy store with two unreadable tables
  // rendered the 'never logged in' card while the banner above it said the
  // database could not be read.
  assert.equal(hasNoData(S({ degraded: ["get_daily_series", "build_runs"] })), false,
               "unreadable is not empty");

  // Activities synced, no daily_metrics row yet: data_through is null, but the
  // Runs tab is full — the header must not say 'no data yet' over it.
  assert.equal(hasNoData(S({ counts: { days: 0, runs: 12, weeks: 9 } })), false);
  assert.equal(hasNoData(S({ data_through: "2026-09-20" })), false);

  // The demo always has data by construction.
  assert.equal(hasNoData(S({ demo: true, garmin_session: false })), false);
  // Nothing at all to go on: fail towards 'empty', which only ever locks
  // buttons and shows a guide.
  assert.equal(hasNoData(undefined), true);
});

test("isFirstRun: no session AND nothing stored — an expired login is not one", () => {
  assert.equal(isFirstRun(S({ garmin_session: false })), true);

  // THE case the review caught: data in the store, token directory gone
  // (re-login in progress, or RUNSPECTOR_GARMIN_TOKENS set in one shell and not
  // the other). Not a first run — and the sync-failure banner is gated on
  // this, so getting it wrong silences a page that has stopped updating.
  assert.equal(isFirstRun(S({ garmin_session: false, data_through: "2026-09-20" })), false,
               "data + no session is an expired login, not a first run");

  // Session present, database still empty: sync has not run yet, but the
  // login step is done — no guide telling them to log in again.
  assert.equal(isFirstRun(S({ garmin_session: true })), false);

  // An older server does not send the field at all: never claim a first run.
  assert.equal(isFirstRun(S({ garmin_session: undefined })), false);
});

test("showSyncFailure: silent only on a true first run", () => {
  const failed = { ok: false, reason: "session expired" };

  // THE regression. Data in the store, token directory gone: the startup sync
  // DOES run there (server._first_run gates on empty-and-no-session), it fails,
  // and this banner is what carries that failure to the page.
  assert.equal(showSyncFailure(S({ garmin_session: false, data_through: "2026-09-20",
                                   last_sync: failed })), true,
               "a store with data must never go silent about a failed sync");

  // First run: the guide on the verdict card says it better, so no banner.
  assert.equal(showSyncFailure(S({ garmin_session: false, last_sync: failed })), false);

  // Ordinary expired session with a session directory still in place.
  assert.equal(showSyncFailure(S({ data_through: "2026-09-20", last_sync: failed })), true);
  // Nothing wrong, or nothing attempted yet.
  assert.equal(showSyncFailure(S({ last_sync: { ok: true } })), false);
  assert.equal(showSyncFailure(S({ last_sync: null })), false);
});

/* ── "Last hard session" is counted from the readiness day ─────────────── */

import { lastHardLabel } from "../src/runspector/web/static/logic.js";

test("last hard session: relative to the day the count was made on", () => {
  // `days_since_hard_workout` is counted from the readiness day. An early
  // sync writes a row for today with no recovery signal yet, so "stale_days"
  // said 0 while the count was still yesterday's - and a session from
  // yesterday was printed as "today".
  const fmt = (iso) => `[${iso}]`;
  assert.equal(lastHardLabel(0, "2026-06-09", "2026-06-10", fmt), "0 d before [2026-06-09]");
  assert.equal(lastHardLabel(0, "2026-06-10", "2026-06-10", fmt), "today");
  assert.equal(lastHardLabel(1, "2026-06-10", "2026-06-10", fmt), "yesterday");
  assert.equal(lastHardLabel(3, "2026-06-10", "2026-06-10", fmt), "3 d ago");
  assert.equal(lastHardLabel(null, "2026-06-10", "2026-06-10", fmt), "–");
  assert.equal(lastHardLabel(2, null, "2026-06-10", fmt), "–", "no readiness day, no claim");
});

/* ── setup checklist ───────────────────────────────────────────────────── */

import { setupSteps } from "../src/runspector/web/static/logic.js";

test("setup: a fresh install sees all three steps and the one command for each", () => {
  const steps = setupSteps({ garmin_session: false, claude_status: "missing", counts: {} });
  assert.deepEqual(steps.map(s => [s.key, s.done]), [["garmin", false], ["claude", false], ["sync", false]]);
  assert.match(steps[0].text, /runspector login/);
  assert.match(steps[1].text, /claude auth login/);
  assert.match(steps[1].text, /no API key/, "it says plainly that nothing else is to connect");
  assert.match(steps[2].text, /after the Garmin sign-in/);
});

test("setup: the subscription is done when the CLI says so, and the sync shows as running", () => {
  const steps = setupSteps({ garmin_session: true, claude_status: "subscription",
                             sync_state: "running", counts: {} });
  assert.equal(steps[1].done, true);
  assert.equal(steps[2].running, true);
  assert.match(steps[2].text, /last 30 days now/);
});

test("setup: nothing to set up in the demo, and a signed-out Claude stays visible with data", () => {
  assert.deepEqual(setupSteps({ demo: true }), []);
  const steps = setupSteps({ garmin_session: true, claude_status: "signed_out",
                             data_through: "2026-10-28", counts: { runs: 3 } });
  assert.deepEqual(steps.filter(s => !s.done).map(s => s.key), ["claude"]);
});

test("Garmin's load focus: each band against its own target range", async () => {
  const { loadFocusRows, loadFocusPhrase } = await import("../src/runspector/web/static/logic.js");
  const rows = loadFocusRows({
    load_aerobic_low: 115.4, load_aerobic_low_min: 115, load_aerobic_low_max: 279,
    load_aerobic_high: 0, load_aerobic_high_min: 186, load_aerobic_high_max: 350,
    load_anaerobic: 0, load_anaerobic_min: 0, load_anaerobic_max: 164,
  });
  assert.deepEqual(rows.map(r => [r.label, r.state]),
                   [["Low aerobic", "within"], ["High aerobic", "below"], ["Anaerobic", "within"]]);
  assert.equal(loadFocusRows({ load_aerobic_low: 900, load_aerobic_low_min: 363,
                               load_aerobic_low_max: 883 })[0].state, "above");
  assert.deepEqual(loadFocusRows(null), []);
  assert.equal(loadFocusPhrase("AEROBIC_HIGH_SHORTAGE"), "aerobic high shortage");
});

test("the You tab lists each fact with its evidence, escaped, and says when nothing is known", async () => {
  const { youHtml } = await import("../src/runspector/web/static/you.js");
  const html = youHtml([{ label: "Long run", value: "sat", evidence: "8 of 8 weeks",
                          change: "since about 3 weeks ago; before: sun" },
                        { label: "<b>x</b>", value: "<script>", evidence: "e", change: null }]);
  assert.match(html, /Long run/);
  assert.match(html, /since about 3 weeks ago; before: sun/);
  assert.ok(!html.includes("<script>") && html.includes("&lt;script&gt;"));
  assert.match(youHtml([]), /Nothing yet/);
});

test("the You tab's week: history fills it, the athlete's setting wins and says so", async () => {
  const { youHtml, effective } = await import("../src/runspector/web/static/you.js");
  const week = { set: { long_run_day: "sun", off_days: ["thu"] }, note: "", note_max: 600,
                 history: { days_per_week: 3, run_days: ["tue", "thu", "sat"], long_run_day: "sat",
                            off_days: [], sport_days: ["tue"] },
                 effective: { days_per_week: { value: 3, source: "history" },
                              run_days: { value: ["tue", "sat"], source: "history" },
                              long_run_day: { value: "sun", source: "you" },
                              off_days: { value: ["thu"], source: "you" },
                              sport_days: { value: ["tue"], source: "history" } } };
  assert.deepEqual(effective(week, "long_run_day"), { value: "sun", source: "you" });
  assert.deepEqual(effective(week, "days_per_week"), { value: 3, source: "history" });
  const html = youHtml([{ key: "long_run", label: "Long run", value: "sun",
                          evidence: "set by you; your history says sat" }], week);
  assert.match(html, /set by you/);
  assert.match(html, /from your history/);
  assert.match(html, /day-long[\s\S]*Sun/);            // the strip marks the long run
  assert.match(html, /day-off/);
  assert.match(html, /data-week-edit/);
  assert.match(html, /For your coach/);
});

test("the week editor: only what the athlete touched is sent; a day off moves the history's days", async () => {
  const { newDraft, pick, draftPatch, youHtml } =
    await import("../src/runspector/web/static/you.js");
  const week = { set: {}, note: "",
                 history: { days_per_week: 3, run_days: ["tue", "thu", "sat"], long_run_day: "sat",
                            off_days: [], sport_days: null } };
  let d = newDraft(week);
  assert.deepEqual(draftPatch(d), {});
  // Review finding: a day off saved the history's running days as the athlete's own.
  d = pick(d, "off_days", "thu", week);
  assert.deepEqual(d.values.run_days, ["tue", "sat"]);          // the preview follows the planner
  assert.deepEqual(draftPatch(d), { off_days: ["thu"] });       // ...but only the day off is theirs
  // The history's long-run day can be taken off: the long run moves, weekend first.
  d = pick(d, "off_days", "sat", week);
  assert.equal(d.values.long_run_day, "sun");
  assert.deepEqual(draftPatch(d), { off_days: ["thu", "sat"] });
  assert.doesNotMatch(youHtml([], week, { draft: newDraft(week) }),
                      /data-week-pick="off_days" data-value="sat"\s+aria-pressed="false" disabled/);
  d = pick(d, "days_per_week", "4", week);
  assert.equal(draftPatch(d).days_per_week, 4);
  d = pick(d, "off_days", "thu", week);
  d = pick(d, "off_days", "sat", week);                         // untoggled: no days off is no setting
  assert.equal(draftPatch(d).off_days, null);
  assert.equal(d.values.long_run_day, "sat");
});

test("the week editor: the athlete's own values are kept and handed back to the history", async () => {
  const { newDraft, pick, resetRow, draftPatch, youHtml } =
    await import("../src/runspector/web/static/you.js");
  const week = { set: { long_run_day: "sun", run_days: ["tue", "thu"], off_days: ["sat"] }, note: "",
                 history: { days_per_week: 3, run_days: ["tue", "thu", "sat"], long_run_day: "sat",
                            off_days: [], sport_days: null } };
  let d = newDraft(week);
  // A running day of their own that becomes a day off leaves the list they SEE;
  // the save carries only the day off (the server takes it out of their days).
  d = pick(d, "off_days", "thu", week);
  assert.deepEqual(d.values.run_days, ["tue"]);
  assert.deepEqual(draftPatch(d), { off_days: ["thu", "sat"] });
  // Round 2: tapping the day off again used to cost them Thursday for good.
  d = pick(d, "off_days", "thu", week);
  assert.deepEqual(d.values.run_days, ["tue", "thu"]);
  assert.equal(draftPatch(d).run_days, undefined);
  // Runs a week of their own that would not fit next to another day off: blocked.
  const tight = { ...week, set: { ...week.set, days_per_week: 6 } };
  assert.match(youHtml([], tight, { draft: newDraft(tight) }),
               /data-week-pick="off_days" data-value="mon"\s+aria-pressed="false" disabled/);
  // A long-run day they set cannot be taken off; a day off cannot be the long run.
  const html = youHtml([], week, { draft: newDraft(week) });
  assert.match(html, /data-week-pick="off_days" data-value="sun"\s+aria-pressed="false" disabled/);
  assert.match(html, /data-week-pick="long_run_day" data-value="sat"\s+aria-pressed="false" disabled/);
  // Review finding: "use my history" showed Sat while Sat is a day off; the planner says Sun.
  d = resetRow(newDraft(week), week, "long_run_day");
  assert.equal(d.values.long_run_day, "sun");
  assert.equal(draftPatch(d).long_run_day, null);
  assert.match(youHtml([], week, { draft: d }), /data-week-save\s*>/);
});

test("the note to the coach is escaped and keeps what is being typed", async () => {
  const { youHtml } = await import("../src/runspector/web/static/you.js");
  const week = { set: {}, note: "<b>gym</b>", note_max: 600, history: {} };
  const html = youHtml([], week);
  assert.ok(!html.includes("<b>gym</b>") && html.includes("&lt;b&gt;gym&lt;/b&gt;"));
  assert.match(html, /data-note-save disabled/);         // nothing changed yet
  const typing = youHtml([], week, { note: "less time" });
  assert.match(typing, />less time<\/textarea>/);
  assert.match(typing, /data-note-save >/);
});

test("setup: an API-key sign-in counts as done only when the athlete allowed it", () => {
  const base = { garmin_session: true, claude_status: "other", counts: {} };
  const refused = setupSteps(base)[1];
  assert.equal(refused.done, false);
  assert.match(refused.text, /billed there/, "the cards already run on it, and say so");
  assert.doesNotMatch(refused.text, /RUNSPECTOR_ALLOW_API_KEY/,
                      "the switch would only add a per-token bill for anyone with a plan");
  const allowed = setupSteps({ ...base, claude_api_key_allowed: true })[1];
  assert.equal(allowed.done, true);
  assert.match(allowed.text, /billed there/);
});

import { coachReassurance } from "../src/runspector/web/static/logic.js";

test("the note under the coach buttons promises no bill only on a plan", () => {
  assert.match(coachReassurance({ claude_status: "subscription" }), /no bill per card/);
  for (const allowed of [false, true]) {
    const note = coachReassurance({ claude_status: "other", claude_api_key_allowed: allowed });
    assert.doesNotMatch(note, /no bill per card/, "a key or cloud sign-in bills every card");
    assert.match(note, /billed per token/);
  }
  assert.doesNotMatch(coachReassurance({ claude_status: "unknown" }), /no bill per card/,
                      "a check that said nothing confirms no plan");
});

test("monotony: the line says what the server's number means, and when the planner holds", async () => {
  const { monotonyLine } = await import("../src/runspector/web/static/logic.js");
  assert.equal(monotonyLine(null), "");
  assert.equal(monotonyLine({ monotony: null }), "");
  assert.match(monotonyLine({ monotony: 1.2, load: 400, base: 420 }), /1\.2 - enough difference/);
  assert.match(monotonyLine({ monotony: 2.4, load: 600, base: 500 }), /much alike; .*does not step the next one up/);
  assert.doesNotMatch(monotonyLine({ monotony: 2.4, load: 300, base: 500 }), /step/);
  assert.match(monotonyLine({ monotony: 10, load: 560, base: 560 }), /every day alike/);
  assert.match(monotonyLine({ monotony: 2.04, load: 600, base: 500 }), /days: 2\.04 - the days were much alike/,
    "2.04 is above the line and must read so - not as 2.0");
  assert.doesNotMatch(monotonyLine({ monotony: 2.0, load: 900, base: 500 }), /much alike/,
    "2.0 itself is not above the line - the same comparison as the Python");
});

test("Garmin's readiness: shown as Garmin's opinion beside the light, absent without one", async () => {
  const { garminReadinessLine } = await import("../src/runspector/web/static/logic.js");
  assert.equal(garminReadinessLine({}), "");
  assert.equal(garminReadinessLine(null), "");
  assert.equal(garminReadinessLine({ garmin_readiness: null, garmin_readiness_level: "HIGH" }), "");
  assert.equal(garminReadinessLine({ garmin_readiness: 0 }).startsWith("Garmin's own Training Readiness: 0 -"), true,
    "0 is a score, not a missing one");
  assert.match(garminReadinessLine({ garmin_readiness: 85, garmin_readiness_level: "HIGH" }),
    /Readiness: 85 \(high\) - its score; the call above is this app's/);
});

test("weight: the line under VO2max, with the weight's own share once it is a kilogram", async () => {
  const { weightLine } = await import("../src/runspector/web/static/logic.js");
  assert.equal(weightLine(null), "");
  assert.equal(weightLine({ kg: 71.4, change_kg: null }), "Body weight 71.4 kg");
  assert.equal(weightLine({ kg: 71.4, change_kg: -0.6, since: "2026-09-28", vo2max_effect: null }),
    "Body weight 71.4 kg (-0.6 kg since 2026-09-28)");
  assert.match(weightLine({ kg: 71.4, change_kg: -1.6, since: "2026-09-28", vo2max_effect: 1.1 }),
    /\(-1\.6 kg since 2026-09-28\) - VO2max is per kilogram, so the weight alone moves it by about \+1\.1$/);
  assert.match(weightLine({ kg: 74, change_kg: 2.1, since: "2026-09-28", vo2max_effect: -1.4 }),
    /\(\+2\.1 kg .* about -1\.4$/);
});

test("sync on return: only when the data is older than the server's threshold", async () => {
  const { syncOnReturn } = await import("../src/runspector/web/static/logic.js");
  const synced = "2026-10-30T06:00:00+00:00";
  const at = min => Date.parse(synced) + min * 60_000;
  const s = { synced_at: synced, sync_after_min: 30, garmin_session: true, sync_state: "idle" };
  assert.equal(syncOnReturn(s, at(29)), false);
  assert.equal(syncOnReturn(s, at(31)), true);
  assert.equal(syncOnReturn({ ...s, sync_after_min: 120 }, at(90)), false);
  assert.equal(syncOnReturn({ ...s, demo: true }, at(600)), false, "never in the demo");
  assert.equal(syncOnReturn({ ...s, garmin_session: false }, at(600)), false, "no session, no sync");
  assert.equal(syncOnReturn({ ...s, sync_state: "running" }, at(600)), false, "one sync at a time");
  assert.equal(syncOnReturn({ ...s, synced_at: null }, at(600)), false, "nothing synced yet: the setup says what");
  assert.equal(syncOnReturn({ ...s, synced_at: "garbage" }, at(600)), false);
  assert.equal(syncOnReturn({ ...s, sync_after_min: undefined }, at(31)), true, "30 minutes by default");
});

test("sync on return: off at 0, and no new attempt within the threshold of a failed one", async () => {
  const { syncOnReturn } = await import("../src/runspector/web/static/logic.js");
  const synced = "2026-10-30T06:00:00+00:00";
  const at = min => Date.parse(synced) + min * 60_000;
  const s = { synced_at: synced, sync_after_min: 30, garmin_session: true, sync_state: "idle" };
  // Review finding: RUNSPECTOR_AUTOSYNC_MIN=0 means "off" to the tools.
  assert.equal(syncOnReturn({ ...s, sync_after_min: 0 }, at(600)), false);
  assert.equal(syncOnReturn({ ...s, sync_after_min: -5 }, at(600)), false);
  // Review finding: a failed sync moves no data, so `synced_at` stays old and
  // every glance used to start another login into a rate-limited account.
  const failedAt = new Date(at(120)).toISOString();
  const failed = { ...s, sync_state: "error", last_sync: { ok: false, at: failedAt } };
  assert.equal(syncOnReturn(failed, at(125)), false, "five minutes after a failure");
  assert.equal(syncOnReturn(failed, at(151)), true, "a threshold after it, once more");
});

test("check-in: a button per kind, a line for each that holds, the easy days after", async () => {
  const { checkInView, CHECK_IN_KINDS } = await import("../src/runspector/web/static/logic.js");
  const day = iso => `<${iso}>`;
  const empty = checkInView(null, "2026-06-10", day);
  assert.deepEqual(empty.chips.map(c => [c.kind, c.pressed]),
    CHECK_IN_KINDS.map(k => [k, false]));
  assert.deepEqual(empty.lines, []);
  assert.equal(empty.after, "");
  const v = checkInView({
    open: [{ kind: "sore", since: "2026-06-10", last_said: "2026-06-10" },
           { kind: "ill", since: "2026-06-08", last_said: "2026-06-09" }],
    after: null,
  }, "2026-06-10", day);
  assert.deepEqual(v.chips.filter(c => c.pressed).map(c => c.kind), ["ill", "sore"]);
  assert.deepEqual(v.lines, [
    { kind: "ill", text: "Ill since <2026-06-08> - no run while it holds", saidToday: false,
      undoable: false, ending: false },
    { kind: "sore", text: "Sore legs since today - no hard session while it holds", saidToday: true,
      undoable: true, ending: false },
  ]);
  assert.equal(checkInView({ open: [], after: { kind: "pain", days: 1, until: "2026-06-11" } },
    "2026-06-10", day).after,
    "In pain for 1 day: easy days only, the hard sessions come back on <2026-06-11>");
});

test("check-in toast: easy days only after an illness or a pain", async () => {
  const { checkInToast } = await import("../src/runspector/web/static/logic.js");
  assert.match(checkInToast("ill", "over"), /first days back are easy/);
  assert.doesNotMatch(checkInToast("sore", "over"), /easy/);
  assert.match(checkInToast("pain", "over tomorrow"), /today still counts; tomorrow is the first day back, and it is easy/);
  assert.doesNotMatch(checkInToast("stressed", "over tomorrow"), /easy/);
  assert.match(checkInToast("ill", "taken back"), /Taken back/);
  assert.match(checkInToast("ill", "new"), /today's call goes by it/);
});

test("lightAddendum: a day the athlete's word decided is not the light's call", async () => {
  const { lightAddendum } = await import("../src/runspector/web/static/logic.js");
  assert.equal(lightAddendum({ decision: "rest", reason: "check-in ill" }), " Today what you said decides: rest.");
  assert.equal(lightAddendum({ decision: "easy", reason: "after pain" }), " Today what you said decides: easy.");
  assert.match(lightAddendum({ decision: "rest", reason: "verdict REST" }), /readiness light decides/);
});

test("check-in: a state said over today shows when it ends, no second 'over'", async () => {
  const { checkInView } = await import("../src/runspector/web/static/logic.js");
  const v = checkInView({ open: [{ kind: "ill", since: "2026-06-10", last_said: "2026-06-10",
                                   over: "2026-06-11" }], after: null }, "2026-06-10", d => `<${d}>`);
  assert.equal(v.lines[0].text, "Ill since today - over from <2026-06-11>");
  assert.equal(v.lines[0].ending, true);
});

test("check-in: a button tapped today is a switch - the view says it can be taken back", async () => {
  const { checkInView } = await import("../src/runspector/web/static/logic.js");
  const day = d => `<${d}>`;
  const v = checkInView({ open: [
    { kind: "ill", since: "2026-06-10", last_said: "2026-06-10", over: "2026-06-11" },
    { kind: "pain", since: "2026-06-08", last_said: "2026-06-10", over: null },
  ], after: null }, "2026-06-10", day);
  const chip = k => v.chips.find(c => c.kind === k);
  // Tapped today, even after "It's over": a second tap or "Said by mistake" takes it back.
  assert.equal(chip("ill").undoable, true);
  assert.equal(v.lines.find(l => l.kind === "ill").undoable, true);
  // Said on an earlier day: a tap says it again, nothing to take back.
  assert.equal(chip("pain").undoable, false);
  assert.equal(chip("sore").undoable, false);
});
