/* ═══════════════════════════════════════════════════════════════════════════
   The page.

   Everything here renders, holds state or listens to the DOM — importing this
   module STARTS the app, which is why nothing in it can be unit-tested. The
   arithmetic it needs lives in `./logic.js` (pure, argument-in/value-out,
   pinned by web-tests/logic.test.mjs); the frame lives in `./chassis.js`, the
   coach cards in `./cards.js`, the formatters in `./ui.js`.

   That is the whole rule: if a function only reads its arguments, it belongs
   in logic.js. If it touches `document`, `state` or the network, it belongs
   here.

   Imports are relative so that Node can resolve them the same way the browser
   does from /static/app.js.
   ═══════════════════════════════════════════════════════════════════════════ */
"use strict";
import {
  esc, fmtDur, fmtHours, fmtPace, fmtKm, fmtNum, fmtDelta, fmtDay, fmtDayShort,
  fmtMinSec, plural, cell, toast, armOrFire, resetArm, apiGet, apiPost,
  progressInfo, progressEnd,
} from "./ui.js";
import { mountApp } from "./chassis.js";
import {
  addDays, axisX, bandSplit, barLabel, barScale, carryForward, chartPoints, chartScale, dataAge as ageOf, groupRunsByWeek, hardSessionToday as hardSessionOf, hasNoData, intensityHits, intensitySeries, isFirstRun, showSyncFailure, isRunning, isStrength, lightAddendum as lightAddendumOf, NO_DATE, shortTitle, stepMarks, stepPath, todayIso, volumeAverageKm, volumeComparison, weekdayShort, weekIncomplete, weekLabel, weeksBetween, weekStart, zoneTimeParts,
} from "./logic.js";
import { mountCards, mountJobs, jobPoller, todayCard, handle409,
         setCardContext } from "./cards.js";

/* The /api/state answer IS the snapshot, plus `templates`, `jobs`, `cards`,
   `demo` and `claude_available` next to the snapshot keys. */
let state = null;
let openRun = null;

const D = () => state || {};

/* ═══ Verdict ══════════════════════════════════════════════════════════════
   The readiness light comes finished from the data (the same function the
   coach agent reads) — here it is only written large. No second rule set in
   the frontend: otherwise the app and the coach claim different verdicts.
   The COLOUR is deliberately not a system traffic light: "easy" is not a
   malfunction. */
const VERDICT = {
  GO:   { word: "Go",   cls: "i-go",   sub: "room for a real stimulus today" },
  EASY: { word: "Easy", cls: "i-easy", sub: "keep it calm today" },
  REST: { word: "Rest", cls: "i-rest", sub: "not today" },
};

/* Dates, the hard-session predicate and all chart arithmetic come from
   ./logic.js — these are only the snapshot-reading wrappers around them. */

/* THE day this page is about — the server's, not the browser's.
   `snapshot.assemble` runs in `RUNCOACH_TZ`, which is not necessarily the
   viewer's zone, and a page left open over midnight keeps a wall clock that has
   already moved on. Both put the "this week" highlight, the staleness banner
   and the today-card match on a different day from every number beside them.
   The wall clock stays as the fallback for a state that has not loaded yet. */
const appToday = () => (D().plan || {}).today || todayIso();

const dataAge = () => ageOf(D().today, appToday());
const hardSessionToday = () =>
  hardSessionOf(D().runs, (D().today || {}).signals, appToday());
const lightAddendum = () => lightAddendumOf(D().decision_today);

function renderVerdict() {
  const t = D().today || {};
  const v = VERDICT[t.verdict] || { word: "No call", cls: "s-unknown",
                                    sub: "too little data for a verdict" };
  const reasons = (t.reasons || []).map(r => `<li>${esc(r)}</li>`).join("");
  const age = dataAge();
  const old = age && age.days >= 1;
  const goal = (D().profile || {}).goal;
  const el = document.getElementById("verdict");
  // The verdict KEY on the element, next to the word in the element. The word
  // is written for a human ("Go"); the key is what the snapshot said, and it
  // is the only part a smoke test can read back without guessing at prose —
  // the same trick as `<html data-area>` in chassis.js. Before the page module
  // moved out of index.html, a test looking for "GO" found it in the inline
  // script's source and passed without ever seeing the rendered page.
  el.dataset.verdict = String(t.verdict || "");
  // The empty page, before there is anything to judge. In the cockpit this
  // state never existed - the sync had always run before the tab was opened.
  // Standalone it is the first screen anyone sees, and "No call · not enough
  // data for a verdict" over a column of dashes reads as a broken app.
  //
  // Gated on `hasNoData`, NOT on "no session": the guide used to disappear the
  // moment `runcoach login` succeeded - i.e. one step in - and handed back the
  // very screen it exists to replace, while step two was still undone and this
  // card was the only place naming `--days 30`. It stays until there is data,
  // and only the STEPS change with the session.
  if (noData()) {
    const logged = D().garmin_session !== false;
    el.innerHTML = `
      <div class="verdict-head">
        <span class="verdict-word s-unknown">${logged ? "Almost" : "Hello"}</span>
        <span class="verdict-sub">${logged ? "signed in, nothing synced yet"
                                           : "no Garmin session yet"}</span>
      </div>
      <ol class="first-run">
        ${logged ? "" : `<li>In a terminal: <code>runcoach login</code> — Garmin e-mail,
            password and the MFA code, once. Only session tokens are stored.</li>`}
        <li>${logged ? "In a terminal: " : "Then "}<code>runcoach sync --days 30</code>
            once — the same command <code>runcoach doctor</code> names. A plain
            <code>sync</code> (and the ↻ button) re-fetches only the last few days,
            which is right for daily use and too little to fill the Trend tab.</li>
        <li>Rather look around first? <code>runcoach serve --demo</code> shows a
            synthetic athlete with every card filled.</li>
      </ol>
      <div class="note">Everything below stays empty until then — empty, not zero.</div>`;
    return;
  }
  el.innerHTML = `
    ${old ? `<div class="stale-banner" style="margin-bottom:12px">
        <span>This is the state of ${esc(fmtDay(age.day))}${
          age.days === 1 ? " (yesterday)" : ` — ${age.days} days old`}.
        There are no values for today yet.</span></div>` : ""}
    <div class="verdict-head">
      <span class="verdict-word ${v.cls}">${esc(v.word)}</span>
      <span class="verdict-sub">${esc(v.sub)}</span>
    </div>
    ${reasons ? `<ul class="reasons">${reasons}</ul>`
              : `<div class="empty-note">No reasons in the data.</div>`}
    ${decisionHtml()}
    ${todayCardHtml()}
    ${goal ? `<div class="goal-line">Goal: ${esc(String(goal))}</div>` : ""}`;
}

/* Referee between the readiness light and the weekly share: the light pushes
   towards rest, the intensity card in the Trend tab pushes towards more
   intensity — this line says which one wins today (recovery takes priority).
   Decision and sentence come finished from the snapshot, so that the app and
   the coach show the same sentence; nothing is recomputed here.

   `week.hard_min` / `week.target_min` are the server's numbers for MINUTES
   ABOVE THE EASY ZONES (Z3 + Z4 + Z5) against the share of the measured zone
   time. The key is still called `hard_min`; what it counts is everything that
   is not easy, so the words here say that and not "zone 4–5". */

function decisionHtml() {
  const e = D().decision_today || {};
  if (!e.decision) return "";
  const w = e.week || {};
  // The RUNNING week has a denominator that grows with it: on a Tuesday, after
  // one threshold run, 35 min above easy stand against an 11 min "share" —
  // that reads like three times the target but is merely a young week. So this
  // line only mentions the number for orientation and claims neither
  // "reached" nor "missed".
  const running = (D().weeks || []).some(
    x => x.week_start === w.week_start && (x.partial || x.week_start === weekStart(appToday())));
  const standing = w.target_min > 0
    ? (running
        ? ` (this week so far ${w.hard_min ?? 0} min above easy)`
        : ` (this week ${w.hard_min ?? 0} min above easy, ${w.target_min} would be the share)`)
    : "";
  // Content inside ONE <span>: the wrapper costs nothing and shows the intent
  // (see the `.reasons li` note in the style block).
  return `<ul class="reasons" style="margin-top:8px">
    <li><span><b>${esc(e.sentence || "")}</b>${esc(standing)}</span></li>
  </ul>`;
}

/* The coach's answer to "Train today?" belongs BELOW the verdict, not only in
   the Coach tab: in the morning this one sentence is what is looked for. If
   the newest card is not from today, it says so — reading yesterday's card as
   today's is the same mistake as with the verdict itself. */
function todayCardHtml() {
  const h = todayCard(D().cards, appToday());
  if (!h) return "";
  const c = h.card;
  if (!h.current) {
    return `<div class="today-card">
      <span class="k">Coach</span>
      <span>The last card is from ${esc(fmtDay(c.day))} — none for today yet.</span>
      <a href="#coach">To the Coach tab →</a></div>`;
  }
  // The card from 07:45 says "train: 4×3 min" — the run was at 08:22. After
  // that the recommendation is done, not wrong, and must no longer stand next
  // to a red "Rest" as if the two contradicted each other. If a run analysis
  // already exists, the line shows that instead.
  const done = hardSessionToday();
  if (done) {
    const analysis = (D().cards || []).find(k =>
      k.kind === "analyze-run" && !k.parent && String(k.ref) === String(done.activity_id));
    const at = done.start_time ? String(done.start_time).slice(11, 16) : "";
    return `<div class="today-card today-card-done">
      <span class="k">Coach today · done</span>
      <b>${esc(done.name || "Session")}${at ? ` at ${esc(at)}` : ""}${
        done.training_load != null ? ` · load ${fmtNum(done.training_load)}` : ""}</b>
      ${analysis
        ? `<span>${esc(analysis.headline || "")}</span>
           <a href="#coach">Run analysis in the Coach tab →</a>`
        : `<span>Recommended was: ${esc(c.headline || "")} — analyze the session
             from the Runs tab.</span>
           <a href="#runs">To the session →</a>`}</div>`;
  }
  return `<div class="today-card">
    <span class="k">Coach today</span>
    <b>${esc(c.headline || "")}</b>
    ${c.verdict ? `<span>${esc(c.verdict)}</span>` : ""}
    <a href="#coach">Reasoning in the Coach tab →</a></div>`;
}

/* Smallest span over which a sparkline is drawn — the natural spread of each
   quantity. Without it EVERY series fills the full height: a resting heart
   rate between 46 and 48 would look like one between 40 and 70. For ACWR 0.6
   is chosen because the interesting corridor is 0.8–1.3. */
const MIN_SPAN = { hrv: 20, rhr: 10, bb: 40, score: 30, acwr: 0.6 };

function renderSignals() {
  const t = D().today || {};
  const s = t.signals || {};
  // Rank AND key come from the source (`reason_flags`): "rest" is the hard
  // reason, "easy" the dampening one. Matching on prose keywords and guessing
  // the severity from the overall verdict would put "ACWR 0.4 (stimulus
  // missing)" in the same alarm colour as "HRV LOW", although the source only
  // lists it as a damper — and a rewording on the server would silently
  // switch the colouring off.
  const flags = new Map((t.reason_flags || []).map(f => [f.key, f.level]));
  const flagCls = key => ({ rest: "s-down", easy: "s-warn" })[flags.get(key)] || "";
  const load = D().load || {};
  const ser = D().series || {};
  const rhrDelta = (s.resting_hr != null && s.resting_hr_baseline != null)
    ? s.resting_hr - s.resting_hr_baseline : null;
  const acwr = s.acwr ?? load.acwr;
  document.getElementById("signals").innerHTML = `<div class="grid2">
    ${cell("HRV", s.hrv_status || "–", "Garmin status", flagCls("hrv"),
           ser.hrv_avg_ms, "HRV over the last 28 days", MIN_SPAN.hrv)}
    ${cell("Resting HR", s.resting_hr != null ? s.resting_hr : "–",
           rhrDelta != null ? `${fmtDelta(rhrDelta, 0)} vs 27-day baseline`
                            : "no baseline",
           flagCls("resting_hr"), ser.resting_hr, "Resting heart rate over the last 28 days", MIN_SPAN.rhr)}
    ${cell("Body Battery", s.body_battery_high != null ? s.body_battery_high : "–",
           "daily high", flagCls("body_battery"), ser.body_battery_high,
           "Body Battery over the last 28 days", MIN_SPAN.bb)}
    ${cell("Sleep score", s.sleep_score != null ? s.sleep_score : "–",
           "last night", flagCls("sleep_score"), ser.sleep_score,
           "Sleep score over the last 28 days", MIN_SPAN.score)}
    ${cell("ACWR", acwr != null ? fmtNum(acwr, 2) : "–",
           `${load.acwr_status || ""} ${load.acwr_source === "computed"
             ? "(computed)" : "(Garmin)"}`.trim(), flagCls("acwr"),
           ser.acwr_ratio, "ACWR over the last 28 days", MIN_SPAN.acwr)}
    ${(() => {
      // Counted from the ANCHOR - the last day with data - not from the wall
      // clock. On a stale sync "0" means "on the last day we know about", and
      // printing it as "today" told an athlete their five-day-old session was
      // this morning's. `build_decision` gates this through `fresh`; this cell
      // did not, so the two disagreed on the same screen.
      const d = s.days_since_hard_workout;
      const stale = (D().stale_days ?? 0) >= 1;
      const txt = d == null ? "–"
                : stale ? `${d} d before ${fmtDayShort(D().data_through)}`
                : d === 0 ? "today" : d === 1 ? "yesterday" : `${d} d ago`;
      return cell("Last hard session", txt, s.training_status || "");
    })()}
  </div>`;
}

function renderSleep() {
  const sl = D().sleep || {};
  const p = sl.phases || {};
  const total = ["deep", "light", "rem", "awake"]
    .reduce((a, k) => a + (p[k] || 0), 0);
  const seg = (k, label) => p[k]
    ? `<span class="${k}" style="flex:${Number(p[k]) || 0}"
           title="${esc(label)}: ${esc(fmtHours(p[k]))}"></span>` : "";
  const dScore = (sl.score != null && sl.avg_14d_score != null)
    ? sl.score - sl.avg_14d_score : null;
  // The deviation in DURATION is the message, not the score: "6 h 10 min ·
  // −62 min vs the average" says what the night was.
  const dMin = (sl.seconds != null && sl.avg_14d_seconds != null)
    ? Math.round((sl.seconds - sl.avg_14d_seconds) / 60) : null;
  document.getElementById("sleep").innerHTML = `
    <div class="subhead">Sleep · ${esc(fmtDay(sl.day))}</div>
    ${sl.seconds == null ? `<div class="empty-note">No sleep data.</div>` : `
      <div class="verdict-head" style="align-items:baseline">
        <span class="big">${esc(fmtHours(sl.seconds))}</span>
        <span class="verdict-sub">${dMin != null
          ? `<span class="${dMin <= -45 ? "s-warn" : ""}">${fmtDelta(dMin, 0)} min vs 14-day average</span> · `
          : ""}score ${esc(sl.score ?? "–")}${
          dScore != null ? ` (${fmtDelta(dScore, 0)})` : ""}</span>
      </div>
      ${total ? `<div class="bar" style="margin-top:10px">
          ${seg("deep", "Deep")}${seg("light", "Light")}${seg("rem", "REM")}${seg("awake", "Awake")}
        </div>
        <div class="bar-legend">
          ${p.deep ? `<span><i class="deep"></i>Deep ${esc(fmtHours(p.deep))}</span>` : ""}
          ${p.light ? `<span><i class="light"></i>Light ${esc(fmtHours(p.light))}</span>` : ""}
          ${p.rem ? `<span><i class="rem"></i>REM ${esc(fmtHours(p.rem))}</span>` : ""}
          ${p.awake ? `<span><i class="awake"></i>Awake ${esc(fmtHours(p.awake))}</span>` : ""}
        </div>` : `<div class="thin-data">No phase breakdown for this night.</div>`}
      <div class="note" style="margin-top:8px">14-day average
        ${esc(fmtHours(sl.avg_14d_seconds))} over ${esc(plural(sl.nights_14d || 0, "measured night", "measured nights"))}
        (missing nights do not count as zero).</div>`}`;
}

/* The zone card ends the "which number is it?" argument: ONE source, with a
   date. The LACTATE THRESHOLD is part of it (HR + pace, from Garmin's last
   Firstbeat measurement). The two readings carry SEPARATE dates: the zone
   bounds come from the last run, the threshold from the last measurement —
   these can be weeks apart, and one shared "as of" line would blur that. */
function renderZones() {
  const z = D().zones || {};
  const degraded = D().degraded || [];
  const pace = z.lt_pace_s_per_km ? fmtPace(z.lt_pace_s_per_km) : null;
  const hasLt = z.lthr_bpm != null;
  const unreadable = degraded.includes("latest_lactate_threshold") || degraded.includes("max_hr_since");
  document.getElementById("zones").innerHTML = `
    <div class="subhead">Your bounds</div>
    ${z.z4_low == null && z.z5_low == null && !hasLt
      ? `<div class="empty-note">The recent runs carried no zone bounds.</div>`
      : `<div class="grid2">
           ${cell("Threshold from", z.z4_low ?? "–", "zone 4")}
           ${cell("VO2max from", z.z5_low ?? "–", "zone 5")}
           ${hasLt ? cell("Lactate threshold", z.lthr_bpm, "bpm") : ""}
           ${pace ? cell("Threshold pace", pace, "Firstbeat measurement") : ""}
           ${z.max_hr_observed != null
             ? cell("Highest HR", z.max_hr_observed,
                    `measured ${fmtDay(z.max_hr_day)}`) : ""}
           ${z.lthr_pct_max_hr != null
             ? cell("Threshold share", `${z.lthr_pct_max_hr} %`, "of the observed maximum") : ""}
         </div>
         <div class="note" style="margin-top:8px">${esc(z.source || "")} ·
           as of ${esc(fmtDay(z.as_of_day))}${
             hasLt && z.lt_measured_on
               ? ` · threshold measured ${esc(fmtDay(z.lt_measured_on))}`
               : ""}</div>`}
    <div class="thin-data">${esc(z.note || "")}${hasLt
      ? ` The lactate threshold is the heart rate at which lactate build-up and
          clearance are just about in balance — above it the clock is ticking.
          Garmin re-measures it on hard runs.${z.max_hr_observed != null
            ? ` "Highest HR" is the highest value of the last 90 days, i.e. a
                LOWER BOUND for your maximum heart rate — not your maximum heart
                rate. The percentage falls as soon as you go all out again, and
                rises when an old peak rolls out of the window; it is good for
                orientation, not as an alarm.` : ""}`
      : ""}${unreadable ? " Part of this block could not be read from the database." : ""}</div>`;
}

/* ═══ Actions (coach jobs) ═════════════════════════════════════════════════ */

/** NO price anywhere in the page: a dollar figure under every trigger turns
 *  cost into the deciding criterion, and on a subscription the figure the CLI
 *  reports is nominal anyway. It stays in the job JSON for anyone who asks;
 *  the cockpit this was extracted from had a cost dashboard, this app does not. */

/** Is a paid job running right now? The source is the job state from the
 *  payload, not the duration of the POST: the request is through after a
 *  second, the run takes minutes. Exactly in that window nothing would lock
 *  the buttons and nothing would show that something is running. */
function hasActiveJob() {
  return (D().jobs || []).some(j => j.status === "queued" || j.status === "running");
}

const coachUnavailable = () => D().claude_available === false;

/* Both predicates live in `logic.js` so web-tests can reach them; these are
   the page's shorthand for "against the current payload". */
const noData = () => hasNoData(D());
const firstRun = () => isFirstRun(D());

function tplButton(t, ctx) {
  const c = ctx ? ` data-ctx='${esc(JSON.stringify(ctx))}'` : "";
  const locked = spawnRunning || hasActiveJob() || coachUnavailable() || noData();
  return `<button class="btn btn-primary" data-tpl="${esc(t.id)}"${c}
            ${locked ? "disabled" : ""}
            title="${esc(t.hint || "")}">${esc(t.title)}</button>`;
}

function renderActions() {
  const tpls = D().templates || [];
  // `tplToday`, not `today`-something that shadows a module function called in
  // the same scope — a same-named local constant once shadowed the date
  // helper one line above its declaration (temporal dead zone). It was
  // triggered by every snapshot WITHOUT readiness data (`today.day` is null
  // there): half the page stayed empty and the app blamed the server.
  const day = (D().today || {}).day || appToday();
  const tplToday = tpls.find(t => t.id === "train-today");
  document.getElementById("actions-today").innerHTML = `
    <div class="subhead">Ask the coach</div>
    <div class="btn-row">
      ${tplToday ? tplButton(tplToday, { day }) : ""}
    </div>
    ${hasActiveJob() ? `<div class="job-warn">A run is still under way —
      the buttons are locked until it is done.</div>` : ""}
    <div class="note" style="margin-top:8px">${noData()
      ? (firstRun() ? "Nothing to analyse yet — start with runcoach login, above."
                    : "Nothing to analyse yet — sync first.")
      : coachUnavailable()
        ? "Coach cards need the Claude Code CLI — see the Coach tab."
        : "Starts a real agent run. A second tap confirms. The answer appears in the Coach tab."}</div>`;

  document.getElementById("coach-hint").innerHTML = noData()
    ? `<div class="coach-hint">No data to analyse yet — the coach reads what a sync
         has stored. ${firstRun() ? "Start with <code>runcoach login</code>."
                                  : "Press ↻ or run <code>runcoach sync</code>."}</div>`
    : coachUnavailable()
      ? `<div class="coach-hint">Claude Code CLI not found — coach cards need it
           (run <code>runcoach doctor</code>).</div>` : "";
  document.getElementById("coach-actions").innerHTML = `
    <div class="subhead">Start an analysis</div>
    <div class="btn-row">${tpls.filter(t => !(t.params || []).length)
      .map(t => tplButton(t)).join("")}</div>`;
}

/* A spawn in flight locks ALL template buttons until the answer is there.
   Without this the button could be armed again at once, and because nothing
   stays on the Today page after the start (the toast is gone after a few
   seconds), an impatient user taps again — two arm+fire rounds are two real,
   paid runs. */
let spawnRunning = false;

async function spawnTemplate(id, ctx, force = false) {
  if (spawnRunning) { toast("Already starting — one moment.", { warn: true }); return; }
  spawnRunning = true;
  document.querySelectorAll("button[data-tpl]").forEach(b => (b.disabled = true));
  try {
    await apiPost("/api/spawn", { template_id: id, ...(ctx ? { ctx } : {}),
                                  ...(force ? { force: true } : {}) });
    toast("Running — the answer will appear in the Coach tab.",
          { action: "View", onAction: () => showView("coach"), ms: 8000 });
    await refresh();
    poller.start();
  } catch (e) {
    // The server's dedup: "already running" is only shown; "the card is on the
    // current data" offers »Recompute« — the same request with force:true.
    // The toast action is the confirmation.
    if (e.status === 409 && handle409(e, () => spawnTemplate(id, ctx, true))) {
      await refresh().catch(() => {});
      return;
    }
    // "Refused" only for a REAL refusal by the server (an HTTP status). If the
    // connection drops or runs into the time limit, the job may have been
    // created anyway — the server writes the job file BEFORE it answers. Then
    // this must not say "refused", or a second paid run gets tapped.
    if (e.message === "403") { /* the login overlay is already up */ }
    else if (e.status) {
      toast("Refused: " + e.message, { warn: true, ms: 5000 });
    } else {
      toast("Connection lost — whether the run started is shown in the Coach tab.",
            { warn: true, ms: 7000, action: "Check",
              onAction: () => { showView("coach"); reload(); } });
      await refresh().catch(() => {});     // pull the job list, quietly
    }
  } finally {
    spawnRunning = false;
    // Do NOT unlock across the board: `refresh()` has just rendered the
    // buttons correctly locked (the job keeps running for minutes), and a
    // blanket `disabled = false` would undo exactly that — while the text next
    // to them says "the buttons are locked until it is done".
    // `renderActions()` decides from the real job state.
    renderActions();
  }
}

/* ═══ Runs ═════════════════════════════════════════════════════════════════ */

/* The bar shows THREE bands (easy / moderate / hard), not five zones: five
   colours cannot be made high-contrast on a light ground and separable for
   colour-blind readers at the same time (Z3↔Z4 ΔE 8.8 under deuteranopia —
   exactly the two whose ratio is the question). The five zone times stand
   next to it as NUMBERS; there they are exact instead of estimated. */
function bandBar(z) {
  const { easy, mid, hard, total: tot } = bandSplit(z);
  if (!tot) return "";
  const seg = (v, cls, name) => v
    ? `<span class="${cls}" style="flex:${Number(v) || 0}"
           title="${esc(name)}: ${esc(fmtDur(v))}"></span>` : "";
  const pct = v => Math.round(100 * v / tot);
  return `<div class="bar" style="margin-top:8px">
      ${seg(easy, "b-easy", "Easy")}${seg(mid, "b-moderate", "Moderate")}${seg(hard, "b-hard", "Hard")}
    </div>
    <div class="bar-legend">
      ${easy ? `<span><i class="b-easy"></i>Easy ${pct(easy)} %</span>` : ""}
      ${mid ? `<span><i class="b-moderate"></i>Moderate ${pct(mid)} %</span>` : ""}
      ${hard ? `<span><i class="b-hard"></i>Hard ${pct(hard)} %</span>` : ""}
    </div>`;
}

function zoneTimes(z) {
  const parts = zoneTimeParts(z);
  return parts.length ? `<div class="note">${esc(parts.join(" · "))}</div>` : "";
}

/* Sport as a symbol: without any marker a strength session sits between the
   runs and cannot be told apart when skimming. */
const SPORT_ICON = {
  running: `<svg viewBox="0 0 24 24"><path d="M3 12h4l2.5-6 4 12 2.5-6h5"/></svg>`,
  strength: `<svg viewBox="0 0 24 24"><path d="M4 9v6M8 7v10M16 7v10M20 9v6M8 12h8"/></svg>`,
  other: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="7"/></svg>`,
};
function sportIcon(t) {
  const k = isRunning(t) ? "running" : isStrength(t) ? "strength" : "other";
  return `<span class="sport sport-${k}" aria-hidden="true">${SPORT_ICON[k]}</span>`;
}

const BAND_TITLE = { easy: "easy", moderate: "grey middle", hard: "hard" };

function runRow(r) {
  const open = openRun === r.activity_id;
  const st = r.structure || {};
  // ONLY an interval structure gets a label — and it is marked as Garmin's
  // auto-segmentation, not as fact: one continuous 35-minute block can show up
  // as "2×20:41". The rep count is unreliable; as an accent badge it would
  // still carry authority.
  const tag = st.kind === "intervals" && st.label
    ? `<span class="tag tag-garmin" title="Garmin's automatic segmentation — not the planned structure">Garmin: ${esc(st.label)}</span>` : "";
  const strength = isStrength(r.type);
  // The band comes from the server (one source, see snapshot.band).
  const band = strength || !BAND_TITLE[r.band] ? null : r.band;
  const sub = [
    r.distance_m ? fmtKm(r.distance_m) : null,
    r.duration_s ? fmtDur(r.duration_s) : null,
    r.pace_s_per_km ? fmtPace(r.pace_s_per_km) : null,
    r.avg_hr ? `avg ${r.avg_hr}` : null,
  ].filter(Boolean).join(" · ");
  return `<div class="run${band ? " run-band-" + band : ""}${strength ? " run-strength" : ""}">
    <button class="run-head" data-run="${Number(r.activity_id) || 0}"
            aria-expanded="${open}"${band ? ` title="${esc(BAND_TITLE[band])}"` : ""}>
      ${sportIcon(r.type)}
      <span class="run-mid">
        <span class="run-title">${esc(r.name || r.type || "Session")}${tag}</span>
        <span class="run-sub">${esc(sub)}</span>
      </span>
      <span class="run-right">${esc(fmtDay(r.day))}<br>${
        r.training_load != null ? "load " + fmtNum(r.training_load) : ""}</span>
    </button>
    ${open ? runDetail(r) : ""}
  </div>`;
}

function runDetail(r) {
  const z = r.zones_s || {}, st = r.structure || {};
  const rows = [];
  if (st.kind === "intervals") {
    rows.push(["Structure", `${st.rep_count}× ${fmtDur(st.avg_rep_duration_s)} ` +
      `(avg ${st.avg_active_hr ?? "–"} active / ${st.avg_recovery_hr ?? "–"} recovery)`]);
  }
  if (r.max_hr) rows.push(["Max HR", r.max_hr]);
  if (r.avg_cadence) rows.push(["Cadence", fmtNum(r.avg_cadence) + " spm"]);
  if (r.aerobic_te != null) rows.push(["Training effect",
    `${fmtNum(r.aerobic_te, 1)} aerobic${r.anaerobic_te != null
      ? " / " + fmtNum(r.anaerobic_te, 1) + " anaerobic" : ""}${
      r.te_label ? " · " + r.te_label : ""}`]);
  if (r.performance_condition != null)
    rows.push(["Performance condition", fmtDelta(r.performance_condition, 0)]);
  if (r.temperature_c != null) rows.push(["Temperature", fmtNum(r.temperature_c, 1) + " °C"]);
  if (r.vo2max != null) rows.push(["VO2max after", fmtNum(r.vo2max, 1)]);
  if (r.hr_z4_low || r.hr_z5_low)
    rows.push(["Zones that day", `Z4 from ${r.hr_z4_low ?? "–"}, Z5 from ${r.hr_z5_low ?? "–"}`]);

  const tpl = (D().templates || []).find(t => t.id === "analyze-run");
  return `<div class="run-detail">
    ${r.has_detail ? bandBar(z) + zoneTimes(z)
      : `<div class="thin-data">No detail data has been synced for this session —
           zones and structure are therefore missing, they are not zero.</div>`}
    ${rows.length ? `<dl class="kv">${rows.map(([k, v]) =>
        `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join("")}</dl>` : ""}
    ${tpl ? `<div class="btn-row">${tplButton(tpl, { activity_id: String(r.activity_id) })}</div>` : ""}
  </div>`;
}

/* Runners think in weeks, not in "the last 30 entries". The list therefore
   gets week headers with a subtotal. IMPORTANT: the sum counts only what is in
   THIS list — the oldest group is cut off by the 30-entry limit and says so.
   Otherwise a weekly sum would stand here that the Trend tab reports
   differently. */
function renderRuns() {
  const runs = D().runs || [];
  const withDetail = runs.filter(r => r.has_detail).length;
  document.getElementById("runs-head").innerHTML = `
    <div class="subhead">Recent sessions</div>
    <div class="note">${plural(runs.length, "session", "sessions")} · ${withDetail} with detail data
      (zones/splits). Tap for details.</div>`;

  if (!runs.length) {
    document.getElementById("runs").innerHTML =
      `<div class="run"><div class="empty-note">No sessions in the snapshot.</div></div>`;
    return;
  }
  const groups = groupRunsByWeek(runs);
  document.getElementById("runs").innerHTML = groups.map((g, i) => {
    const km = g.runs.reduce((a, r) => a + (r.distance_m || 0), 0);
    const last = i === groups.length - 1;
    const range = g.wk === NO_DATE ? "no date"
      : `${fmtDayShort(g.wk)} – ${fmtDayShort(addDays(g.wk, 6))}`;
    return `<div class="wk-head">
        <span>${esc(range)}</span>
        <span class="num">${plural(g.runs.length, "session", "sessions")}${
          km ? " · " + fmtKm(km, 0) : ""}${last ? " · list ends here" : ""}</span>
      </div>` + g.runs.map(runRow).join("");
  }).join("");
}

/* ═══ Trend ════════════════════════════════════════════════════════════════ */

/* VO2max as STEPS. Garmin carries the last value forward until a real
   measurement changes it — an interpolated line would show a steady
   development that never happened.

   Two defects that showed up in the live picture and are fixed here:
   (1) The y-axis spanned exactly the existing range — with a carried-forward
       VO2max that is often 1.0 point, stretched over the full card height. It
       looked like a rocket where one decimal had moved. Now a MINIMUM SPAN of
       3.0 points applies; small movements look small.
   (2) The jump markers had the colour of the line and sat on it — the text
       promised "12 marked jumps" and none could be seen. Now a ring in the
       surface colour, so that the dot stands on the line instead of in it. */
const VO2_MIN_SPAN = 3.0;

/** Step curve over `days` (one value per day, null = gap; markers only at real
 *  jumps). Shared by VO2max, lactate threshold and aerobic pace: `fmt` labels
 *  the axis, `invert` flips it — for a pace in seconds/km LESS is better and
 *  belongs at the top. */
function stepChart(days, vals, { minSpan = 3, fmt = v => v.toFixed(1), invert = false,
                                 label = "Trend as a step curve", markAll = false } = {}) {
  const pts = chartPoints(vals);
  if (pts.length < 2) return "";
  const W = 320, H = 118, PL = 34, PR = 10, PT = 10, PB = 18;
  const xs = axisX(vals.length, { W, PL, PR });
  const { y0, mid, y1, ys } = chartScale(pts, { minSpan, invert, H, PT, PB });
  const d = stepPath(pts, xs, ys);
  const steps = stepMarks(pts, markAll);
  const axis = [y0, mid, y1];
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
    ${axis.map(v => `<line class="grid" x1="${PL}" y1="${ys(v).toFixed(1)}"
        x2="${W - PR}" y2="${ys(v).toFixed(1)}"/>
      <text x="2" y="${(ys(v) + 3).toFixed(1)}">${esc(fmt(v))}</text>`).join("")}
    <path class="step" d="${d}"/>
    ${steps.map(p => `<circle class="dot" cx="${xs(p[0]).toFixed(1)}"
        cy="${ys(p[1]).toFixed(1)}" r="3.2">
        <title>${esc(days[p[0]])}: ${esc(fmt(p[1]))}</title></circle>`).join("")}
    <text x="${PL}" y="${H - 4}">${esc(fmtDay(days[pts[0][0]]))}</text>
    <text x="${W - PR}" y="${H - 4}" text-anchor="end">${esc(fmtDay(days[days.length - 1]))}</text>
  </svg>`;
}

/* Lactate threshold over time. Garmin re-measures it on hard runs; the
   measurements are the points, in between the last value holds — steps as
   with VO2max, on the same 8-week axis. Pace is the more telling series (the
   HR moves in 2-bpm steps), so it goes on top; the axis is flipped so that
   faster = higher. */
function renderThreshold() {
  const z = D().zones || {}, s = D().series || {};
  const el = document.getElementById("threshold");
  const hist = (z.lt_history || []).filter(p => p.day);
  if ((D().degraded || []).includes("lactate_threshold_history")) {
    el.innerHTML = `<div class="subhead">Lactate threshold</div>
      <div class="empty-note">History not readable — the measurements could not be
        read from the database.</div>`;
    return;
  }
  if (!hist.length) {
    el.innerHTML = `<div class="subhead">Lactate threshold</div>
      <div class="empty-note">No measurement yet — Garmin measures the threshold on
        hard runs (threshold block, intervals from zone 4).</div>`;
    return;
  }
  // Carry forward onto the series days: on each day the newest measurement up
  // to then holds; before the first measurement the series stays empty (a
  // gap, not 0).
  const days = s.days || [];
  const pace = carryForward(days, hist, "lt_pace_s_per_km");
  const first = hist[0], latest = hist[hist.length - 1];
  const dPace = (first.lt_pace_s_per_km != null && latest.lt_pace_s_per_km != null)
    ? latest.lt_pace_s_per_km - first.lt_pace_s_per_km : null;
  const dBpm = (first.lthr_bpm != null && latest.lthr_bpm != null)
    ? latest.lthr_bpm - first.lthr_bpm : null;
  const rows = [...hist].reverse().map(p => `<tr>
      <td>${esc(fmtDay(p.day))}</td>
      <td class="num">${esc(p.lthr_bpm ?? "–")}</td>
      <td class="num">${p.lt_pace_s_per_km != null ? esc(fmtMinSec(p.lt_pace_s_per_km)) : "–"}</td></tr>`).join("");
  el.innerHTML = `
    <div class="subhead">Lactate threshold</div>
    <div class="verdict-head" style="align-items:baseline">
      <span class="big" style="font-size:34px">${latest.lt_pace_s_per_km != null
        ? esc(fmtMinSec(latest.lt_pace_s_per_km)) + " /km" : esc(latest.lthr_bpm ?? "–") + " bpm"}</span>
      <span class="verdict-sub">${latest.lthr_bpm != null ? `${esc(latest.lthr_bpm)} bpm · ` : ""}${
        plural(hist.length, "measurement", "measurements")} since ${esc(fmtDay(first.day))}${
        dPace != null && hist.length > 1
          ? ` · pace ${dPace > 0 ? "+" : ""}${esc(dPace)} s/km` : ""}${
        dBpm != null && dBpm !== 0 ? ` · HR ${dBpm > 0 ? "+" : ""}${esc(dBpm)}` : ""}</span>
    </div>
    ${pace.some(v => v != null)
      ? stepChart(days, pace, { minSpan: 20, invert: true, fmt: fmtMinSec, markAll: true,
          label: "Threshold pace as a step curve, faster is up, axis at least 20 seconds wide" })
      : ""}
    <details class="card-more"><summary>All measurements</summary>
      <table class="tab" style="margin-top:8px"><thead><tr><th>Measured</th>
        <th class="num">bpm</th><th class="num">min/km</th></tr></thead>
        <tbody>${rows}</tbody></table></details>
    <div class="thin-data">Only the marked days are measurements; in between the
      last value holds. A slower threshold pace at the same HR does not
      automatically mean lost form — heat and elevation on the day of the
      measurement push it down as well.</div>`;
}

function renderVo2() {
  const v = D().vo2max || {}, s = D().series || {};
  const el = document.getElementById("vo2");
  if (v.current == null) {
    el.innerHTML = `<div class="subhead">VO2max</div>
      <div class="empty-note">No VO2max in the last 8 weeks.</div>`;
    return;
  }
  el.innerHTML = `
    <div class="subhead">VO2max</div>
    <div class="verdict-head" style="align-items:baseline">
      <span class="big" style="font-size:34px">${fmtNum(v.current, 1)}</span>
      <span class="verdict-sub">
        28 days ${esc(fmtDelta(v.change_28d))} ·
        56 days ${v.change_56d == null ? "no baseline" : esc(fmtDelta(v.change_56d))}</span>
    </div>
    ${stepChart(s.days || [], s.vo2max || [], { minSpan: VO2_MIN_SPAN,
        label: "VO2max as a step curve, axis at least 3 points wide" })}
    <div class="thin-data">Steps, not a curve: Garmin carries the value forward on
      days without a real measurement. ${(() => {
        const shown = (v.changed_days || []).length;
        const total = v.changed_total ?? shown;
        // The server caps the MARKERS at 12; the count is the real one. Saying
        // "only the 12 marked jumps" for a series with 30 would be a statement
        // about the chart dressed up as a statement about the athlete.
        return total > shown
          ? `${total} of the days are new measurements; the last ${shown} are marked.`
          : `Only the ${plural(shown, "marked jump is a new measurement",
                               "marked jumps are new measurements")}.`;
      })()}</div>`;
}

/* ═══ Week plan, hard minutes, aerobic efficiency, predictions ═════════════
   The bridge between the coach's advice and the watch, the lever for
   threshold training, the base-fitness signal, and the most tangible
   translation of the numbers. */

function renderWeek() {
  const p = D().plan || {};
  const el = document.getElementById("week");
  const days = p.days || [];
  if (!days.length) {
    el.innerHTML = `<div class="subhead">This week</div>
      <div class="empty-note">No calendar in the snapshot.</div>`;
    return;
  }
  // A failed database read must not show up as "nothing planned" — the server
  // reports it in `degraded`.
  if ((D().degraded || []).includes("get_scheduled_workouts")) {
    el.innerHTML = `<div class="subhead">This week</div>
      <div class="empty-note">Plan not readable — the scheduled workouts could not be
        read from the database. This does NOT mean that nothing is planned.</div>`;
    return;
  }
  const nowIso = p.today || appToday();
  const columns = days.map(t => {
    const planned = (t.planned || [])[0];
    const ran = (t.done || []).filter(g => isRunning(g.type));
    const strength = (t.done || []).filter(g => isStrength(g.type));
    const past = t.day < nowIso, isToday = t.day === nowIso;
    // State of the cell: done (band colour), planned (outline), missed
    // (planned, past, nothing run), empty.
    const run = ran[0];
    const band = run && BAND_TITLE[run.band] ? run.band : "easy";
    const cls = run ? ` w-run w-${band}`
              : planned && past ? " w-missed"
              : planned ? " w-planned" : "";
    const content = run
      ? `<span class="w-t">${esc(weekLabel(run.name))}</span>
         ${run.training_load != null ? `<span class="w-s">load ${fmtNum(run.training_load)}</span>` : ""}`
      : planned
        ? `<span class="w-t">${esc(weekLabel(planned.title))}</span>
           <span class="w-s">${past ? "missed" : "planned"}</span>`
        : strength.length ? `<span class="w-t muted">Gym</span>` : `<span class="w-t muted">–</span>`;
    const tip = [fmtDay(t.day), run ? shortTitle(run.name) : planned ? shortTitle(planned.title) : ""]
      .filter(Boolean).join(" · ");
    return `<div class="w-day${cls}${isToday ? " w-today" : ""}" title="${esc(tip)}">
      <span class="w-d">${esc(weekdayShort(t.day))}</span>${content}</div>`;
  }).join("");
  const upcoming = (p.upcoming || []).slice(0, 3).map(d =>
    `<span>${esc(fmtDay(d.day))} ${esc(shortTitle(d.title))}</span>`).join(" · ");
  const nothingPlanned = !days.some(t => (t.planned || []).length) && !(p.upcoming || []).length;
  el.innerHTML = `
    <div class="subhead">This week</div>
    <div class="week-strip">${columns}</div>
    ${upcoming ? `<div class="note" style="margin-top:8px">Coming up: ${upcoming}</div>` : ""}
    ${nothingPlanned ? `<div class="thin-data">Nothing is scheduled on the watch. Workouts
        in the Garmin calendar appear here after the next sync.</div>` : ""}`;
}

/* Minutes ABOVE THE EASY ZONES per week against the 80/20 SHARE — not against
   a fixed number of minutes. The rule only knows the relation "~20 % of the
   training is not easy"; an absolute target would be an invented norm. Every
   week therefore gets its OWN target line from its own zone time. Four weeks
   at zero, then 35 minutes on one day — that is the pattern this bar makes
   visible.

   The bar is `moderate_s + hard_s` (Z3 + Z4 + Z5): everything that is not
   easy. Counting the grey middle in the denominator only — as the old Z4+Z5
   numerator did — made the share look smaller than it was and tilted the whole
   app towards prescribing another hard session.

   The SOLID inner segment is `hard_s` alone (Z4 + Z5). It has to be visible,
   because the two are not interchangeable: a full bar made entirely of the
   lighter part is a week of grey-zone running, and `decide_today` reads it as
   exactly that. One bar, both numbers, no second interpretation. */
function renderIntensity() {
  const weeks = D().weeks || [];
  const share = (D().targets || {}).hard_share ?? 0.20;
  const el = document.getElementById("hard-minutes");
  if (!weeks.length) { el.innerHTML = ""; return; }
  const { mins, quality, targets, max } = intensitySeries(weeks, share);
  const currentWk = weekStart(appToday());
  const { hits, full } = intensityHits(weeks, mins, targets, currentWk);
  const pct = `${Math.round(share * 100)} %`;
  const W = 320, H = 116, PB = 28, PT = 16;
  const { bw, xOf, yOf, hOf } = barScale(weeks.length, max, { W, H, PT, PB });
  const svg = `<svg class="chart intensity" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Minutes above the easy zones per week, the part above threshold drawn solid inside each bar, with each week's ${esc(pct)} share of the measured zone time as a target line">
    ${weeks.map((w, i) => {
      const h = hOf(mins[i]);
      const x = xOf(i), y = H - PB - h;
      const ty = yOf(targets[i]);
      const qh = hOf(quality[i]);
      return `<rect class="barfill${weekIncomplete(w, currentWk) ? " wip" : ""}${
                targets[i] > 0 && mins[i] >= targets[i] ? "" : " bar-muted"}"
                x="${x + 3}" y="${y.toFixed(1)}" width="${(bw - 6).toFixed(1)}"
                height="${Math.max(h, 1).toFixed(1)}" rx="3">
          <title>${esc(w.week_start)}: ${mins[i]} min above easy, of which ${quality[i]} min
            above threshold (zone 4-5)${targets[i] > 0
            ? ` · ${pct} of the measured zone time would be ${Math.round(targets[i])} min` : ""}</title></rect>
        ${quality[i] > 0 ? `<rect class="barquality" x="${x + 3}" y="${(H - PB - qh).toFixed(1)}"
            width="${(bw - 6).toFixed(1)}" height="${Math.max(qh, 1).toFixed(1)}" rx="3"
            pointer-events="none"/>` : ""}
        ${targets[i] > 0 ? `<line class="target" x1="${(x + 2).toFixed(1)}" x2="${(x + bw - 2).toFixed(1)}"
            y1="${ty.toFixed(1)}" y2="${ty.toFixed(1)}"/>` : ""}
        <text class="barlabel" x="${(x + bw / 2).toFixed(1)}" y="${(y - 4).toFixed(1)}"
              text-anchor="middle">${mins[i]}</text>
        <text x="${(x + bw / 2).toFixed(1)}" y="${H - 14}" text-anchor="middle">${
          esc(barLabel(w.week_start))}</text>`;
    }).join("")}
    <text x="4" y="${H - 2}">min above easy (Z3–Z5), solid = above threshold (Z4–Z5) · line = ${pct}</text>
  </svg>`;
  el.innerHTML = `
    <div class="subhead">Minutes above easy per week</div>
    ${svg}
    <div class="note" style="margin-top:6px">${hits} of ${plural(full, "full week", "full weeks")}
      ${full === 1 ? "is" : "are"} at or above the ${pct} share.${lightAddendum()}</div>
    <details class="card-more"><summary>How this is counted</summary>
    <div class="thin-data">The 80/20 rule refers to the SHARE, not to a fixed number of
      minutes — the line therefore moves with the week's zone time. Counted is everything
      above the easy zones, zone 3 included: the target is ${pct} of the TOTAL measured
      zone time, so the grey middle has to appear on both sides of the fraction.
      The solid part of each bar is the time above threshold (zone 4–5). A bar that
      reaches its line while staying pale is a week spent in the grey middle — the
      minutes are there, the stimulus is not. The line is not a target for EVERY week
      either: base, deload and taper weeks rightly sit below it — this row counts,
      it does not judge.</div></details>`;
}

/* Easy pace at a fixed HR: the base-fitness signal. The threshold shows the
   peak; this shows whether the foundation is getting faster. */
function renderAerobic() {
  const a = D().aerobic || {};
  const el = document.getElementById("aerobic");
  const pts = a.points || [];
  if (pts.length < 2) {
    el.innerHTML = `<div class="subhead">Easy pace${a.ref_hr ? ` at ${esc(a.ref_hr)} bpm` : ""}</div>
      <div class="empty-note">Too few easy runs for a trend yet.</div>`;
    return;
  }
  const days = pts.map(p => p.week_start), vals = pts.map(p => p.pace_s_per_km);
  // NO traffic light: the weekly values scatter by several s/km, and a colour
  // from ±10 s would fire below its own noise. The trend is ONLY mentioned if,
  // over the window, it is larger than the spread.
  const tr = a.trend_s_per_week, sd = a.spread_s;
  // CALENDAR weeks spanned, not the number of points. Weeks without a qualifying
  // easy run are simply absent, so multiplying a per-week slope by the point
  // count compressed the axis again and hid a real change as noise.
  const spanWeeks = weeksBetween(days[0], days[days.length - 1]);
  const total = tr != null ? tr * spanWeeks : null;
  const clear = total != null && sd != null && Math.abs(total) > sd;
  const nRuns = pts.map(p => p.n || 0).reduce((x, y) => x + y, 0);
  const temps = pts.filter(p => p.temp_c != null).map(p =>
    `${esc(fmtDayShort(p.week_start))} ${fmtNum(p.temp_c, 0)} °C`).slice(-3).join(", ");
  el.innerHTML = `
    <div class="subhead">Easy pace at ${esc(a.ref_hr)} bpm</div>
    <div class="verdict-head" style="align-items:baseline">
      <span class="big" style="font-size:34px">${esc(fmtMinSec(a.current))} /km</span>
      <span class="verdict-sub">${clear
        ? `trend ${total < 0 ? "−" : "+"}${Math.abs(Math.round(total))} s/km over ${spanWeeks + 1} weeks`
        : "no trend above the spread"}${
        sd != null ? ` · spread ±${fmtNum(sd, 0)} s` : ""}</span>
    </div>
    ${stepChart(days, vals, { minSpan: 30, invert: true, fmt: fmtMinSec, markAll: true,
        label: "Easy pace at a fixed heart rate per week, faster is up" })}
    <div class="thin-data">Each easy run's pace is scaled to ${esc(a.ref_hr)} beats
      (pace × HR ÷ ${esc(a.ref_hr)}), then the median per week over ${plural(nRuns, "run", "runs")}.
      This is a PROPORTIONAL MODEL through the origin and therefore a rough
      approximation — the real HR–pace line has an intercept. The metric is
      exploratory, not a benchmark.${temps ? ` Heat shifts it: ${temps}.` : ""}</div>`;
}

/* Garmin's race-time predictions from VO2max + threshold. Not times that were
   run — the card says so, otherwise 51:05 is taken for a 10 k that happened. */
function renderPredictions() {
  const p = D().predictions || {};
  const el = document.getElementById("predictions");
  if ((D().degraded || []).includes("latest_race_predictions")) {
    el.innerHTML = `<div class="subhead">Race times</div>
      <div class="empty-note">Predictions not readable from the database.</div>`;
    return;
  }
  if (p.k5_s == null && p.k10_s == null) {
    el.innerHTML = `<div class="subhead">Race times</div>
      <div class="empty-note">No Garmin prediction synced yet.</div>`;
    return;
  }
  const vc = volumeComparison(D().weeks || [], weekStart(appToday()));
  el.innerHTML = `
    <div class="subhead">Race times · Garmin prediction</div>
    <div class="grid2">
      ${cell("5 km", fmtDur(p.k5_s), "")}
      ${cell("10 km", fmtDur(p.k10_s), "")}
      ${cell("Half marathon", fmtDur(p.hm_s), "")}
      ${cell("Marathon", fmtDur(p.m_s), "")}
    </div>
    <div class="note" style="margin-top:8px">As of ${esc(fmtDay(p.day))} · computed from VO2max
      and threshold, not run. The longer distances assume that the volume for them is
      there${vc && vc.current_km != null
        ? ` — at currently ${fmtNum(vc.current_km, 0)} km per week the marathon value is
            arithmetic, not a plan` : ""}.</div>`;
}

function renderFactors() {
  const f = (D().vo2max || {}).factors || {};
  const a = f.last_28d, b = f.prev_28d;
  const el = document.getElementById("factors");
  if (!a || !b) {
    el.innerHTML = `<div class="subhead">What has changed</div>
      <div class="empty-note">Too few sessions for a block comparison.</div>`;
    return;
  }
  const row = (k, va, vb, unit = "", digits = 0) => {
    const d = (va != null && vb != null) ? va - vb : null;
    const show = v => v == null ? "–" : fmtNum(v, digits) + unit;
    return `<tr><td style="color:var(--muted)">${esc(k)}</td>
      <td class="num" style="text-align:right">${esc(show(va))}</td>
      <td class="num" style="text-align:right;color:var(--muted)">${esc(show(vb))}</td>
      <td class="num" style="text-align:right">${d == null ? "" : esc(fmtDelta(d, digits))}</td></tr>`;
  };
  el.innerHTML = `
    <div class="subhead">What has changed</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <tr style="font-size:11px;color:var(--muted);text-transform:uppercase">
        <th style="text-align:left">&nbsp;</th><th style="text-align:right">28 days</th>
        <th style="text-align:right">before</th><th style="text-align:right">Δ</th></tr>
      ${row("Runs", a.runs, b.runs)}
      ${row("Distance", a.distance_km, b.distance_km, " km", 1)}
      ${row("Z5 minutes", a.z5_min, b.z5_min, "", 1)}
      ${row("Easy share", a.easy_pct, b.easy_pct, " %")}
      ${row("Avg temperature", a.avg_temp_c, b.avg_temp_c, " °C", 1)}
    </table>
    <div class="thin-data">${f.covers_full_window === false
      ? "The comparison block was not read in full — the difference is an artefact, not a change. "
      : ""}Co-occurrence, not cause: n=1, and things that happen at the same time
      need not be connected.</div>`;
}

/* The running week is not finished yet and must not look like a slump:
   hatched instead of merely grey, with its own label right at the bar — a
   footnote is a place nobody looks at. */
function volumeChart(weeks) {
  if (!weeks.length) return "";
  const W = 320, H = 116, PB = 28, PT = 16;
  const max = Math.max(...weeks.map(w => w.distance_m || 0), 1);
  const currentWk = weekStart(appToday());
  const { bw, xOf, hOf } = barScale(weeks.length, max, { W, H, PT, PB });
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Kilometres per week, incomplete weeks hatched">
    <defs><pattern id="wip" width="5" height="5" patternUnits="userSpaceOnUse"
        patternTransform="rotate(45)">
      <!-- Colours via CLASSES, not via fill="var(--x)": presentation
           attributes do not resolve CSS variables, so the hatching would stay
           invisible and the running week would lose its marker. -->
      <rect class="wip-bg" width="5" height="5"/>
      <line class="wip-line" x1="0" y1="0" x2="0" y2="5"/>
    </pattern></defs>
    ${weeks.map((w, i) => {
      const h = hOf(w.distance_m || 0);
      const x = xOf(i), y = H - PB - h;
      // EVERY incomplete week is hatched — that is the running one (right)
      // AND the one cut off by the window edge (left). As a full column the
      // latter would claim a rest week that never happened. `partial` comes
      // from the server, "still running" from the calendar.
      const running = w.week_start === currentWk;
      const incomplete = weekIncomplete(w, currentWk);
      // A class instead of a fill attribute: the CSS rule `.barfill { fill }`
      // overrides any presentation attribute — the bar would stay solid even
      // with `fill="url(#wip)"` set.
      return `<rect class="barfill${incomplete ? " wip" : ""}" x="${x + 3}"
                y="${y.toFixed(1)}" width="${(bw - 6).toFixed(1)}"
                height="${Math.max(h, 1).toFixed(1)}" rx="3">
          <title>${esc(w.week_start)}: ${((Number(w.distance_m) || 0) / 1000).toFixed(1)} km${
            running ? " (week still running)"
                    : w.partial ? " (cut off by the time window)" : ""}</title></rect>
        <text class="barlabel" x="${(x + bw / 2).toFixed(1)}" y="${(y - 4).toFixed(1)}"
              text-anchor="middle">${Math.round((w.distance_m || 0) / 1000)}</text>
        <text x="${(x + bw / 2).toFixed(1)}" y="${H - 14}" text-anchor="middle">${
          esc(barLabel(w.week_start))}</text>`;
    }).join("")}
    <text x="4" y="${H - 2}">km per week · hatched = incomplete</text>
  </svg>`;
}

/* The most important number of the Trend tab must be SAID, not only drawn:
   weekly bars can show 55 → 21 km and a table can confirm it, while the view
   still never names it. The sentence stands at the top — and explains the
   ACWR in passing: a ratio of 1.7 need not come from an extreme week, it can
   come from a fallen chronic load. */
function renderTrendHead() {
  const el = document.getElementById("trend-head");
  const v = volumeComparison(D().weeks || [], weekStart(appToday()));
  const load = D().load || {};
  if (!v) {
    el.innerHTML = `<div class="subhead">Where you stand</div>
      <div class="empty-note">Too few completed weeks for a comparison yet.</div>`;
    return;
  }
  const down = v.percent != null && v.percent < 75;
  const up = v.percent != null && v.percent > 125;
  const bestRange = `${fmtDayShort(v.best_start)} – ${fmtDayShort(addDays(v.best_start, 27))}`;
  let sentence;
  if (down) {
    sentence = `Volume over the last four weeks at <b>${fmtNum(v.current_km, 0)} km</b> per week —
      <b>${100 - v.percent} % below</b> your best block (${fmtNum(v.best_km, 0)} km, ${esc(bestRange)}).`;
  } else if (up) {
    sentence = `Volume over the last four weeks at <b>${fmtNum(v.current_km, 0)} km</b> per week —
      ${v.percent - 100} % above your best earlier block (${fmtNum(v.best_km, 0)} km).`;
  } else {
    sentence = `Volume steady: <b>${fmtNum(v.current_km, 0)} km</b> per week over the last four weeks
      (best block ${fmtNum(v.best_km, 0)} km).`;
  }
  // An ACWR remark only where it follows from the numbers: a high ratio at
  // fallen volume = a small denominator, not an extreme numerator.
  let acwr = "";
  if (load.acwr != null && load.acwr > 1.3 && down) {
    acwr = `<div class="thin-data">ACWR ${fmtNum(load.acwr, 2)} is high because the chronic
      load has fallen to ${fmtNum(load.chronic_weekly)} per week — not because this week
      was extreme. The denominator is small, the numerator is not large.</div>`;
  } else if (load.acwr != null && load.acwr < 0.8) {
    acwr = `<div class="thin-data">ACWR ${fmtNum(load.acwr, 2)}: the last seven days are
      clearly below the four-week average — the stimulus is missing.</div>`;
  }
  el.innerHTML = `<div class="subhead">Where you stand</div>
    <div class="trend-sentence${down ? " s-warn" : ""}">${sentence}</div>${acwr}`;
}

function renderVolume() {
  const weeks = D().weeks || [];
  const avg = volumeAverageKm(weeks, weekStart(appToday()));
  document.getElementById("volume").innerHTML = `
    <div class="subhead">Volume</div>
    ${volumeChart(weeks)}
    <div class="note" style="margin-top:6px">${avg != null
      ? `Average of the completed weeks: ${fmtNum(avg, 1)} km`
      : "No completed week in the window yet."}</div>`;
}

function renderDistribution() {
  const i28 = (D().intensity || {}).d28 || {}, i84 = (D().intensity || {}).d84 || {};
  const el = document.getElementById("distribution");
  if (!i28.total_s) {
    el.innerHTML = `<div class="subhead">Distribution</div>
      <div class="empty-note">No zone times in the last 28 days.</div>`;
    return;
  }
  const grey = i28.moderate_pct;
  el.innerHTML = `
    <div class="subhead">Distribution (28 days)</div>
    ${bandBar({ z1: i28.easy_s, z2: 0, z3: i28.moderate_s, z4: i28.hard_s, z5: 0 })}
    ${grey != null && grey > 20
      ? `<div class="thin-data">${fmtNum(grey, 0)} % sit in zone 3 — the middle
           that is too hard for recovery and too soft for a stimulus.</div>` : ""}
    <div class="note" style="margin-top:8px">84 days: easy ${fmtNum(i84.easy_pct, 0)} % ·
      moderate ${fmtNum(i84.moderate_pct, 0)} % · hard ${fmtNum(i84.hard_pct, 0)} %
      (${esc(i84.with_detail)}/${esc(i84.total_runs)} runs with detail data)</div>`;
}

function renderAcwr() {
  const s = D().series || {}, load = D().load || {};
  const vals = (s.acwr_ratio || []).map((v, i) => [(s.days || [])[i], v]).filter(p => p[1] != null);
  const last = vals.slice(-10).reverse();
  document.getElementById("acwr").innerHTML = `
    <div class="subhead">Load</div>
    <div class="grid2">
      ${cell("ACWR", load.acwr != null ? fmtNum(load.acwr, 2) : "–", load.acwr_status || "")}
      ${cell("Acute (7 d)", fmtNum(load.acute_7d), "training load")}
      ${cell("Chronic", fmtNum(load.chronic_weekly), "per week")}
      ${cell("Status", load.training_status || "–", "Garmin")}
    </div>
    ${last.length ? `<div class="note" style="margin-top:8px">Latest: ${
      last.map(p => `${esc(fmtDayShort(p[0]))} ${fmtNum(p[1], 2)}`).join(" · ")}</div>` : ""}
    <div class="thin-data">Below 0.80 the stimulus is missing, above 1.30 the injury
      risk grows — in between is the range in which adaptation happens.</div>`;
}

/* ═══ Coach ════════════════════════════════════════════════════════════════
   Cards and the job strip come from /static/cards.js. Here is only what is
   particular to this page: the words per card kind. */
const KIND_TITLE = {
  "analyze-run": "Run analysis", "train-today": "Train today?",
  "why-vo2max": "VO2max", "week-review": "Training week",
};

/** Make a card's reference readable: "9000000042" becomes "Threshold run,
 *  Mon 7 Sep", the day reference of the morning card just the day. A raw
 *  activity id tells the user nothing. */
function refText(c) {
  if (c.kind === "analyze-run") {
    const r = (D().runs || []).find(x => String(x.activity_id) === String(c.ref));
    if (r) return `${r.name || "Run"}, ${fmtDay(r.day)}`;
    return c.ref ? `Run ${c.ref}` : "";
  }
  if (c.kind === "train-today") return c.day ? fmtDay(c.day) : "";
  return c.ref || "";
}

/* "Coach jobs", not "runs": right under a tab called Runs, a job strip using
   the same word could not be told apart. */
function renderJobs() {
  mountJobs("#coach-jobs", D().jobs || [],
            { finished: true, title: "Coach jobs", onChange: () => refresh() });
}

function renderCoach() {
  renderJobs();
  mountCards("#coach-cards", D().cards || [], {
    kindTitle: KIND_TITLE, filter: true, refText,
    emptyText: coachUnavailable()
      ? "No analyses yet."
      : "No analyses yet. Start one above — or tap a run and have it analyzed.",
  });
}

/* ═══ Frame ════════════════════════════════════════════════════════════════
   Tabs, reloading, loading state, error screens and data age live in
   `chassis.js`. Here is only the rendering and the job/coach machinery. */

/* Two failures that otherwise look like nothing at all, side by side above
   every tab:

   - The STARTUP SYNC. It runs before anyone can press ↻, so its verdict is
     reachable nowhere else. Without this the page showed "data is 5 days
     behind" with no cause, while the reason - "your Garmin session expired,
     run `runcoach login`" - sat in the terminal the app was started from. On
     a phone there is no such terminal.
   - The JOB RUNNER. When it is dead, cards simply stay `queued`, then the
     queue cap turns the next one into "queue full", and no surface says why.

   Both are silent by design when healthy: `#health:empty` collapses. */
function renderHealth() {
  const el = document.getElementById("health");
  const out = [];
  const sync = D().last_sync;
  // Suppressed ONLY on a true first run, where the verdict card carries the
  // guide instead. Gating this on `garmin_session !== false` was too coarse and
  // deleted the banner in a state it was written for: data in the store and the
  // token directory gone (a re-login in progress, or `RUNCOACH_GARMIN_TOKENS`
  // set in the shell that ran `login` but not in the one running `serve`). The
  // page then showed a normal verdict over data that had silently stopped
  // updating — reproduced in a browser, green dot and all.
  if (showSyncFailure(D())) {
    out.push(`<div class="stale-banner block">
      <span>The last Garmin sync did not go through${
        sync.at ? ` (${esc(fmtDay(sync.at.slice(0, 10)))})` : ""}. ` +
      `The figures below are the last ones that arrived.</span>
      ${sync.reason ? `<span class="why">${esc(String(sync.reason))}</span>` : ""}
      <span class="why">Press ↻ to try again. If it keeps failing, run
        <b>runcoach doctor</b> in a terminal - it names the next step.</span>
    </div>`);
  }
  // Any store read that failed and has NO card of its own to say so. Five of the
  // twelve possible names are handled where they belong (the threshold card, the
  // plan strip, the predictions card); the rest used to vanish silently — with
  // `activities` unreadable the page showed an empty run list, a null ACWR, an
  // all-zero intensity split and "not enough data for a verdict", which reads as
  // "you trained nothing for 28 days". That is precisely the confident lie the
  // `soft()` wrapper exists to prevent, so whatever is not named elsewhere is
  // named here.
  const OWN_CARD = ["latest_lactate_threshold", "max_hr_since", "lactate_threshold_history",
                    "get_scheduled_workouts", "latest_race_predictions"];
  const orphaned = (D().degraded || []).filter(n => !OWN_CARD.includes(n));
  if (orphaned.length) {
    out.push(`<div class="stale-banner block">
      <span>Part of the database could not be read, so some figures below are
        missing rather than zero.</span>
      <span class="why">${esc(orphaned.join(", "))}</span>
      <span class="why">Run <b>runcoach doctor</b> - it checks the database file
        and names the next step.</span>
    </div>`);
  }
  const w = D().worker;
  if (w && w.ok === false) {
    out.push(`<div class="stale-banner block">
      <span>${esc(String(w.reason || "the job runner is not healthy"))}</span>
      <span class="why">Analyses stay queued until it is running again.</span>
    </div>`);
  }
  el.innerHTML = out.join("");
}

function renderAll() {
  resetArm();
  document.getElementById("demo-badge").hidden = !D().demo;
  document.getElementById("about").textContent = D().version ? `runcoach ${D().version}` : "";
  renderVerdict(); renderWeek(); renderSignals(); renderSleep(); renderZones(); renderActions();
  renderRuns();
  renderTrendHead(); renderVo2(); renderThreshold(); renderIntensity(); renderAerobic();
  renderPredictions(); renderFactors(); renderVolume(); renderDistribution(); renderAcwr();
  renderCoach(); renderHealth();
}

document.addEventListener("click", ev => {
  const tpl = ev.target.closest("button[data-tpl]");
  if (tpl) {
    const ctx = tpl.dataset.ctx ? JSON.parse(tpl.dataset.ctx) : null;
    // An agent run is not free — a second tap confirms.
    armOrFire(tpl, "tpl:" + tpl.dataset.tpl + (tpl.dataset.ctx || ""),
              () => spawnTemplate(tpl.dataset.tpl, ctx));
    return;
  }
  const run = ev.target.closest("button[data-run]");
  if (run) {
    const id = Number(run.dataset.run);
    openRun = openRun === id ? null : id;
    renderRuns();
    document.querySelector(`button[data-run="${id}"]`)?.focus();
    return;
  }
});

/* No permanent poll on /api/state (tens of kilobytes, changes only on a sync):
   it is loaded at start, on returning to the page and after a job start. While
   a job is running, ONLY the slim jobs endpoint is polled — by the one poller
   from cards.js. */
const poller = jobPoller({
  jobs: () => state?.jobs,
  setJobs: jobs => {
    if (!state) return;
    state.jobs = jobs;
    renderJobs();
    renderActions();                        // unlock the buttons once nothing runs any more
  },
  onFinished: () => refresh(),
});

/* ↻ REALLY pulls: first the Garmin sync on the server (last night's sleep,
   fresh runs), then the new state. A button that merely re-reads the last
   snapshot leaves "no sleep for today yet" empty and the evening run
   invisible until the next scheduled sync. The sync takes 30–120 s; the
   button spins that long, polled every 3 s. */
async function syncAndLoad(fetchState) {
  // ALWAYS returns `true`: this page reports its end state itself, the
  // chassis must not cover it with a generic "Updated.".
  let start;
  try { start = await apiPost("/api/refresh", {}); }
  catch (e) { await fetchState(); throw e; }       // the chassis reports the error
  if (start.state === "fresh") {
    await fetchState();
    progressEnd(`Already up to date — ${statusText()}`);
    return true;
  }
  if (start.state === "error") {
    await fetchState();
    progressEnd("Sync does not start: " + (start.reason || "unknown"), { warn: true });
    return true;
  }
  progressInfo("Garmin sync running — pulling sleep and sessions…");
  const t0 = Date.now();
  while (Date.now() - t0 < 240_000) {
    await new Promise(res => setTimeout(res, 3000));
    let s;
    try { s = await apiGet("/api/refresh"); }
    catch (e) {
      if (e.status === 403) return true;           // login overlay is up; stop polling behind it
      continue;                                    // one missed poll does not matter
    }
    if (s.state === "running" || s.state === "started") continue;
    await fetchState();                            // done/fresh/error: load the state
    if (s.state === "done" || s.state === "fresh") {
      progressEnd(`Updated — ${statusText()}`);
    } else {
      progressEnd("Sync problem: " + (s.reason || `state ${s.state}`)
                  + " — showing the last state.", { warn: true });
    }
    return true;
  }
  await fetchState();
  progressEnd("The sync is still running — pull ↻ again in a moment.", { warn: true });
  return true;
}

/** Short data status for the result line: "data up to Mon, 7 Sep · 2 sessions
 *  this week". Without it the line would only say "Updated" — and the very
 *  question was whether anything had changed at all. */
function statusText() {
  const parts = [];
  if (state?.data_through) parts.push("data up to " + fmtDay(state.data_through));
  const week = state?.weeks?.[state.weeks.length - 1];
  const n = week?.workouts;
  if (typeof n === "number") parts.push(`${plural(n, "session", "sessions")} this week`);
  return parts.join(" · ") || "no data status";
}

const app = mountApp({
  marker: "runcoach", api: "/api/state", schema: 1,
  views: ["today", "runs", "trend", "coach"], initial: "today",
  head: "#verdict", clear: ["#week", "#signals", "#sleep", "#zones", "#actions-today"],
  skeleton: [{ sel: "#verdict", kind: "title" }, { sel: "#signals", kind: "grid", n: 6 }],
  staleText: d => `Data up to ${fmtDay(d.data_through)} — the Garmin sync is `
                + `${plural(d.stale_days, "day", "days")} behind.`,
  empty: hasNoData,           // one definition of "nothing here" for header and page
  render: p => { state = p; renderAll(); },
  onAfterLoad: () => poller.start(),
  onVisible: () => poller.start(),
  onRefresh: syncAndLoad,
});
// Follow-up questions on a card start a job: pull the state, start the poller.
setCardContext({ onJobStart: async () => { await refresh().catch(() => {}); poller.start(); } });
const showView = app.showView;
const reload = app.reload;
const refresh = app.fetchState;
