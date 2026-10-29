// The "You" tab. Three cards:
//   · Your week - the foundation the planner goes by. Filled from the history
//     (`habits.py`) and kept up to date by it; what the athlete sets wins
//     until they hand it back (`profile.py`, POST /api/profile).
//   · For your coach - the athlete's own note, read by the coach (never by a
//     rule) before every answer.
//   · What the coach has learned - the other facts (`facts.py`), read-only.
// Pure: state in, HTML out, so web-tests render it without a page. The draft
// of an edit lives in app.js, because the page re-renders on every poll.
import { esc } from "./ui.js";

export const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const SHORT = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };

/** The week settings in the order the editor shows them. `fact` is the key of
 *  the matching fact, whose evidence the read view shows. */
export const WEEK_ROWS = [
  { key: "days_per_week", fact: "days", label: "Runs per week", kind: "count" },
  { key: "run_days", fact: "weekdays", label: "Your running days", kind: "multi",
    hint: "easy runs go here first" },
  { key: "long_run_day", fact: "long_run", label: "Long run", kind: "one" },
  { key: "off_days", fact: "off_days", label: "Days you can't run", kind: "multi",
    hint: "nothing is ever planned here" },
  { key: "sport_days", fact: "other_sport", label: "Other sport", kind: "multi",
    hint: "no hard run that day or the next" },
];
const WEEK_FACTS = new Set(WEEK_ROWS.map(r => r.fact));

//: The planner's defaults and where a long run goes when its day is a day
//: off - the same order as `habits._LONG_FALLBACK`.
const DEFAULT_DAYS = 4;
const LONG_FALLBACK = ["sun", "sat", "fri", "thu", "wed", "tue", "mon"];

/** The value the planner uses for `key`, and where it comes from - as the
 *  server computed it (`habits.effective`), so the tab never shows a day the
 *  planner has moved off a day off. */
export function effective(week, key) {
  const e = ((week && week.effective) || {})[key];
  if (e) return { value: e.value, source: e.source };
  const set = (week && week.set) || {};
  if (set[key] !== undefined && set[key] !== null) return { value: set[key], source: "you" };
  const h = ((week && week.history) || {})[key];
  if (h !== undefined && h !== null) return { value: h, source: "history" };
  return { value: null, source: null };
}

/** What the history gives for `key` with these days off - the editor's
 *  preview of the planner's rule while a draft is open (`habits.effective`):
 *  a day off leaves the running days, moves the long run (weekend first) and
 *  caps the runs a week at the free days. Pinned against Python in
 *  tests/test_js_python_contract.py. */
export function historyValue(week, key, offDays) {
  const off = new Set(offDays || []);
  const h = ((week && week.history) || {})[key];
  if (key === "run_days") return h ? h.filter(d => !off.has(d)) : null;
  if (key === "long_run_day") return [h, ...LONG_FALLBACK].find(d => d && !off.has(d)) || null;
  if (key === "days_per_week") return Math.min(h ?? DEFAULT_DAYS, 7 - off.size);
  if (key === "off_days") return [];
  return h ?? null;
}

/** Whether the draft holds the athlete's own value for `key` (saved and not
 *  handed back, or changed in this edit). */
function mine(draft, week, key) {
  const set = (week && week.set) || {};
  return draft.touched.includes(key) || (set[key] !== undefined && !draft.reset.includes(key));
}

/** The values of the keys the athlete did not touch in this edit: derived
 *  from what is saved and the draft's days off, never sent. A day off can
 *  therefore change what they SEE for their running days and long run, and
 *  taking it back restores it - the save carries only what they tapped. */
function derive(draft, week) {
  const values = { ...draft.values };
  const set = (week && week.set) || {};
  const off = values.off_days || [];
  for (const r of WEEK_ROWS) {
    if (draft.touched.includes(r.key) || r.key === "off_days") continue;
    if (!mine(draft, week, r.key)) values[r.key] = historyValue(week, r.key, off);
    else if (r.key === "run_days") values[r.key] = set.run_days.filter(d => !off.includes(d));
    else values[r.key] = set[r.key];
  }
  return { ...draft, values };
}

/** A fresh draft for the editor: the effective values, nothing touched yet. */
export function newDraft(week) {
  const values = {};
  for (const r of WEEK_ROWS) {
    const v = effective(week, r.key).value;
    values[r.key] = Array.isArray(v) ? [...v] : v;
  }
  if (!Array.isArray(values.off_days)) values.off_days = [];
  return { values, touched: [], reset: [] };
}

/** One tap in the editor, applied to the draft (returns a new draft). Only
 *  the tapped key becomes the athlete's own; the others are re-derived. */
export function pick(draft, key, value, week = null) {
  const values = { ...draft.values };
  const row = WEEK_ROWS.find(r => r.key === key);
  if (!row) return draft;
  if (row.kind === "count") values[key] = Number(value);
  else if (row.kind === "one") values[key] = value;
  else {
    const cur = new Set(values[key] || []);
    cur.has(value) ? cur.delete(value) : cur.add(value);
    values[key] = WEEKDAYS.filter(d => cur.has(d));
  }
  const touched = [...new Set([...draft.touched, key])];
  return derive({ values, touched, reset: draft.reset.filter(k => k !== key) }, week);
}

/** "Use my history" for one row: the saved setting is removed on save, and
 *  the row shows what the history gives with the draft's days off. */
export function resetRow(draft, week, key) {
  const touched = draft.touched.filter(k => k !== key);
  const reset = new Set(draft.reset);
  if (((week && week.set) || {})[key] !== undefined) reset.add(key);
  const values = { ...draft.values };
  if (key === "off_days") values.off_days = [];
  return derive({ values, touched, reset: [...reset] }, week);
}

/** The body for POST /api/profile: touched keys with their value, handed-back
 *  keys as null. An empty list of days off is no setting at all. */
export function draftPatch(draft) {
  const set = {};
  for (const k of draft.touched) {
    const v = draft.values[k];
    set[k] = k === "off_days" && Array.isArray(v) && !v.length ? null : v;
  }
  for (const k of draft.reset) set[k] = null;
  return set;
}

function showValue(row, v) {
  if (v === null || v === undefined) return "not known yet";
  if (row.kind === "count") return `${v} a week`;
  if (row.kind === "one") return SHORT[v] || v;
  return v.length ? v.map(d => SHORT[d] || d).join(", ") : "none";
}

function source(src) {
  if (src === "you") return `<span class="src src-you">set by you</span>`;
  if (src === "history") return `<span class="src">from your history</span>`;
  if (src === "default") return `<span class="src">default</span>`;
  return "";
}

/** The seven days at a glance: what each one is for this athlete. */
function weekStrip(week) {
  const long = effective(week, "long_run_day").value;
  const runs = new Set(effective(week, "run_days").value || []);
  const off = new Set(effective(week, "off_days").value || []);
  const sport = new Set(effective(week, "sport_days").value || []);
  const tiles = WEEKDAYS.map(d => {
    const role = off.has(d) ? ["off", "Off"] : d === long ? ["long", "Long"]
      : runs.has(d) ? ["run", "Run"] : sport.has(d) ? ["sport", "Sport"] : ["free", "·"];
    const extra = sport.has(d) && role[0] !== "sport" && role[0] !== "off"
      ? `<span class="day-extra">+ sport</span>` : "";
    return `<div class="day-tile day-${role[0]}">
        <span class="day-name">${SHORT[d]}</span><span class="day-role">${role[1]}</span>${extra}
      </div>`;
  }).join("");
  return `<div class="week-strip" aria-label="Your week">${tiles}</div>`;
}

function readRows(week, facts) {
  const byFact = Object.fromEntries((facts || []).map(f => [f.key, f]));
  return WEEK_ROWS.map(r => {
    const { value, source: src } = effective(week, r.key);
    if (r.key === "off_days" && !(value || []).length) return "";
    if (r.key === "sport_days" && src !== "you" && !(value || []).length) return "";
    const f = byFact[r.fact];
    const evidence = f ? f.evidence + (f.change ? ` · ${f.change}` : "") : (r.hint || "");
    return `<div class="week-row">
        <div class="week-k">${esc(r.label)}</div>
        <div class="week-v">${esc(showValue(r, value))}</div>
        ${source(src)}
        <div class="note week-ev">${esc(evidence)}</div>
      </div>`;
  }).join("");
}

function chips(row, draft, week, saving) {
  const v = draft.values;
  const busy = saving ? "disabled" : "";
  if (row.kind === "count") {
    const free = 7 - (v.off_days || []).length;
    return `<div class="seg" role="group" aria-label="${esc(row.label)}">${[2, 3, 4, 5, 6, 7].map(n =>
      `<button type="button" class="chip" data-week-pick="${row.key}" data-value="${n}"
         aria-pressed="${v[row.key] === n}" ${n > free || saving ? "disabled" : ""}>${n}</button>`).join("")}</div>`;
  }
  const off = new Set(v.off_days || []);
  return `<div class="chip-week" role="group" aria-label="${esc(row.label)}">${WEEKDAYS.map(d => {
    const on = row.kind === "one" ? v[row.key] === d : (v[row.key] || []).includes(d);
    // A day off cannot be the long run or a running day, and a long-run day
    // the athlete set cannot be taken off - the server refuses both, the chips
    // show it. The history's long-run day can: the long run moves.
    const blocked = (row.key !== "off_days" && row.key !== "sport_days" && off.has(d))
      || (row.key === "off_days" && v.long_run_day === d && mine(draft, week, "long_run_day"))
      || (row.key === "off_days" && !on && mine(draft, week, "days_per_week")
          && v.days_per_week > 6 - (v.off_days || []).length);
    return `<button type="button" class="chip" data-week-pick="${row.key}" data-value="${d}"
        aria-pressed="${on}" ${blocked ? "disabled" : busy}
        ${blocked ? `title="${row.key === "off_days" ? "your long run day" : "a day off"}"` : ""}>${SHORT[d]}</button>`;
  }).join("")}</div>`;
}

function editRows(week, draft, saving) {
  return WEEK_ROWS.map(r => {
    const own = mine(draft, week, r.key);
    // The history's value in the label only where there is one: a default is not a history.
    const known = (((week && week.history) || {})[r.key] ?? null) !== null;
    const h = r.key === "off_days" || !known ? null : historyValue(week, r.key, draft.values.off_days);
    const back = own && r.key !== "off_days"
      ? `<button type="button" class="linkish" data-week-reset="${r.key}">use my history${
          h !== null && h !== undefined ? ` (${esc(showValue(r, h))})` : ""}</button>`
      : own ? `<button type="button" class="linkish" data-week-reset="${r.key}">clear</button>` : "";
    return `<div class="edit-row">
        <div class="edit-head"><span class="week-k">${esc(r.label)}</span>${back}</div>
        ${chips(r, draft, week, saving)}
        ${r.hint ? `<div class="note">${esc(r.hint)}</div>` : ""}
      </div>`;
  }).join("");
}

function weekCard(week, facts, ui) {
  const draft = ui && ui.draft;
  if (!week) return "";
  const head = `<div class="you-head"><div class="subhead">Your week</div>${draft ? ""
    : `<button type="button" class="btn btn-quiet" data-week-edit>Edit</button>`}</div>`;
  if (draft) {
    const dirty = draft.touched.length || draft.reset.length;
    return `<div class="card you-week editing">${head}
      <div class="note">Tap to change. What you set wins over your history until you hand it back.</div>
      ${editRows(week, draft, ui.saving)}
      ${ui.error ? `<div class="form-error" role="alert">${esc(ui.error)}</div>` : ""}
      <div class="btn-row form-actions">
        <button type="button" class="btn btn-primary" data-week-save ${dirty && !ui.saving ? "" : "disabled"}>
          ${ui.saving ? "Saving…" : "Save"}</button>
        <button type="button" class="btn btn-quiet" data-week-cancel ${ui.saving ? "disabled" : ""}>Cancel</button>
      </div></div>`;
  }
  return `<div class="card you-week">${head}
    <div class="note">What the week plan goes by - read from your history and kept up to date.
      Not right any more? Edit it: what you set wins.</div>
    ${weekStrip(week)}
    <div class="week-rows">${readRows(week, facts)}</div></div>`;
}

function noteCard(week, ui) {
  if (!week) return "";
  const max = week.note_max || 600;
  const text = ui && typeof ui.note === "string" ? ui.note : (week.note || "");
  const dirty = text.trim() !== (week.note || "").trim();
  return `<div class="card you-note">
    <div class="subhead">For your coach</div>
    <textarea class="field note-input" data-note maxlength="${max}" rows="3"
      aria-label="A note for your coach"
      placeholder="e.g. Strength training three times a week, usually Mon, Wed and Fri. Less time than usual until December.">${esc(text)}</textarea>
    <div class="note-foot">
      <span class="note">The coach reads this before every answer and week plan. The days above
        are what the planner itself keeps to.</span>
      <span class="note note-count" data-note-count>${text.length}/${max}</span>
    </div>
    ${ui && ui.noteError ? `<div class="form-error" role="alert">${esc(ui.noteError)}</div>` : ""}
    <div class="btn-row form-actions">
      <button type="button" class="btn btn-primary" data-note-save ${dirty ? "" : "disabled"}>Save note</button>
    </div></div>`;
}

function learnedCard(facts) {
  const rest = (facts || []).filter(f => !WEEK_FACTS.has(f.key));
  if (!rest.length) {
    return `<div class="card"><div class="subhead">What the coach has learned about you</div>
      <div class="empty-note">Nothing yet — after a few weeks of synced running the coach knows
        your days, your long run and how you train.</div></div>`;
  }
  const tiles = rest.map(f => `<div class="fact">
      <div class="fact-k">${esc(f.label)}</div>
      <div class="fact-v">${esc(f.value)}</div>
      <div class="note">${esc(f.evidence)}${f.change
        ? ` · <span class="fact-change">${esc(f.change)}</span>` : ""}</div>
    </div>`).join("");
  return `<div class="card"><div class="subhead">What the coach has learned about you</div>
    <div class="note">Read from your own data every time the page loads — nothing is stored or
      guessed. The coach reads this before every answer.</div>
    <div class="facts">${tiles}</div></div>`;
}

/** The whole tab. `week` is the server's week settings (`facts.week_settings`),
 *  `ui` the editor's state from app.js: `{draft, error, saving, note, noteError}`. */
export function youHtml(facts, week = null, ui = {}) {
  return weekCard(week, facts, ui) + noteCard(week, ui) + learnedCard(facts);
}
