/* ═══════════════════════════════════════════════════════════════════════════
   Theme switch: Auto (system setting) → Light → Dark → Auto.

   tokens.css knows three states: without `data-theme` it follows the system
   (prefers-color-scheme), `data-theme="light"|"dark"` forces one. This module
   only holds the user's choice: stored in localStorage, applied to the
   `<html>` element, cycled through a button in the header.

   Against the flash on load (dark system, stored "light"): the page carries a
   boot line in its <head> that sets the stored value BEFORE the first paint.
   This module applies it once more afterwards (meta colour, button state).
   ═══════════════════════════════════════════════════════════════════════════ */

export const THEME_KEY = "runcoach-theme";
export const THEMES = ["auto", "light", "dark"];
const LABEL = { auto: "Auto", light: "Light", dark: "Dark" };
const ICON = {
  auto: "M12 3a9 9 0 1 0 0 18V3z",                       // half circle
  light: "M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0-5v3m0 14v3M2 12h3m14 0h3M4.9 4.9l2.1 2.1m10 10 2.1 2.1M4.9 19.1l2.1-2.1m10-10 2.1-2.1",
  dark: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z",
};
/* Colour of the browser bar (meta theme-color): the value equals --bg in
   tokens.css; a theme change without updating it would leave the bar in the
   old tone. */
const META_BG = { dark: "#12151c", light: "#f4f5f8" };

/** Next state in the cycle — pure, so a Node test can check it. */
export function nextTheme(current) {
  const i = Math.max(0, THEMES.indexOf(current));   // anything unknown counts as "auto"
  return THEMES[(i + 1) % THEMES.length];
}

/** Stored choice; anything unknown is "auto". */
export function getTheme() {
  try { const t = localStorage.getItem(THEME_KEY); return THEMES.includes(t) ? t : "auto"; }
  catch { return "auto"; }
}

/** Which colour world is actually IN EFFECT (auto resolved via the system). */
export function effectiveTheme(t = getTheme()) {
  if (t !== "auto") return t;
  try { return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"; }
  catch { return "dark"; }
}

export function applyTheme(t = getTheme()) {
  const root = document.documentElement;
  if (t === "auto") delete root.dataset.theme; else root.dataset.theme = t;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", META_BG[effectiveTheme(t)]);
  document.querySelectorAll("[data-theme-toggle]").forEach(b => draw(b, t));
}

export function setTheme(t) {
  try { if (t === "auto") localStorage.removeItem(THEME_KEY); else localStorage.setItem(THEME_KEY, t); }
  catch { /* private mode: the choice holds until reload */ }
  applyTheme(t);
}

function draw(btn, t) {
  btn.dataset.themeToggle = t;
  btn.setAttribute("aria-label", `Colour scheme: ${LABEL[t]} — tap for ${LABEL[nextTheme(t)]}`);
  btn.title = `Colour scheme: ${LABEL[t]}`;
  btn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICON[t]}"/></svg><span>${LABEL[t]}</span>`;
}

/** Put the button into the header — before `#refresh`, otherwise at the end.
 *  Idempotent. */
export function mountThemeToggle(header = document.querySelector("header.app, header")) {
  if (!header || header.querySelector("[data-theme-toggle]")) return;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "theme-toggle";
  draw(btn, getTheme());
  btn.addEventListener("click", () => setTheme(nextTheme(getTheme())));
  const before = header.querySelector("#refresh, .head-right");
  header.insertBefore(btn, before);
  // Follow a system change while on "auto" (meta colour) — the button state
  // stays "Auto", only the browser bar changes with it.
  try { matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => applyTheme()); } catch { /* old browser */ }
}

// Apply immediately in the browser; under Node (tests) there is no document.
if (typeof document !== "undefined") applyTheme();
