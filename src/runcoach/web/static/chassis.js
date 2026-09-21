/* ═══════════════════════════════════════════════════════════════════════════
   Page frame.

   Everything here is content-NEUTRAL: view switching, reloading, loading
   state, error screens, data age. It knows nothing about running.

   The contract between the server's snapshot and the page is exactly four
   fields — whoever delivers them gets the freshness display, the schema check
   and the error screens for free:
       schema · generated_at · data_through · stale_days

   The job/card machinery (paid agent runs, coach cards, job poller) lives in
   `cards.js`, not here: the chassis is the frame, cards.js renders content.
   The page mounts `mountCards`/`mountJobs`/`jobPoller` itself and hooks the
   poller into `onAfterLoad`/`onVisible`.

   Imports are relative so that Node tests can load this module as well.
   ═══════════════════════════════════════════════════════════════════════════ */

import { esc, fmtAgo, fmtDay, toast, mountLogin, showLogin, apiGet,
         progressStart, progressEnd, progressActive, hasInput } from "./ui.js";
import { mountThemeToggle } from "./theme.js";


/* ── Views (tabs) ─────────────────────────────────────────────────────── */

/** Hash routing over a fixed list of views. Returns `showView`.
 *  Expects `<section id="view-<name>">` per view and buttons with
 *  `data-view="<name>"` inside the `tabs` container.
 *
 *  `onView(name)` runs after EVERY view change — including the one through
 *  the tab bar. `showView` writes the hash with `replaceState`, and that does
 *  NOT fire `hashchange`; without the hook a page has no chance to follow. */
export function mountViews({ tabs = "#tabs", views, initial, onView } = {}) {
  const bar = document.querySelector(tabs);

  function showView(name) {
    document.querySelectorAll(".view").forEach(v =>
      v.classList.toggle("active", v.id === "view-" + name));
    bar?.querySelectorAll("button[data-view]").forEach(b => {
      // aria-current rather than aria-selected: aria-selected belongs to
      // role="tab", and an app's bottom navigation is not a tab list in the
      // ARIA sense.
      if (b.dataset.view === name) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    // Compare the view part only: a page may carry a sub-target behind the
    // name (`#runs/<id>`) — the tab bar must not write that away.
    if (hashView() !== name) history.replaceState(null, "", "#" + name);
    onView?.(name);
  }
  const hashView = () => location.hash.slice(1).split("/")[0];

  bar?.addEventListener("click", ev => {
    const b = ev.target.closest("button[data-view]");
    if (b) showView(b.dataset.view);
  });
  addEventListener("hashchange", () => {
    const h = hashView();
    if (views.includes(h)) showView(h);
  });

  const start = hashView();
  showView(views.includes(start) ? start : (initial || views[0]));
  return showView;
}

/* ── Reloading ────────────────────────────────────────────────────────── */

/** Tap on the freshness display AND pull from the top. Without a handle the
 *  only way to reload would be "close the app and open it again". Returns
 *  `reload`. */
export function mountRefresh({ onRefresh, btn = "#refresh", pull = "#pull" } = {}) {
  const button = document.querySelector(btn);
  const ind = document.querySelector(pull);

  async function reload() {
    button?.classList.add("loading");
    // The strip runs from the first moment — the spinning arrow alone is too
    // little for a 30–120 s sync. A custom onRefresh may refine the text on
    // the way (`progressInfo`) and reports the end itself if it returns `true`.
    progressStart();
    try {
      // If the handler reports itself (returns `true`), do NOT report again:
      // a generic success message would overwrite every error branch the
      // handler has just set — one element, last call wins.
      // The plain loader returns the payload OBJECT (truthy) — so ONLY the
      // explicit signal `true` counts, otherwise the strip would stay active
      // forever, clock counting, claiming a sync was still running.
      const selfReported = (await onRefresh()) === true;
      // Safety net against "I report myself" followed by forgetting to: whoever
      // reported has ended the strip; if it is STILL active afterwards, the
      // handler forgot — end it generically instead of claiming "Refreshing…"
      // forever.
      if (!selfReported || progressActive()) progressEnd("Updated.");
    } catch (e) {
      if (e.message === "403") progressEnd("Not signed in.", { warn: true });
      else {
        progressEnd("Loading failed: " + e.message, { warn: true });
        toast("Loading failed: " + e.message, { warn: true });
      }
    } finally {
      button?.classList.remove("loading");
    }
  }

  button?.addEventListener("click", () => reload());

  let startY = 0, pulling = false;
  addEventListener("touchstart", ev => {
    pulling = scrollY <= 0 && ev.touches.length === 1;
    startY = pulling ? ev.touches[0].clientY : 0;
  }, { passive: true });
  addEventListener("touchmove", ev => {
    if (!pulling || !ind) return;
    const dist = ev.touches[0].clientY - startY;
    ind.style.height = dist > 0 ? Math.min(dist / 2, 56) + "px" : "0";
    ind.textContent = dist > 110 ? "release to refresh" : dist > 20 ? "pull…" : "";
  }, { passive: true });
  addEventListener("touchend", () => {
    if (pulling && ind && parseFloat(ind.style.height || "0") >= 55) reload();
    if (ind) { ind.style.height = "0"; ind.textContent = ""; }
    pulling = false;
  }, { passive: true });

  return reload;
}

/* ── Data age ─────────────────────────────────────────────────────────── */

/** Shows how old the data is. `staleText(d)` supplies the page's own
 *  sentence. */
export function renderFreshness(d, { staleText, empty, fresh = "#fresh", dot = "#fresh-dot",
                                     stale = "#stale" } = {}) {
  const el = document.querySelector(fresh);
  const point = document.querySelector(dot);
  // `generated_at` is the age of the SNAPSHOT, not of the data: on a fresh
  // install with nothing synced it read "1 s ago" next to a green dot, which
  // says "fresh" about a database that has never held a day. No data, no age.
  //
  // `empty` comes from the app, so the header and the page agree on what
  // "nothing here" means. This module had its own second definition for one
  // review round, and the two disagreed exactly when a store held activities
  // but no `daily_metrics` row: header "no data yet", Runs tab full.
  // No fallback definition. There WAS one, and it disagreed with the page's
  // exactly when a store held activities but no `daily_metrics` row. A caller
  // that forgets `empty` gets "no opinion", not a second opinion.
  const noData = empty ? !!empty(d) : false;
  if (el) el.textContent = noData ? "no data yet" : d?.generated_at ? fmtAgo(d.generated_at) : "–";
  // The threshold comes from the SNAPSHOT (`snapshot.STALE_AFTER_DAYS`), not
  // from a literal here: this banner tells the athlete to run `runcoach
  // doctor`, and doctor used to answer `[ok]` at the very age that raised it.
  const old = (d?.stale_days ?? 0) >= (d?.stale_after_days ?? 3);
  if (point) point.style.background = noData ? "var(--muted)" : old ? "var(--warn)" : "var(--ok)";
  const banner = document.querySelector(stale);
  if (!banner) return;
  banner.innerHTML = old
    ? `<div class="stale-banner">${esc(staleText
        ? staleText(d)
        : `Data up to ${fmtDay(d.data_through)} — the sync is ${d.stale_days} days behind.`)}</div>`
    : "";
}

/* ── Loading and error screens ────────────────────────────────────────── */

/** An empty white page is no answer to "still loading". `specs` are
 *  `{sel, kind: "title"|"grid", n}` — the skeleton shows the future shape so
 *  that nothing jumps when the data arrives. */
export function renderSkeleton(specs) {
  for (const { sel, kind = "title", n = 6 } of specs) {
    const el = document.querySelector(sel);
    if (!el) continue;
    el.innerHTML = kind === "grid"
      ? `<div class="grid2">${Array.from({ length: n }, () =>
          `<div class="cell"><span class="skel skel-line" style="width:50%"></span>
            <span class="skel skel-line" style="height:18px;width:70%"></span></div>`).join("")}</div>`
      : `<div class="skel skel-title"></div>
         <div class="skel skel-line" style="width:80%"></div>
         <div class="skel skel-line" style="width:60%"></div>`;
  }
}

/** Error screen in the head container; all other containers are emptied —
 *  otherwise the old state would still stand next to the error message. */
export function renderError({ head, clear = [], word = "No data",
                              text = "", hint = "" }) {
  const el = document.querySelector(head);
  if (el) {
    el.innerHTML =
      `<div class="verdict-head"><span class="verdict-word s-unknown">${esc(word)}</span></div>
       <div class="note">${esc(text)}</div>
       ${hint ? `<div class="thin-data">${esc(hint)}</div>` : ""}`;
  }
  for (const sel of clear) {
    const c = document.querySelector(sel);
    if (c) c.innerHTML = "";
  }
}

/* ── Response check ───────────────────────────────────────────────────── */

/** Throws if the response is unusable. Two cases, both of which have
 *  happened:
 *  - A torn transfer yields a 200 with an unreadable body; `apiGet` turns that
 *    into `{}`. Without a check the app discards the good state and actively
 *    claims the opposite ("no data") — complete with a green freshness dot
 *    and an "Updated." message.
 *  - The server renames its fields: the page would show "–" everywhere
 *    instead of saying that it expects a different format. */
export function checkPayload(p, { schema } = {}) {
  if (!p || typeof p !== "object" || (p.schema == null && !p.generated_at)) {
    throw new Error("incomplete response from the server");
  }
  const s = p.schema;
  if (schema != null && s != null && s !== schema) {
    const err = new Error(`The snapshot has format ${s}, the app expects ${schema}.`);
    err.schemaMismatch = true;
    throw err;
  }
  return p;
}

/* ── Orchestrator ─────────────────────────────────────────────────────── */

/** Starts the page: login, theme, tabs, reload handle, skeleton, first load,
 *  error screens, return from the background.
 *
 *  `render(payload)` is the only content-specific part. `onAfterLoad` is
 *  optional for follow-up logic (the job poll).
 *  Returns `{ showView, reload, fetchState, payload }`.
 *
 *  `marker` is written to `<html data-area>` — proof for a smoke test that
 *  this module actually ran (a SyntaxError in the page module leaves it
 *  unset). */
export function mountApp({
  marker = "runcoach", api, schema, views, initial, render,
  head = "#verdict", clear = [], skeleton = [], staleText, empty,
  onAfterLoad, onVisible, onRefresh, onView,
}) {
  document.documentElement.dataset.area = marker;
  mountThemeToggle();   // light/dark/auto — the choice lives in localStorage (theme.js)
  mountLogin(api);

  let payload = null;
  const showView = mountViews({ views, initial, onView });

  async function fetchState() {
    payload = checkPayload(await apiGet(api), { schema });
    renderFreshness(payload, { staleText, empty });
    render(payload);
    return payload;
  }

  // `onRefresh(fetchState)` lets the page turn ↻/pull into more than a re-read
  // (first the real Garmin sync, then the snapshot). Visibility changes and
  // the first load deliberately stay with the cheap fetchState().
  const reload = mountRefresh({ onRefresh: onRefresh ? () => onRefresh(fetchState) : fetchState });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden || !payload) return;
    if (hasInput()) return;
    // Restart the page's follow-up logic as well: it stops itself in the
    // background, and without this a running indicator would stick after
    // "lock the screen, come back five minutes later".
    // Do NOT swallow a failed reload: the page would show old numbers while
    // the freshness display claims "2 min ago". The login case has its own
    // overlay.
    fetchState().then(() => onVisible?.(payload)).catch(e => {
      if (e?.message === "403" || e?.status === 403) return;
      toast("Could not refresh — showing the last state. ↻ at the top right.",
            { warn: true });
    });
  });

  (async function start() {
    renderSkeleton(skeleton);
    try {
      await fetchState();
      onAfterLoad?.(payload);
    } catch (e) {
      if (e.message === "403") return;             // the login overlay is already up
      if (e.schemaMismatch) {
        renderError({ head, clear, word: "Different format", text: e.message,
                      hint: "Reload the page — if it still does not fit, the "
                          + "page is older than the server." });
        return;
      }
      if (e.status === 404) {
        renderError({ head, clear, text: "No snapshot yet.",
                      hint: "A sync creates it — run `runcoach sync` once." });
        return;
      }
      // An error WHILE RENDERING is not a server problem. Mixing the two up
      // sends the search in the wrong direction ("is the server running?"
      // while the server cleanly answered 200 and a variable was missing).
      const inCode = e instanceof ReferenceError || e instanceof TypeError;
      renderError({ head, clear,
                    word: inCode ? "Page error" : "No data",
                    text: (inCode ? "While building the page: " : "The server does not answer: ")
                          + (e.message || "unknown"),
                    hint: inCode
                      ? "This is a bug in the app, not in the data — open the console."
                      : "Is `runcoach serve` running? Otherwise pull down from the top to retry." });
    }
  })();

  return { showView, reload, fetchState, payload: () => payload, showLogin };
}
