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
  barScale, carryForward,
} from "../src/runcoach/web/static/logic.js";
import { fmtKm, fmtNum, fmtDelta, fmtDay, fmtDayShort, fmtMinSec, fmtPace, fmtDur,
         fmtHours, plural, esc } from "../src/runcoach/web/static/ui.js";

/* ── Hard sessions ────────────────────────────────────────────────────── */

/* These three numbers are a COPY of `src/runcoach/logic.py: is_hard`. This is
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
