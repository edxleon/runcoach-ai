/* ═══════════════════════════════════════════════════════════════════════════
   Shared UI helpers: token flow, escaping, formatting, sparkline, toast,
   progress strip, arm-and-confirm, login overlay, fetch wrappers.

   Token flow:
   `?token=` is only the BOOTSTRAP — it moves into localStorage at once and is
   removed from the URL (otherwise the password sits in the browser history);
   after that every request carries it as a header, never in the query
   (otherwise it ends up in the server log).
   ═══════════════════════════════════════════════════════════════════════════ */

export const TOKEN_KEY = "runcoach_token";
export const TOKEN_HEADER = "X-Runcoach-Token";

/** One locale for every number and date on the page: 24 h, day before month,
 *  decimal point. */
export const LOCALE = "en-GB";

export const TOKEN = (() => {
  try {
    const params = new URLSearchParams(location.search);
    const q = params.get("token");
    if (q) {
      localStorage.setItem(TOKEN_KEY, q);
      // Remove ONLY the token, not the whole query string: other parameters of
      // a login link have to survive the bootstrap.
      params.delete("token");
      const rest = params.toString();
      history.replaceState(null, "", location.pathname + (rest ? "?" + rest : "") + location.hash);
    }
    return localStorage.getItem(TOKEN_KEY);
  } catch (e) { return null; }        // privacy mode / Node: carry on without a token
})();

export function apiHeaders(extra) {
  return TOKEN ? { ...extra, [TOKEN_HEADER]: TOKEN } : (extra || {});
}

export function forgetToken() {
  try { localStorage.removeItem(TOKEN_KEY); } catch (e) { /* does not matter */ }
}

/** Everything that comes from data or from model output goes through this —
 *  without exception. Coach cards are model text, activity names come from
 *  Garmin: both are foreign content inside an HTML page. */
export function esc(t) {
  return String(t ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ── Formatting ───────────────────────────────────────────────────────── */

export function fmtAgo(iso) {
  if (!iso) return "–";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (isNaN(s)) return "–";
  if (s < 60) return `${Math.max(1, Math.round(s))} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(LOCALE, { day: "numeric", month: "short" });
}

/** Seconds → "1:23:45" or "23:45". For a duration, not a time of day. */
export function fmtDur(s) {
  if (s == null || isNaN(s)) return "–";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = n => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

/** Seconds → "7 h 34 min" (sleep reads like that, not as 7:34:00). */
export function fmtHours(s) {
  if (s == null || isNaN(s)) return "–";
  // Round to whole minutes FIRST, then split. Rounding the remainder on its own
  // printed "6 h 60 min" for 25170 s — reachable from the sleep card, whose
  // 14-night mean is an arbitrary second count.
  const total = Math.round(s / 60);
  const h = Math.floor(total / 60), m = total % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

/** Seconds per km → "5:12" (no unit). ONE source for this format: several
 *  local copies of the same rounding, applied to the same field, will drift
 *  apart sooner or later. */
export function fmtMinSec(sPerKm) {
  if (!sPerKm || isNaN(sPerKm)) return "–";
  // Round to whole seconds FIRST, then split: rounding the remainder on its own
  // turns 359.8 s/km into "5:60" instead of "6:00", and paces carry a decimal.
  const total = Math.round(sPerKm);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Seconds per km → "5:12 /km". */
export function fmtPace(sPerKm) {
  const v = fmtMinSec(sPerKm);
  return v === "–" ? v : `${v} /km`;
}

export function fmtKm(m, digits = 1) {
  if (m == null || isNaN(m)) return "–";
  return (m / 1000).toFixed(digits) + " km";
}

export function fmtNum(v, digits = 0) {
  if (v == null || isNaN(v)) return "–";
  return Number(v).toLocaleString(LOCALE, { minimumFractionDigits: digits,
                                            maximumFractionDigits: digits });
}

/** "+0.8" / "−0.3" / "±0" — here the sign is the information. */
export function fmtDelta(v, digits = 1) {
  if (v == null || isNaN(v)) return "–";
  if (v === 0) return "±0";
  // Magnitude via fmtNum: one formatting grammar per page, not two.
  return (v > 0 ? "+" : "−") + fmtNum(Math.abs(v), digits);
}

/** Number with the right noun: `plural(1, "run", "runs")` → "1 run".
 *  Replaces the bracket form "1 run(s)", which is simply wrong in the singular
 *  and reads like a placeholder somebody forgot. Zero takes the plural. */
export function plural(n, one, many) {
  const z = Number(n);
  const count = Number.isFinite(z) ? fmtNum(z) : "–";
  return `${count} ${Math.abs(z) === 1 ? one : many}`;
}

/** "Mon, 7 Sep" — weekday, day, month. */
export function fmtDay(iso) {
  if (!iso) return "–";
  const d = new Date(iso + "T12:00:00");
  // Something unparsable does NOT return the raw value: this function ends up
  // in innerHTML in many places, and a foreign string passed through would be
  // an escape bypass there. A broken date is an error case anyway — "–" is the
  // more honest display.
  if (isNaN(d)) return "–";
  return d.toLocaleDateString(LOCALE, { weekday: "short", day: "numeric", month: "short" });
}

/** "7 Sep" — the same day without the weekday (ranges, tight labels). */
export function fmtDayShort(iso) {
  if (!iso) return "–";
  const d = new Date(iso + "T12:00:00");
  if (isNaN(d)) return "–";
  return d.toLocaleDateString(LOCALE, { day: "numeric", month: "short" });
}

/* ── Sparkline ────────────────────────────────────────────────────────── */

/** Tiny trend as inline SVG. `values` may contain gaps (null) — they are NOT
 *  bridged, they break the line: a solid line across a measurement gap claims
 *  data that does not exist.
 *  The last point is marked (now is the point of reference).
 *  Inherits its colour from the parent element (`currentColor`).
 *
 *  `minSpan` is the smallest value range that is drawn. Without it every
 *  series fills the full height — a resting heart rate that moves between 46
 *  and 48 would look as dramatic as one between 40 and 70. Callers that know
 *  the natural spread of their quantity pass it; without it, auto-zoom. */
export function sparkline(values, { w = 100, h = 22, label = "", minSpan = 0 } = {}) {
  const pts = (values || []).map((v, i) => [i, v]);
  const known = pts.filter(p => p[1] != null && !isNaN(p[1]));
  if (known.length < 2) return "";
  const rawLo = Math.min(...known.map(p => p[1]));
  const rawHi = Math.max(...known.map(p => p[1]));
  const mid = (rawLo + rawHi) / 2;
  const span = Math.max(rawHi - rawLo, minSpan) || 1;
  const lo = Math.min(rawLo, mid - span / 2);
  const hi = Math.max(rawHi, mid + span / 2);
  const n = Math.max(pts.length - 1, 1);
  const x = i => (i / n) * w;
  const y = v => h - 2 - ((v - lo) / (hi - lo)) * (h - 4);

  let d = "", pen = false;
  for (const [i, v] of pts) {
    if (v == null || isNaN(v)) { pen = false; continue; }
    d += `${pen ? "L" : "M"} ${x(i).toFixed(1)} ${y(v).toFixed(1)} `;
    pen = true;
  }
  // The end point is a vertical tick, not a circle: the sparkline is stretched
  // horizontally (preserveAspectRatio="none") and a circle would be distorted
  // into a dash. A tick stays a tick — and `vector-effect` keeps the stroke
  // width constant.
  const last = known[known.length - 1];
  const lx = x(last[0]).toFixed(1), ly = y(last[1]).toFixed(1);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"
       role="img" aria-label="${esc(label)}">
    <path d="${d.trim()}" vector-effect="non-scaling-stroke"/>
    <path class="spark-end" d="M ${lx} ${Number(ly) - 2.5} L ${lx} ${Number(ly) + 2.5}"
          vector-effect="non-scaling-stroke"/></svg>`;
}

/* ── Metric cell ──────────────────────────────────────────────────────── */

/** One metric with caption, sub-line and optional trend.
 *  `series` are raw values for the sparkline, `minSpan` their smallest value
 *  range (see `sparkline`). The value ALWAYS goes through `esc()` — callers
 *  must not pass finished HTML. */
export function cell(k, v, x, cls, series, label, minSpan) {
  const sp = series ? sparkline(series.slice(-28), { label: label || k, minSpan }) : "";
  return `<div class="cell"><span class="k">${esc(k)}</span>
    <span class="v ${esc(cls || "")}">${esc(v)}</span>
    <span class="x">${esc(x || "")}</span>
    ${sp ? `<span class="sparkwrap ${esc(cls || "")}">${sp}</span>` : ""}</div>`;
}

/* ── Toast ────────────────────────────────────────────────────────────── */

let toastTimer = null;

/** Builds the toast element itself on first use — no markup to duplicate. */
export function toast(msg, opts = {}) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.innerHTML = '<span id="toast-msg"></span><button id="toast-action" hidden></button>';
    document.body.appendChild(el);
  }
  el.querySelector("#toast-msg").textContent = msg;
  el.classList.toggle("warn", !!opts.warn);
  const btn = el.querySelector("#toast-action");
  if (opts.action) {
    btn.hidden = false; btn.textContent = opts.action;
    btn.onclick = () => { hideToast(); opts.onAction?.(); };
  } else { btn.hidden = true; btn.onclick = null; }
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, opts.ms || (opts.action ? 6000 : 3500));
}
export function hideToast() { document.getElementById("toast")?.classList.remove("show"); }


/* ── Progress strip ───────────────────────────────────────────────────── */

/** Shows permanently THAT a refresh is running — and what came of it. The
 *  toast is not enough for that: it is gone after 3.5 s, while a Garmin sync
 *  takes 30–120 s. Whoever looks away in between would not know whether
 *  anything happened at all.
 *
 *  Builds its element itself, like `toast`. */
let progressTimer = null, progressEndTimer = null, progressBegin = 0;

function progressEl() {
  let el = document.getElementById("progress");
  if (!el) {
    el = document.createElement("div");
    el.id = "progress";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.innerHTML = '<span id="progress-dot"></span><span id="progress-text"></span>'
                 + '<span id="progress-time" class="mono"></span>';
    // NOT `header?.after(el) || prepend(el)`: `after()` returns undefined, so
    // `prepend` would ALWAYS run as well and push the strip ABOVE the sticky
    // header — it would scroll away instead of staying put.
    const header = document.querySelector("header.app");
    if (header) header.after(el);
    else document.body.prepend(el);
  }
  return el;
}

/** A run begins. The strip appears and counts the seconds. */
export function progressStart(text = "Refreshing…") {
  progressHide();                  // clear the end state of the previous run
  const el = progressEl();
  clearTimeout(progressEndTimer); clearInterval(progressTimer);
  progressBegin = Date.now();
  el.className = "active";
  el.querySelector("#progress-text").textContent = text;
  const time = el.querySelector("#progress-time");
  const tick = () => {
    const s = Math.floor((Date.now() - progressBegin) / 1000);
    time.textContent = s < 1 ? "" : s < 60 ? `${s}s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };
  tick();
  progressTimer = setInterval(tick, 1000);
}

/** Interim status without ending the run (the clock keeps going). */
export function progressInfo(text) {
  if (!document.getElementById("progress")?.classList.contains("active")) return;
  progressEl().querySelector("#progress-text").textContent = text;
}

/** The run is finished. `warn` leaves the strip standing (an error should be
 *  seen), success fades out after `ms`. */
export function progressEnd(text, { warn = false, ms = 12000 } = {}) {
  const el = progressEl();
  clearInterval(progressTimer); progressTimer = null;
  const took = progressBegin ? Math.round((Date.now() - progressBegin) / 1000) : 0;
  el.className = warn ? "error" : "done";
  el.querySelector("#progress-text").textContent = text;
  el.querySelector("#progress-time").textContent = took ? `${took}s` : "";
  clearTimeout(progressEndTimer);
  if (!warn) progressEndTimer = setTimeout(() => el.classList.remove("done"), ms);
}

/** Is a strip currently running? (For the chassis safety net: a refresh
 *  handler that says "I report the end myself" and then forgets to would
 *  otherwise let the counter run forever — minutes on the clock for a sync
 *  that finished in twenty seconds.) */
export function progressActive() {
  return !!document.getElementById("progress")?.classList.contains("active");
}

/** Hide the strip at once. Called by `progressStart`, so that a new run does
 *  not visually stick to the end state of the previous one. */
export function progressHide() {
  clearInterval(progressTimer); clearTimeout(progressEndTimer); progressTimer = null;
  const el = document.getElementById("progress");
  if (el) el.className = "";
}

/* ── Arm-and-confirm ──────────────────────────────────────────────────── */

let armed = { id: null, timer: null, orig: null, btn: null };

/** A re-render resets the arming. Without this the armed state survived an
 *  innerHTML update, and the NEW button fired on the first tap — safe beats
 *  convenient. */
export function resetArm() {
  if (armed.timer) clearTimeout(armed.timer);
  // Reset the BUTTON as well, not only the state: `resetArm` is also called
  // from places that do NOT re-render the button — the job poller, for one,
  // only rebuilds its own list. The button then still looked armed ("Sure? Tap
  // again") but no longer was; every further tap merely armed it again and
  // never fired. A button that looks ready and does nothing is worse than one
  // that never looked that way.
  if (armed.btn && armed.orig != null && armed.btn.isConnected) {
    armed.btn.classList.remove("armed");
    armed.btn.innerHTML = armed.orig;
  }
  armed = { id: null, timer: null, orig: null, btn: null };
}

/** Two-tap confirmation. The button content is saved and restored through
 *  `innerHTML`, NOT through `textContent`: a button may contain markup, and
 *  `textContent` would flatten it on the first tap for good. The content
 *  always originates from DOM that the page produced itself; nothing foreign
 *  is inserted that was not there before. */
export function armOrFire(btn, key, fire) {
  if (armed.id === key) {
    clearTimeout(armed.timer);
    btn.classList.remove("armed");
    if (armed.orig != null) btn.innerHTML = armed.orig;
    armed = { id: null, timer: null, orig: null, btn: null };
    fire();
    return;
  }
  if (armed.timer) clearTimeout(armed.timer);
  document.querySelectorAll(".armed").forEach(b => b.classList.remove("armed"));
  btn.classList.add("armed");
  const orig = btn.innerHTML;
  btn.textContent = btn.dataset.armLabel || "Sure? Tap again";
  armed = { id: key, orig, btn, timer: setTimeout(() => {
    armed = { id: null, timer: null, orig: null, btn: null };
    btn.classList.remove("armed"); btn.innerHTML = orig;
  }, 4000) };
}

/* ── Login overlay ────────────────────────────────────────────────────── */

let loginProbeUrl = "/api/state";

/** `probeUrl` is the route the entered password is checked against. Without a
 *  configured token the server accepts everything, so this overlay only ever
 *  appears after a 403. */
export function mountLogin(probeUrl) {
  if (probeUrl) loginProbeUrl = probeUrl;
  if (document.getElementById("login-overlay")) return;
  const el = document.createElement("div");
  el.id = "login-overlay";
  el.innerHTML = `
    <form id="login-form" autocomplete="on">
      <h1>Sign in</h1>
      <input id="login-pw" type="password" inputmode="text" placeholder="Access token"
             autocomplete="current-password" aria-label="Access token">
      <div id="login-err" role="alert"></div>
      <button type="submit">Continue</button>
    </form>`;
  document.body.appendChild(el);
  el.querySelector("#login-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    const pw = el.querySelector("#login-pw").value.trim();
    if (!pw) return;
    try {
      const r = await fetch(loginProbeUrl, { cache: "no-store",
        headers: { [TOKEN_HEADER]: pw } });
      if (r.status === 403) { showLogin("Wrong token"); return; }
      if (!r.ok && r.status !== 404) { showLogin("Server says " + r.status); return; }
      try { localStorage.setItem(TOKEN_KEY, pw); } catch (e) { /* privacy mode */ }
      location.reload();
    } catch (e) { showLogin("Server not reachable"); }
  });
}

export function showLogin(err) {
  mountLogin();
  document.getElementById("login-overlay").classList.add("show");
  document.getElementById("login-err").textContent = err || "";
  document.getElementById("login-pw").focus();
}

/* ── Fetch with uniform 403 handling ──────────────────────────────────── */

/* Without a time limit `fetch` hangs for minutes on a phone (Wi-Fi → mobile
   data, sleeping host): the spinner sticks, every further tap starts a
   PARALLEL request, and a hanging POST keeps the paid buttons locked
   indefinitely — with no message and no way back except a restart. 15 s is
   generous for a LAN and short enough to stay usable. */
export const REQUEST_TIMEOUT_MS = 15000;

function withTimeout(extra, ms = REQUEST_TIMEOUT_MS) {
  return { signal: AbortSignal.timeout(ms), ...extra };
}

/** Shared tail of every request. Throws on errors; a 403 discards the stored
 *  sign-in and shows the login (the token may have changed on the server
 *  while the page was open — without this the page polls into 403 forever).
 *
 *  The error carries `status`, `code`, `ref` and the raw `data`: without
 *  `status` a caller cannot tell a 403 from a network failure, and `code` is
 *  what the 409 handling switches on — never the message text. */
async function finish(r) {
  if (r.status === 403) {
    forgetToken();
    showLogin("Please sign in");
    throw Object.assign(new Error("403"), { status: 403 });
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || ("HTTP " + r.status)),
                                 { status: r.status, code: data.code || null,
                                   ref: data.ref || null, data });
  return data;
}

/** A failed `fetch` rejects with a TypeError — the same class a rendering bug
 *  throws. Re-label it here, so that "server down" can never be mistaken for
 *  "bug in the page" further up. No `status` on it: the request may or may
 *  not have reached the server. */
async function request(url, init) {
  let r;
  try { r = await fetch(url, init); }
  catch (e) {
    throw Object.assign(new Error(e?.name === "TimeoutError" ? "timed out" : "network error"),
                        { network: true });
  }
  return finish(r);
}

export async function apiGet(url) {
  return request(url, withTimeout({ cache: "no-store", headers: apiHeaders() }));
}

/** `timeoutMs` overrides the 15 s default: writing routes may legitimately run
 *  longer on the server. If the client gives up first, the page reports a
 *  failure on top of a write that is just succeeding — and the user taps a
 *  second time. */
export async function apiPost(url, body, timeoutMs) {
  return request(url, withTimeout({ method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body) }, timeoutMs || REQUEST_TIMEOUT_MS));
}


/* ── Input guard ──────────────────────────────────────────────────────── */

// Lives in its own side-effect-free module (this one runs the token bootstrap
// on import). Relative, not "/static/…": this module is also imported directly
// by Node tests, and Node does not know the server root. In the browser both
// are the same place.
export { hasInput } from "./input-guard.js";
