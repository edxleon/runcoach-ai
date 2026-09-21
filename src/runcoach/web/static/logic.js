/* ═══════════════════════════════════════════════════════════════════════════
   Pure page logic — no DOM, no fetch, no module state.

   THE RULE for this file, so that nobody has to guess where something goes:
   a function belongs here if it only reads its arguments and returns a value.
   Touching `document`, `window`, the network or the page's `state` variable
   disqualifies it — that half lives in `app.js`, which imports from here.
   Everything in this file is unit-tested under Node (web-tests/logic.test.mjs);
   nothing in `app.js` can be, because importing it starts the page.

   Only DISPLAY arithmetic lives here (comparing and laying out numbers the
   snapshot already contains). Verdicts and classifications come finished from
   the server; the page never re-derives them. The ONE exception is
   `isHardSession` — and it is exported precisely so the node test can pin its
   numbers against the server's.

   Imports are relative (`./ui.js`), not `/static/ui.js`: Node does not know
   the server root, and this module is imported by tests.
   ═══════════════════════════════════════════════════════════════════════════ */

import { LOCALE, esc, fmtDur } from "./ui.js";

/* ── Dates ────────────────────────────────────────────────────────────── */

/** LOCAL date, not UTC: `toISOString()` still returns the previous day for an
 *  hour or two after midnight in zones east of Greenwich — exactly in that
 *  window the "this is yesterday's state" notice would be missing and the app
 *  would show the previous day's verdict as today's. */
export function isoLocal(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-`
       + `${String(d.getDate()).padStart(2, "0")}`;
}

export function todayIso() { return isoLocal(new Date()); }

/** Monday of the week for an ISO date. */
export function weekStart(iso) {
  const d = new Date(iso + "T12:00:00");
  const offset = (d.getDay() + 6) % 7;           // Mon=0 … Sun=6
  d.setDate(d.getDate() - offset);
  return isoLocal(d);                            // local, not UTC (see above)
}

/** ISO date `n` days after `iso`. */
export function addDays(iso, n) {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + n);
  return isoLocal(d);
}

/** "07/09" under a weekly bar — day first, like every other date here. */
export const barLabel = iso => `${iso.slice(8)}/${iso.slice(5, 7)}`;

export const weekdayShort = iso =>
  new Date(iso + "T12:00:00").toLocaleDateString(LOCALE, { weekday: "short" });

/** The most dangerous state of this app: the tab says "Today", but the numbers
 *  are from yesterday (computer off, sync stuck, watch not synchronised).
 *  Reading yesterday's verdict as today's is precisely the mistake this page
 *  exists to prevent — so it is named, not papered over.
 *  `today` is the snapshot's `today` block, `nowIso` the current local day. */
export function dataAge(today, nowIso) {
  const d = (today || {}).day;
  if (!d) return null;
  const days = Math.round((new Date(nowIso) - new Date(d)) / 86400000);
  return { day: d, days };
}

/* ── Is there anything in the store at all? ───────────────────────────────
   Three surfaces ask this: the freshness dot in the header, the coach buttons
   (an analysis of nothing spends a real agent run), and the first-run card.
   They live here rather than in `app.js` so they can be pinned by
   web-tests/logic.test.mjs — `app.js` is a DOM module no test imports.

   `counts` alone is the wrong oracle, and both halves of that were measured:
   `counts.weeks` is 9 even on an empty database (`get_weekly_volume` emits a
   bucket per week regardless), and `counts.days`/`counts.runs` are ZEROED by
   `soft()` when a read fails — so a full, healthy store with two unreadable
   tables rendered as a brand-new install telling the athlete to log in, while
   the banner above it said the database could not be read. `data_through`
   comes from `latest_day()`, which `snapshot.assemble` deliberately keeps
   OUTSIDE `soft()` as a file-level probe, so it cannot be faked by a soft
   failure. The runs count covers the converse case (activities synced, no
   `daily_metrics` row yet), and a non-empty `degraded` list vetoes the whole
   judgement: when we could not read, we do not know. */
export function hasNoData(s) {
  const st = s || {};
  if (st.demo) return false;
  if ((st.degraded || []).length) return false;   // unreadable ≠ empty
  return !st.data_through && !((st.counts || {}).runs);
}

/** The state before `runcoach login`: no session AND nothing stored. A store
 *  with data and no session is an EXPIRED login, which is a different message
 *  (and must keep its sync-failure banner). */
export function isFirstRun(s) {
  return (s || {}).garmin_session === false && hasNoData(s);
}

/** Does the page owe the athlete a "the sync did not go through" banner?
 *
 *  Suppressed on a true first run only, where the guide says the same thing
 *  in a friendlier order. Gating it on `garmin_session === false` alone was
 *  the sharpest defect of this change: with data in the store and the token
 *  directory gone, the banner vanished, `serve()` skipped the startup sync,
 *  and the page showed a normal verdict over data that had stopped updating —
 *  reproduced in a browser, green dot and all. The decision lives here rather
 *  than inside `renderHealth` because a test cannot import `app.js`, and a
 *  reverted gate went unnoticed by the entire suite. */
export function showSyncFailure(s) {
  const st = s || {};
  return !!(st.last_sync && st.last_sync.ok === false) && !isFirstRun(st);
}

/* ── Hard sessions ────────────────────────────────────────────────────── */

/* The authority for "hard" is `src/runcoach/logic.py: is_hard` — the same
   predicate `days_since_hard_workout` is derived from. The numbers below are
   a COPY of it, and a copy drifts; what keeps it honest is
   web-tests/logic.test.mjs, which pins each of the three thresholds and their
   boundaries. If the server's values move, that test goes red and this file
   has to follow. A quietly diverging second definition would put two meanings
   of "hard" on one screen: a run could be "last hard session: today" in the
   signal cell and "still open" in the coach line at the same time. */
export const HARD_AEROBIC_TE = 3.0;
export const HARD_ANAEROBIC_TE = 2.0;
export const HARD_DURATION_S = 4200;             // 70 min — long RUNS are hard days too

/** Whole calendar weeks between two Monday ISO dates. The aerobic trend is a
 *  slope per CALENDAR week, so turning it into a total needs the span, not the
 *  number of data points — weeks without a qualifying easy run are missing. */
export function weeksBetween(firstMonday, lastMonday) {
  const a = Date.parse(`${firstMonday}T00:00:00Z`), b = Date.parse(`${lastMonday}T00:00:00Z`);
  if (isNaN(a) || isNaN(b)) return 0;
  return Math.max(0, Math.round((b - a) / (7 * 24 * 3600 * 1000)));
}


/** Does this session count as a hard day?
 *
 *  A high training effect counts in any sport. Sheer length counts only for a
 *  run: an 80-minute walk or a long easy commute ride is not a reason to cancel
 *  tomorrow's intervals. */
export function isHardSession(run) {
  if (!run) return false;
  const running = String(run.type || run.activity_type || "").toLowerCase().includes("running");
  return (run.aerobic_te != null && run.aerobic_te >= HARD_AEROBIC_TE)
      || (run.anaerobic_te != null && run.anaerobic_te >= HARD_ANAEROBIC_TE)
      || (running && run.duration_s != null && run.duration_s >= HARD_DURATION_S);
}

/** The hard session of TODAY, if there is one. `signals` is the snapshot's
 *  `today.signals`: what the server already computed takes priority over the
 *  local derivation. */
export function hardSessionToday(runs, signals, nowIso) {
  const r = (runs || [])[0];
  if (!r || r.day !== nowIso) return null;
  if ((signals || {}).days_since_hard_workout === 0) return r;
  return isHardSession(r) ? r : null;
}

/* ── Today's decision ─────────────────────────────────────────────────── */

export const DECISION = { hard: "Hard", easy: "Easy", rest: "Rest", unknown: "No call" };

/** So that the intensity card does not push towards "more" on its own: if
 *  today's decision is rest/easy, the line says so — the bar counts, the
 *  readiness light decides (recovery takes priority). Takes the snapshot's
 *  `decision_today`. */
export function lightAddendum(decision) {
  const e = decision || {};
  if (e.decision !== "rest" && e.decision !== "easy") return "";
  // Do NOT repeat the server's sentence here: it names the light itself, and
  // the two would read as a stutter. The verdict word is enough; the full
  // sentence is on the Today tab.
  const word = (DECISION[e.decision] || e.decision || "").toLowerCase();
  return ` Today the readiness light decides: it says ${esc(word)}.`;
}

/* ── Sessions ─────────────────────────────────────────────────────────── */

export const isRunning = t => /running/i.test(t || "");
export const isStrength = t => /strength/i.test(t || "");

export function shortTitle(t) {
  return String(t || "").replace(/\s+HR\s*[≤<]=?\s*\d+/i, "");
}

/** Label for the ~48 px day cell: the TYPE of the session, not its title — a
 *  full workout title wraps in the middle of a word there. The full title is
 *  in the cell's tooltip. Labels are kept short for the same reason. */
export function weekLabel(t) {
  const s = shortTitle(t);
  if (/threshold|tempo/i.test(s)) return "Tempo";
  if (/vo2|interval|\d+\s*[x×]\s*\d/i.test(s)) return "VO2max";
  if (/long/i.test(s)) return "Long";
  if (/easy|z2|recovery/i.test(s)) return "Easy";
  if (/strength/i.test(s)) return "Gym";
  const w = s.split(/\s+/)[0] || "Run";
  return w.length > 9 ? w.slice(0, 8) + "…" : w;
}

/** Three bands out of five zones — see the `bandBar` note in app.js. */
export function bandSplit(z) {
  const easy = (z.z1 || 0) + (z.z2 || 0), mid = z.z3 || 0,
        hard = (z.z4 || 0) + (z.z5 || 0);
  return { easy, mid, hard, total: easy + mid + hard };
}

/** Zone times as text — empty zones are dropped. "Z4 0:00 · Z5 0:00" on an
 *  easy run is not information, it is two entries of noise. */
export function zoneTimeParts(z) {
  return [1, 2, 3, 4, 5]
    .filter(i => (z["z" + i] || 0) > 0)
    .map(i => `Z${i} ${fmtDur(z["z" + i])}`);
}

/** Runs (newest first) cut into consecutive week blocks. Runs without a day
 *  land in a `"no-date"` group. */
export const NO_DATE = "no-date";

export function groupRunsByWeek(runs) {
  const groups = [];
  for (const r of runs || []) {
    const wk = r.day ? weekStart(r.day) : NO_DATE;
    if (!groups.length || groups[groups.length - 1].wk !== wk) groups.push({ wk, runs: [] });
    groups[groups.length - 1].runs.push(r);
  }
  return groups;
}

/* ── Weekly volume ────────────────────────────────────────────────────── */

/** Volume of the last `n` completed weeks against the best `n`-week block
 *  before them. Returns `{current_km, best_km, best_start, percent}` or null
 *  (too few weeks). `percent` = current / best · 100, rounded.
 *
 *  The weekly bars show a drop like 55 → 21 km, but bars alone never say the
 *  number — and it is exactly this number that explains a high ACWR (a small
 *  denominator, not a large numerator).
 *
 *  The running week never counts, even if it is not flagged `partial`. */
export function volumeComparison(weeks, currentWeekStart, n = 4) {
  const done = (weeks || []).filter(w => w && !w.partial && w.week_start !== currentWeekStart
                                         && typeof w.distance_m === "number");
  if (done.length < n + 1) return null;
  const km = w => (w.distance_m || 0) / 1000;
  const mean = arr => arr.reduce((a, w) => a + km(w), 0) / arr.length;
  const last = done.slice(-n);
  let best = null;
  for (let i = 0; i + n <= done.length - n; i++) {
    const block = done.slice(i, i + n);
    const m = mean(block);
    if (!best || m > best.km) best = { km: m, start: block[0].week_start };
  }
  if (!best) return null;
  const current = mean(last);
  return {
    current_km: Math.round(current * 10) / 10,
    best_km: Math.round(best.km * 10) / 10,
    best_start: best.start,
    percent: best.km > 0 ? Math.round(100 * current / best.km) : null,
  };
}

/** A week is incomplete if the server flagged it `partial` OR it is the one
 *  currently running. */
export const weekIncomplete = (w, currentWeekStart) =>
  Boolean(w.partial) || w.week_start === currentWeekStart;

/** Average km over the COMPLETE weeks: neither the running one nor one cut off
 *  by the window edge counts. Empty weeks DO count (they are a result, not a
 *  missing value) — otherwise it would be the average of the training weeks,
 *  not of the last eight. */
export function volumeAverageKm(weeks, currentWeekStart) {
  const done = (weeks || []).filter(w => !weekIncomplete(w, currentWeekStart));
  if (!done.length) return null;
  return done.reduce((a, w) => a + (w.distance_m || 0), 0) / done.length / 1000;
}

/* ── Intensity per week ───────────────────────────────────────────────── */

/* DENOMINATOR = the measured zone time (easy + moderate + hard), NOT
   `duration_s`: that sums ALL activities including strength training — a gym
   session would raise the bar for running intensity. And runs WITHOUT zone
   detail deliver hard_s = 0 at a full denominator; the line would be
   unreachable for data reasons: an alarm on a property of the source. The zone
   sum counts numerator and denominator from the same measurement. */
const zoneSeconds = w => (w.easy_s || 0) + (w.moderate_s || 0) + (w.hard_s || 0);

/** The three series of the intensity chart, READ from the snapshot:
 *  `above_easy_min` (Z3+Z4+Z5), `quality_min` (Z4+Z5) and `target_min`.
 *
 *  Nothing is computed here any more, and that is the point. This function used
 *  to derive all three, and each one drifted from the server in turn: first the
 *  numerator (the chart drew Z4+Z5 while the Today tab printed Z3+Z4+Z5 - 13 min
 *  against 17 for one week), then, after that was fixed, the TARGET - the chart
 *  divided by the week's own zone time while `build_decision` anchors an
 *  incomplete week on a typical one, so the same week showed a bar three times
 *  over its line beside a sentence saying it was below its share.
 *
 *  `snapshot.annotate_weeks` computes them once; `tests/test_js_python_contract.py`
 *  asserts this function reads rather than derives. The `share` argument is kept
 *  for the axis caption only - it is no longer used to divide anything. */
export function intensitySeries(weeks, _share) {
  const mins = (weeks || []).map(w => w.above_easy_min ?? 0);
  const quality = (weeks || []).map(w => w.quality_min ?? 0);
  const targets = (weeks || []).map(w => w.target_min ?? 0);
  return { mins, quality, targets, max: Math.max(1, ...mins, ...targets) };
}

/** How many of the FINISHED weeks reached their own target line. */
export function intensityHits(weeks, mins, targets, currentWeekStart) {
  let hits = 0, full = 0;
  (weeks || []).forEach((w, i) => {
    if (weekIncomplete(w, currentWeekStart)) return;
    full += 1;
    if (targets[i] > 0 && mins[i] >= targets[i]) hits += 1;
  });
  return { hits, full };
}

/* ── Chart geometry ───────────────────────────────────────────────────── */

/** Series → drawable points `[index, value]`, gaps dropped. */
export function chartPoints(vals) {
  const pts = [];
  (vals || []).forEach((v, i) => { if (v != null) pts.push([i, v]); });
  return pts;
}

/** X position per index across `n` slots. */
export const axisX = (n, { W, PL, PR }) => i => PL + (i / (n - 1)) * (W - PL - PR);

/** Y axis for a step curve. `minSpan` keeps small movements small: a
 *  carried-forward VO2max often moves 1.0 point, and an axis spanning exactly
 *  the existing range stretched that over the full card height — it looked
 *  like a rocket where one decimal had moved. `invert` flips the axis, for a
 *  pace in seconds/km where LESS is better and belongs at the top. */
export function chartScale(pts, { minSpan = 3, invert = false, H, PT, PB }) {
  const lo = Math.min(...pts.map(p => p[1])), hi = Math.max(...pts.map(p => p[1]));
  const mid = (lo + hi) / 2;
  const span = Math.max(hi - lo, minSpan);
  const y0 = mid - span / 2, y1 = mid + span / 2;
  const share = v => (v - y0) / (y1 - y0);
  const ys = v => PT + (invert ? share(v) : 1 - share(v)) * (H - PT - PB);
  return { lo, hi, y0, mid, y1, ys };
}

/** The `d` of a STEP path: hold the old value until the new x, then jump. */
export function stepPath(pts, xs, ys) {
  let d = `M ${xs(pts[0][0])} ${ys(pts[0][1])}`;
  for (let k = 1; k < pts.length; k++) {
    d += ` L ${xs(pts[k][0])} ${ys(pts[k - 1][1])} L ${xs(pts[k][0])} ${ys(pts[k][1])}`;
  }
  return d;
}

/** Points that are a REAL measurement: the value changed. With `markAll` the
 *  first point counts as one too. */
export function stepMarks(pts, markAll = false) {
  return pts.filter((p, k) => (k === 0 && markAll) || (k > 0 && p[1] !== pts[k - 1][1]));
}

/** Bar geometry for the weekly charts: one slot per week. */
export function barScale(count, max, { W, H, PT, PB }) {
  const bw = (W - 8) / count;
  return {
    bw,
    xOf: i => 4 + i * bw,
    hOf: v => (v / max) * (H - PT - PB),
    yOf: v => H - PB - (v / max) * (H - PT - PB),
  };
}

/** Carry a measurement series forward onto `days`: on each day the newest
 *  measurement up to then holds. Before the FIRST measurement the series stays
 *  empty (a gap, not 0). `hist` must be sorted oldest first. */
export function carryForward(days, hist, key) {
  const out = [];
  let k = -1;
  for (const d of days || []) {
    while (k + 1 < hist.length && hist[k + 1].day <= d) k++;
    out.push(k >= 0 ? hist[k][key] : null);
  }
  return out;
}
