/* ═══════════════════════════════════════════════════════════════════════════
   Coach cards + job strip — ONE renderer, ONE poller.

   Two layers in one file, deliberately kept apart:
   · PURE LOGIC (top) — computes without a DOM and is tested under Node
     (web-tests/cards.test.mjs): meta line, average duration, parent/child
     grouping, 409 classification, today's card, truncation.
   · DOM (bottom) — renderCard / mountCards / mountJobs / jobPoller.

   Invariants that are NOT negotiable here:
   · Everything from a card or a job is model text or foreign text and goes
     through `esc()`. No Markdown parser — `**` and `#` are removed, not
     interpreted.
   · Feedback and follow-up questions belong to every card: each one comes
     from a job the user started (the server writes `source = "job"`).

   Import is relative (`./ui.js`), not `/static/ui.js`: Node does not know the
   server root, and this module is imported by tests.
   ═══════════════════════════════════════════════════════════════════════════ */

import { esc, fmtAgo, fmtDay, toast, apiGet, apiPost, armOrFire,
         resetArm } from "./ui.js";

/* ═══ Pure logic ═══════════════════════════════════════════════════════════ */

export const ACTIVE_STATUS = ["queued", "running"];
export const isActive = j => ACTIVE_STATUS.includes(j?.status);
export const cardId = c => String(c?.id || c?.job_id || "");

/** Text cut to `max` characters with "…" at the end — without half a word at
 *  the edge. */
export function truncate(t, max = 400) {
  const s = String(t ?? "");
  if (s.length <= max) return s;
  const short = s.slice(0, Math.max(1, max - 1));
  const cut = short.lastIndexOf(" ");
  return (cut > max * 0.6 ? short.slice(0, cut) : short).trimEnd() + "…";
}

/** "claude-opus-5" → "opus-5"; a date suffix ("-20260901") is dropped. */
export function modelShort(m) {
  return String(m || "").replace(/^claude-/, "").replace(/-\d{8}$/, "");
}

/** Building blocks of the meta line, in display order. `fmt` is injectable so
 *  that the test can check the structure instead of a locale's output.
 *  Cards without the runner's stamps fall back to `day`/`written` — they then
 *  show less, but nothing wrong. */
export function metaParts(c, { day = fmtDay, ago = fmtAgo } = {}) {
  const t = [];
  if (c.data_through) t.push("data up to " + day(c.data_through));
  else if (c.day) t.push(day(c.day));
  if (c.generated_at) t.push("generated " + ago(c.generated_at));
  else if (c.written) t.push("generated " + ago(c.written));
  const m = modelShort(c.model);
  if (m) t.push(m);
  // `cost_usd` stays in the card file and is deliberately not shown: on a
  // subscription the CLI's figure is nominal, and a price on every card makes
  // it the thing the eye compares. See the note above `tplButton` in app.js.
  return t;
}

/** Average duration of finished runs in seconds (finished − started); null
 *  without a basis. */
export function avgDurationSec(jobs) {
  const d = (jobs || [])
    .filter(j => j.status === "done" && j.started && j.finished)
    .map(j => (new Date(j.finished) - new Date(j.started)) / 1000)
    .filter(s => Number.isFinite(s) && s > 0);
  return d.length ? d.reduce((a, b) => a + b, 0) / d.length : null;
}

/** "~40 s" / "~2 min" — an expectation, not a clock. */
export function fmtExpectation(sec) {
  if (sec == null || !Number.isFinite(sec)) return "";
  if (sec < 90) return `~${Math.max(10, Math.round(sec / 10) * 10)} s`;
  return `~${Math.round(sec / 60)} min`;
}

/** Parent/child: answer cards (`parent`) hang below their parent card, orphans
 *  (parent not in the list) stay visible in the main list. Children in
 *  chronological order (oldest first — a conversation is read forwards), the
 *  main list keeps the order of the source (newest first). */
export function groupCards(cards) {
  const all = (cards || []).filter(c => cardId(c));
  const ids = new Set(all.map(cardId));
  const main = [], children = new Map();
  for (const c of all) {
    const p = c.parent ? String(c.parent) : "";
    if (p && p !== cardId(c) && ids.has(p)) {
      if (!children.has(p)) children.set(p, []);
      children.get(p).push(c);
    } else main.push(c);
  }
  for (const k of children.values()) k.reverse();
  return { main, children };
}

/** The 409 answer of /api/spawn has three faces. The switch is the `code`
 *  field of the response — never the message text, which is free to change:
 *  `already_running` (ref = job id) → just show it; `card_current` (ref = card
 *  id) → offer "recompute", which repeats the request with force:true;
 *  `queue_full` → just show it. Accepts the thrown error or a plain object. */
export function classify409(err) {
  const code = String(err?.code || err?.data?.code || "");
  const ref = err?.ref || err?.data?.ref || null;
  if (code === "already_running") return { type: "running", ref };
  if (code === "card_current") return { type: "current", ref };
  if (code === "queue_full") return { type: "queue_full", ref };
  return { type: "other", ref };
}

/** Newest card of one kind (main list, no answers) — and whether it is from
 *  today. `today` is an ISO day (local!), compared against `day`. */
export function todayCard(cards, today, kind = "train-today") {
  const k = (cards || [])
    .filter(c => c && c.kind === kind && !c.parent)
    .sort((a, b) => String(b.day || "").localeCompare(String(a.day || ""))
               || String(b.generated_at || b.written || "")
                    .localeCompare(String(a.generated_at || a.written || "")));
  if (!k.length) return null;
  return { card: k[0], current: k[0].day === today };
}

/** Up to `n` points visible, the rest behind a disclosure. Default 2: with
 *  five open bullets of 400 characters each, the newest card was ~1400 px tall
 *  on a phone — one found the verdict, but never the end. */
export function splitBullets(bullets, n = 2) {
  const b = Array.isArray(bullets) ? bullets.filter(x => typeof x === "string" && x) : [];
  return { visible: b.slice(0, n), rest: b.slice(n) };
}

/** Full text for display: line breaks stay, markup goes. NO Markdown parser —
 *  the text is escaped afterwards and placed into a `white-space: pre-wrap`
 *  element.
 *
 *  Also covers chat-style Markdown (bold with ONE asterisk, italics with
 *  underscores, bare code fences). A pair is only removed if it sits on the
 *  SAME line and encloses non-empty text: a single asterisk (multiplication,
 *  footnote) and `snake_case` stay untouched. */
export function textPlain(t) {
  return String(t ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/^ *```.*$/gm, "")
    .replace(/\*\*(\S(?:[^*\n]*\S)?)\*\*/g, "$1")
    .replace(/\*\*/g, "")
    .replace(/\*(\S(?:[^*\n]*\S)?)\*/g, "$1")
    .replace(/(^|[\s(])_(\S(?:[^_\n]*\S)?)_(?=$|[\s.,;:!?)])/gm, "$1$2")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** What belongs in the job strip: everything active, plus failures and
 *  cancellations of the last 24 h (with their reason) — and on request
 *  (`finished`) the completed runs of the last 24 h as well. Filtering on
 *  queued/running alone lets a run that died while waiting for quota vanish
 *  without a trace. */
export function jobsForStrip(jobs, { now = Date.now(), finished = false, max = 8 } = {}) {
  const limit = now - 24 * 3600e3;
  const recent = j => {
    const t = new Date(j.finished || j.started || j.created || 0).getTime();
    return Number.isFinite(t) && t >= limit;
  };
  return (jobs || []).filter(j => j && j.id && (
    isActive(j)
    || (j.status !== "done" && recent(j))
    || (finished && j.status === "done" && recent(j))
  )).slice(0, max);
}

/* ═══ DOM ══════════════════════════════════════════════════════════════════ */

const JOB_LABEL = { queued: "queued", running: "running", done: "done",
                    failed: "failed", timeout: "timeout", cancelled: "cancelled" };
const JOB_PILL = { queued: "pill-unknown", running: "pill-warn", done: "pill-ok",
                   failed: "pill-down", timeout: "pill-down", cancelled: "pill-stale" };

const openCards = new Set();         // expanded card ids, page-wide
const seen = new Map();              // container key → Set(ids) for auto-expand
const filterKind = new Map();        // container key → chosen kind
const state = new WeakMap();         // container element → {cards, opts}
const jobsState = new WeakMap();     // container element → {jobs, opts}
const followUps = new Map();         // card id → {job_id, ts}
const jobLogs = new Map();           // job id → lines[] of the open log
// Expanded <details> survive the re-render: the job poll rebuilds every 8 s,
// and "3 more points" would snap shut each time — unusable while reading a
// long analysis. Key "<id>|<name>".
const openDetails = new Set();
let context = {};                    // page-wide callbacks (setCardContext)
let installed = false;

/** Callbacks of the page: `onJobStart(r)` after a follow-up question was
 *  started (pull the jobs, start the poller). Applies to all containers that
 *  carry no `onJobStart` of their own in their opts. */
export function setCardContext(k) { context = { ...context, ...k }; }

function elOf(x) { return typeof x === "string" ? document.querySelector(x) : x; }
function keyOf(el) { return el.dataset.cardsKey || el.id || "_"; }

/* ── Card ────────────────────────────────────────────────────────────────── */

/** One card as HTML. `opts`: kindTitle {kind→title}, defaultTitle, children
 *  [answer cards], feedback/followUp (false = hide), open (overrides the
 *  remembered state), noRef, refText(c). Needs NO DOM — a pure string
 *  function, which is what makes the escaping invariant testable. */
export function renderCard(c, opts = {}) {
  const id = cardId(c);
  const open = opts.open ?? openCards.has(id);
  const title = (opts.kindTitle || {})[c.kind] || opts.defaultTitle || "Analysis";
  const children = opts.children || [];
  const meta = metaParts(c);
  const p = followUps.get(id);
  if (p && (children.some(k => cardId(k) === p.job_id || String(k.job_id || "") === p.job_id)
            || Date.now() - p.ts > 45 * 60e3)) {
    followUps.delete(id);
  }

  // A readable reference instead of a raw one: `opts.refText(c)` may turn an
  // activity id into "Threshold run, Mon 7 Sep". Without a resolver the raw
  // value stays — but a bare number like "9000000042" tells the user nothing.
  const refTxt = opts.noRef ? "" : String((opts.refText ? opts.refText(c) : c.ref) || "");
  const det = name => {
    const k = `${id}|${name}`;
    return `data-det="${esc(k)}"${openDetails.has(k) ? " open" : ""}`;
  };
  const head = `<button type="button" class="card-head" data-card="${esc(id)}" aria-expanded="${open}">
      <span class="card-meta">${esc(title)}${refTxt ? " · " + esc(refTxt) : ""}${
        meta[0] ? " · " + esc(meta[0]) : ""}</span>
      <h3>${esc(textPlain(c.headline) || "(no headline)")}</h3>
      ${c.verdict ? `<span class="card-verdict">${esc(textPlain(c.verdict))}</span>` : ""}
      <span class="chev" aria-hidden="true">${open ? "▴" : "▾"}</span>
    </button>`;

  let body = "";
  if (open) {
    const { visible, rest } = splitBullets(c.bullets);
    // `textPlain` FIRST, then truncate, then escape. The model answers in chat
    // Markdown often enough ("**Sessions:** 3 today"), and without this the
    // asterisks and fences were rendered literally - the function existed and
    // was fully tested, but nothing called it. `card-text` keeps the line
    // breaks that `textPlain` preserves.
    //
    // The HEADLINE, the verdict and the delta go through it too: wiring only
    // the bullets left the most prominent line on the card - the one in the
    // <h3> - showing `**Bold** headline` verbatim.
    const points = list => list.map(
      b => `<p class="card-text">${esc(truncate(textPlain(b), 400))}</p>`).join("");
    const content = points(visible) + (rest.length
      ? `<details class="card-more" ${det("more")}><summary>${rest.length} more point${
          rest.length === 1 ? "" : "s"}</summary>${points(rest)}</details>`
      : "");
    // Delta, feedback and follow-up each sit behind one line: they are tools,
    // not content — left open they doubled the height of the card.
    const delta = c.delta
      ? `<details class="card-more card-delta-wrap" ${det("delta")}><summary>Since the last card</summary>
           <div class="card-delta">${esc(textPlain(c.delta))}</div></details>` : "";
    const metaLine = meta.length ? `<div class="card-metaline">${meta.map(esc).join(" · ")}</div>` : "";
    const fb = opts.feedback !== false ? renderFeedback(id, c.feedback) : "";
    const pending = followUps.get(id);
    const fu = opts.followUp !== false ? renderFollowUp(id, pending) : "";
    const value = c.feedback?.value;
    const respond = (fb || fu)
      ? `<details class="card-more card-respond" ${det("respond")}${pending || value ? " open" : ""}>
           <summary>Feedback${value ? (value === "good" ? " · 👍" : " · 👎") : ""}${
             pending ? " · answer on its way" : ""}</summary>${fb}${fu}</details>` : "";
    body = `<div class="card-body">${content || `<div class="empty-note">No content.</div>`}${
      delta}${metaLine}${respond}</div>`;
  }
  // Answers hang visibly BELOW the parent card — even when it is collapsed,
  // otherwise nobody would see that a follow-up question has been answered.
  const childrenHtml = children.length
    ? `<div class="card-children">${children.map(k =>
        renderCard(k, { ...opts, children: [], defaultTitle: "Answer",
                        noRef: true, open: undefined })).join("")}</div>`
    : "";
  return `<article class="coach-card${c.parent ? " card-child" : ""}"
             data-card-id="${esc(id)}">${head}${body}${childrenHtml}</article>`;
}

function renderFeedback(id, fb) {
  const value = fb?.value || "";
  const button = (v, sign, label) =>
    `<button type="button" class="fb-btn${value === v ? " active" : ""}" data-fb="${v}"
             aria-pressed="${value === v}" aria-label="${label}" title="${label}">${sign}</button>`;
  return `<div class="card-fb" data-fb-card="${esc(id)}">
    <span class="note">Helpful?</span>
    ${button("good", "👍", "good")}${button("bad", "👎", "bad")}
    <input type="text" class="fb-text" data-fb-text maxlength="500"
           placeholder="briefly why (optional)" aria-label="Feedback text"
           value="${esc(fb?.text || "")}">
    <button type="button" class="fb-send" data-fb-send>OK</button>
    ${fb?.ts ? `<span class="note fb-saved">saved ${esc(fmtAgo(fb.ts))}</span>` : ""}
    <button type="button" class="fb-del" data-card-del="${esc(id)}"
            title="Delete this card" aria-label="Delete this card">Delete</button>
  </div>`;
}

function renderFollowUp(id, pending) {
  return `<div class="card-followup" data-fu-card="${esc(id)}">
    ${pending ? `<div class="note card-followup-pending">The answer arrives as a card${
       pending.job_id ? ` (${esc(pending.job_id)})` : ""} —
       the run is listed in the job strip.</div>` : ""}
    <input type="text" class="fu-text" data-fu-text maxlength="1000"
           placeholder="Ask a follow-up…" aria-label="Follow-up question on this card">
    <button type="button" class="fu-send" data-fu-send>Send</button>
  </div>`;
}

/* ── Card list ───────────────────────────────────────────────────────────── */

/** Renders `cards` into `container`. Remembers state on the element
 *  (re-render after tap/feedback/follow-up). `opts` as for renderCard, plus:
 *  autoOpen (default true: the newest NEW card expands), filter (pills by
 *  kind), emptyText (null = render nothing when empty), onJobStart. */
export function mountCards(container, cards, opts = {}) {
  install();
  const el = elOf(container);
  if (!el) return;
  el.dataset.cards = "1";
  cards = (cards || []).filter(c => c && cardId(c));
  state.set(el, { cards, opts });
  const key = keyOf(el);

  if (opts.autoOpen !== false && cards.length) {
    // Every card that shows up NEW and is the newest one expands — including
    // the one that was just paid for, not only on the first render.
    const before = seen.get(key) || new Set();
    const newest = cardId(cards[0]);
    if (!before.has(newest)) openCards.add(newest);
  }
  seen.set(key, new Set(cards.map(cardId)));

  const { main, children } = groupCards(cards);
  const kinds = [...new Set(main.map(c => c.kind).filter(Boolean))];
  let fk = filterKind.get(key) || "";
  if (fk && !kinds.includes(fk)) fk = "";
  const pills = opts.filter && kinds.length > 1
    ? `<div class="card-filter" role="group" aria-label="Filter by kind">${
        ["", ...kinds].map(k => `<button type="button" class="card-pill${fk === k ? " active" : ""}"
            data-card-filter="${esc(k)}" aria-pressed="${fk === k}">${
            esc(k ? (opts.kindTitle || {})[k] || k : "All")}</button>`).join("")}</div>`
    : "";
  const list = main.filter(c => !fk || c.kind === fk);

  const inputs = saveInputs(el);
  if (!list.length) {
    el.innerHTML = opts.emptyText === null ? "" : pills
      + `<div class="card"><div class="empty-note">${esc(opts.emptyText || "No analyses yet.")}</div></div>`;
    return;
  }
  el.innerHTML = pills + list.map(c =>
    renderCard(c, { ...opts, children: children.get(cardId(c)) || [] })).join("");
  restoreInputs(el, inputs);
}

/* Rescue typed text across an innerHTML rebuild: the job poll and a feedback
   tap must not empty a half-written "why". */
function saveInputs(el) {
  const m = new Map();
  el.querySelectorAll("[data-fb-text], [data-fu-text]").forEach(inp => {
    if (inp.value && inp.value !== inp.defaultValue) {
      const wrap = inp.closest("[data-fb-card], [data-fu-card]");
      const id = wrap?.dataset.fbCard || wrap?.dataset.fuCard;
      if (id) m.set((inp.hasAttribute("data-fb-text") ? "fb:" : "fu:") + id, inp.value);
    }
  });
  return m;
}
function restoreInputs(el, m) {
  if (!m.size) return;
  el.querySelectorAll("[data-fb-text], [data-fu-text]").forEach(inp => {
    const wrap = inp.closest("[data-fb-card], [data-fu-card]");
    const id = wrap?.dataset.fbCard || wrap?.dataset.fuCard;
    const v = m.get((inp.hasAttribute("data-fb-text") ? "fb:" : "fu:") + id);
    if (v != null) inp.value = v;
  });
}

function rerender(fromEl) {
  const cont = fromEl?.closest?.("[data-cards]");
  const z = cont && state.get(cont);
  if (z) { mountCards(cont, z.cards, z.opts); return cont; }
  context.rerender?.(fromEl);
  return null;
}

function findCard(fromEl, id) {
  const cont = fromEl?.closest?.("[data-cards]");
  const z = cont && state.get(cont);
  const source = z ? z.cards : (context.cards?.() || []);
  return source.find(c => cardId(c) === id) || null;
}

function optsOf(fromEl) {
  const cont = fromEl?.closest?.("[data-cards]");
  return (cont && state.get(cont)?.opts) || {};
}

/* ── Interaction (once, delegated) ───────────────────────────────────────── */

function install() {
  if (installed || typeof document === "undefined") return;
  installed = true;

  // `toggle` does not bubble — the capture phase on the document catches
  // every <details>.
  document.addEventListener("toggle", ev => {
    const d = ev.target;
    const k = d?.dataset?.det;
    if (!k) return;
    if (d.open) openDetails.add(k); else openDetails.delete(k);
  }, true);

  document.addEventListener("click", ev => {
    const head = ev.target.closest("button[data-card]");
    if (head) {
      const id = head.dataset.card;
      if (openCards.has(id)) openCards.delete(id); else openCards.add(id);
      rerender(head);
      // The re-render replaces the button — find the focus again, otherwise
      // it lands on <body> and the next Tab starts from the very top.
      document.querySelector(`button[data-card="${CSS.escape(id)}"]`)?.focus();
      return;
    }
    const pill = ev.target.closest("[data-card-filter]");
    if (pill) {
      const cont = pill.closest("[data-cards]");
      if (cont) { filterKind.set(keyOf(cont), pill.dataset.cardFilter || ""); rerender(pill); }
      return;
    }
    const fb = ev.target.closest("[data-fb]");
    if (fb) {
      const wrap = fb.closest("[data-fb-card]");
      const current = findCard(wrap, wrap.dataset.fbCard)?.feedback?.value || "";
      sendFeedback(wrap, current === fb.dataset.fb ? "" : fb.dataset.fb);
      return;
    }
    const fbs = ev.target.closest("[data-fb-send]");
    if (fbs) {
      const wrap = fbs.closest("[data-fb-card]");
      sendFeedback(wrap, findCard(wrap, wrap.dataset.fbCard)?.feedback?.value || "");
      return;
    }
    const fus = ev.target.closest("[data-fu-send]");
    if (fus) { sendFollowUp(fus.closest("[data-fu-card]")); return; }
    const del = ev.target.closest("[data-card-del]");
    if (del) {
      // Two taps: the card is gone for good, and deleting the newest card of a
      // kind also drops the memory the next run of that kind builds on.
      armOrFire(del, "card-del:" + del.dataset.cardDel,
                () => deleteCard(del.dataset.cardDel, del));
      return;
    }
    const log = ev.target.closest("[data-job-log]");
    if (log) { toggleLog(log.dataset.jobLog, log); return; }
    const cancel = ev.target.closest("[data-job-cancel]");
    if (cancel) {
      const id = cancel.dataset.jobCancel;
      armOrFire(cancel, "job-cancel:" + id, () => cancelJob(id, cancel));
    }
  });

  document.addEventListener("keydown", ev => {
    if (ev.key !== "Enter") return;
    if (ev.target.matches?.("[data-fu-text]")) {
      ev.preventDefault(); sendFollowUp(ev.target.closest("[data-fu-card]"));
    } else if (ev.target.matches?.("[data-fb-text]")) {
      ev.preventDefault();
      const wrap = ev.target.closest("[data-fb-card]");
      sendFeedback(wrap, findCard(wrap, wrap.dataset.fbCard)?.feedback?.value || "");
    }
  });
}

/* The only exit from a poisoned card.

   `cleanup_cards()` pins the NEWEST card of each kind forever ("it is the dedup
   anchor and the memory of the next run") and `previous_card_block()` feeds it
   back into every later card of that kind. So a card steered by an injected
   workout label keeps steering until it is deleted. The server route and the
   README both existed; the button did not, and the only remedy was to find
   `~/.runcoach/cards/` in a file manager. */
async function deleteCard(id, btn) {
  if (!id) return;
  try {
    await apiPost(`/api/cards/${encodeURIComponent(id)}/delete`, {});
  } catch (e) {
    toast("Could not delete the card: " + (e.message || e), { warn: true });
    return;
  }
  const card = btn?.closest("[data-card-id]");
  if (card) card.remove();
  toast("Card deleted.");
  context.onJobStart?.();              // pull fresh state: the list has changed
}

/* Optimistic: the card carries the feedback at once, the server confirms. If
   it fails, the old state comes back and the error is visible. */
async function sendFeedback(wrap, value) {
  if (!wrap) return;
  const id = wrap.dataset.fbCard;
  const card = findCard(wrap, id);
  if (!card) return;
  const text = (wrap.querySelector("[data-fb-text]")?.value || "").trim().slice(0, 500);
  const before = card.feedback;
  card.feedback = (value || text) ? { value, text, ts: new Date().toISOString() } : null;
  rerender(wrap);
  try {
    const r = await apiPost("/api/feedback", { card_id: id, value, text });
    // The server may echo the stored object; if it does not, the optimistic
    // one stands.
    if (r && "feedback" in r) card.feedback = r.feedback || null;
  } catch (e) {
    card.feedback = before;
    if (e.message !== "403") toast("Feedback not saved: " + (e.message || "server gone"), { warn: true });
  }
  const fresh = document.querySelector(`[data-fb-card="${CSS.escape(id)}"]`);
  rerender(fresh || wrap);
}

async function sendFollowUp(wrap) {
  if (!wrap || wrap.dataset.sending) return;
  const id = wrap.dataset.fuCard;
  const inp = wrap.querySelector("[data-fu-text]");
  const prompt = (inp?.value || "").trim();
  if (!prompt) { inp?.focus(); return; }
  wrap.dataset.sending = "1";
  wrap.querySelectorAll("button, input").forEach(b => (b.disabled = true));
  try {
    // `from_job` wants the JOB behind the card; a card without its own
    // `job_id` was written under the job's id.
    const fromJob = String(findCard(wrap, id)?.job_id || id);
    const r = await apiPost("/api/spawn", { from_job: fromJob, prompt });
    followUps.set(id, { job_id: String(r?.job?.id || ""), ts: Date.now() });
    toast("Follow-up is running — the answer arrives as a card below this one.", { ms: 6000 });
    const cont = rerender(wrap);
    (optsOf(cont || wrap).onJobStart || context.onJobStart)?.(r);
  } catch (e) {
    delete wrap.dataset.sending;
    wrap.querySelectorAll("button, input").forEach(b => (b.disabled = false));
    if (e.message === "403") return;
    if (e.status === 409 && handle409(e)) return;
    toast("Follow-up not started: " + (e.message || "server gone"), { warn: true, ms: 6000 });
  }
}

/* ── Job strip ───────────────────────────────────────────────────────────── */

/** Active jobs (with their interim `note`, e.g. "waiting for quota", an
 *  expectation from the average duration, cancel with arm-and-confirm) and the
 *  failures of the last 24 h with their reason. `opts`: finished (also show
 *  completed runs), cancel (false = no cancel button), onChange (after a
 *  cancel), title. */
export function mountJobs(container, jobs, opts = {}) {
  install();
  const el = elOf(container);
  if (!el) return;
  el.dataset.jobs = "1";
  jobsState.set(el, { jobs, opts });
  resetArm();                         // a rebuild = confirmation starts over
  const list = jobsForStrip(jobs, { finished: !!opts.finished });
  if (!list.length) { el.innerHTML = ""; return; }
  const active = list.filter(isActive).length;
  const avg = avgDurationSec(jobs);
  el.innerHTML = `<div class="card job-strip">
    <div class="subhead">${esc(opts.title || "Jobs")}${active ? ` · ${active} active` : ""}</div>
    ${list.map(j => renderJobRow(j, avg, opts)).join("")}
  </div>`;
  // An open log of a job that is still running goes stale with every poll —
  // pull it again, quietly, and patch the <pre> in place.
  for (const j of list) if (isActive(j) && jobLogs.has(j.id)) loadLog(j.id).catch(() => {});
}

function renderJobRow(j, avg, opts) {
  const active = isActive(j);
  const when = active
    ? (j.started ? "for " + fmtAgo(j.started).replace(/ ago$/, "") : "waiting")
    : fmtAgo(j.finished || j.started || j.created);
  const expectation = active && avg ? ` · expect ${fmtExpectation(avg)}` : "";
  // Active jobs carry their interim message in `note` ("waiting for quota"),
  // failed ones their reason in `result_summary`. For finished jobs that field
  // holds the result — the card shows that, not the strip.
  const reasonText = active ? (j.note || j.result_summary)
                   : j.status !== "done" ? (j.result_summary || j.note) : "";
  const reason = reasonText
    ? `<div class="job-reason${active ? "" : " job-reason-error"}">${esc(truncate(reasonText, 240))}</div>`
    : "";
  const lines = jobLogs.get(j.id);
  return `<div class="job-line${active ? " job-active" : ""}" data-job-id="${esc(j.id)}">
    <div class="job-row-head">
      <span class="pill ${JOB_PILL[j.status] || "pill-unknown"}">${
        j.status === "running" ? '<span class="live-dot"></span>' : ""}${esc(JOB_LABEL[j.status] || j.status)}</span>
      <span class="job-title">${esc(j.title || j.id)}</span>
      <span class="note job-when">${esc(when)}${esc(expectation)}</span>
      <button type="button" class="job-log-btn" data-job-log="${esc(j.id)}"
              aria-expanded="${!!lines}">Log</button>
      ${active && opts.cancel !== false
        ? `<button type="button" class="job-cancel" data-job-cancel="${esc(j.id)}"
                   data-arm-label="Really cancel?" aria-label="Cancel run">✕ Cancel</button>` : ""}
    </div>
    ${reason}
    ${lines ? `<pre class="job-log mono" data-job-log-out="${esc(j.id)}">${esc(logText(lines))}</pre>` : ""}
  </div>`;
}

const logText = lines => (lines.length ? lines.slice(-200).join("\n") : "(log is empty)");

async function loadLog(id) {
  const r = await apiGet(`/api/jobs/${encodeURIComponent(id)}/log`);
  const lines = (Array.isArray(r?.lines) ? r.lines : []).map(String);
  if (!jobLogs.has(id)) return;                    // closed in the meantime
  jobLogs.set(id, lines);
  const out = document.querySelector(`[data-job-log-out="${CSS.escape(id)}"]`);
  if (out) out.textContent = logText(lines);       // textContent: log lines are foreign text
}

async function toggleLog(id, btn) {
  const cont = btn.closest("[data-jobs]");
  const z = cont && jobsState.get(cont);
  const redraw = () => { if (z) mountJobs(cont, z.jobs, z.opts); };
  if (jobLogs.has(id)) { jobLogs.delete(id); redraw(); return; }
  jobLogs.set(id, ["loading…"]);
  redraw();
  try { await loadLog(id); }
  catch (e) {
    jobLogs.delete(id); redraw();
    if (e.message !== "403") toast("Log not readable: " + (e.message || "server gone"), { warn: true });
  }
}

async function cancelJob(id, btn) {
  const cont = btn.closest("[data-jobs]");
  const opts = (cont && jobsState.get(cont)?.opts) || {};
  btn.disabled = true;
  try {
    await apiPost(`/api/jobs/${encodeURIComponent(id)}/cancel`, {});
    toast("Cancel requested — the run is being stopped.");
    await opts.onChange?.();
  } catch (e) {
    btn.disabled = false;
    if (e.message !== "403") toast("Cancel refused: " + (e.message || "server gone"), { warn: true });
  }
}

/* ── Poller ──────────────────────────────────────────────────────────────── */

/** ONE poller. Asks ONLY the slim /api/jobs (not the whole state) while a run
 *  is active, stops itself in the background and when idle; on the last
 *  transition active→finished it runs `onFinished` (the page then loads the
 *  full state with the new card). `jobs()` returns the current list,
 *  `setJobs(list)` takes the fresh one and renders what depends on it. A
 *  network drop-out does not matter (phone: Wi-Fi → mobile data), only a 403
 *  ends the poll. */
export function jobPoller({ jobs, setJobs, onFinished, intervalMs = 8000 }) {
  let timer = null;
  const stop = () => { if (timer) clearInterval(timer); timer = null; };
  async function tick() {
    if (document.hidden || !(jobs() || []).some(isActive)) { stop(); return; }
    let all;
    try { all = await apiGet("/api/jobs"); }
    catch (e) { if (e.status === 403) stop(); return; }
    const list = Array.isArray(all) ? all : all?.jobs || [];
    setJobs(list);
    if (!list.some(isActive)) { stop(); await onFinished?.(); }
  }
  return {
    start() {
      if (timer || !(jobs() || []).some(isActive)) return;
      timer = setInterval(() => tick().catch(() => {}), intervalMs);
    },
    stop,
    running: () => timer !== null,
  };
}

/* ── Spawn with the dedup answer ─────────────────────────────────────────── */

/** Translate the server's 409 into an action. Returns true if it was dealt
 *  with here. `recompute` repeats the request with force:true — the toast
 *  action IS the confirmation, no second arming. */
export function handle409(e, recompute) {
  const k = classify409(e);
  if (k.type === "running") {
    toast(`Already running${k.ref ? ` (${k.ref})` : ""} — the card arrives as soon as the run is done.`,
          { warn: true, ms: 6000 });
    return true;
  }
  if (k.type === "current") {
    toast(String(e?.message || "Already computed on the current data."),
          recompute ? { ms: 12000, action: "Recompute", onAction: recompute } : { ms: 8000 });
    return true;
  }
  if (k.type === "queue_full") {
    toast(String(e?.message || "The queue is full — wait for a run to finish."),
          { warn: true, ms: 6000 });
    return true;
  }
  return false;
}
