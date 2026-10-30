/* ═══════════════════════════════════════════════════════════════════════════
   Input guard — deliberately its OWN module with no load-time side effects.

   `ui.js` runs the token bootstrap on import (reads `?token=`, writes
   localStorage, calls `history.replaceState`). Anything that only needs the
   guard — a Node test, for instance — should not have to buy that along with
   it. ui.js and chassis.js re-export from here.
   ═══════════════════════════════════════════════════════════════════════════ */

/** "The person is in the middle of typing" — a text field with content of its
 *  own, or a select with a choice that was actually made.
 *
 *  Half-filled forms must survive an app switch: on a phone, "briefly away"
 *  while typing is the normal case (copying something from another app, the
 *  screen lock) — a re-render via innerHTML would discard the input without a
 *  word.
 *
 *  RULE: the root is whatever is about to be overwritten — not whatever is
 *  currently visible. A page-wide rebuild also replaces the cards of inactive
 *  views (`.view{display:none}` only hides them, they are still in the DOM),
 *  so it has to ask `main`. Asking only the active view silently destroys a
 *  half-filled form in the neighbouring tab. For targeted partial renderers,
 *  pass the affected container instead.
 *
 *  Only REAL input fields count. `readonly` carries text that the page wrote
 *  itself and would lock the page permanently; a form that is currently being
 *  submitted (`data-sending`) is no longer open work either. */
export function hasInput(root) {
  const r = root || document.querySelector("main");
  if (!r) return false;
  const counts = el => !el.readOnly && !el.disabled && !el.closest("[data-sending]");
  return [...r.querySelectorAll("input:not([type=date]), textarea")]
      .some(el => counts(el) && el.value.trim() !== "" && el.defaultValue !== el.value)
    || [...r.querySelectorAll("select")].some(el =>
         counts(el) && el.selectedIndex > 0 && el.options[el.selectedIndex]?.defaultSelected === false);
}
