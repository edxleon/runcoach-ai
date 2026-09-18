/* `hasInput` — the guard that decides whether a rebuild would throw away what
 * the person is typing. Two mistakes it has to keep out:
 *   1. A `readonly` field (text the page wrote itself) counted as "typing" and
 *      locked the page for good.
 *   2. Asking only the VISIBLE view while the rebuild replaces all views — the
 *      half-filled form in the neighbouring tab was silently gone. Rule: the
 *      root is whatever is about to be overwritten.
 * No jsdom needed: a minimal DOM stub, only what hasInput touches. */
import test from "node:test";
import assert from "node:assert/strict";

function field(kind, opts = {}) {
  const el = {
    tagName: kind.toUpperCase(),
    value: opts.value ?? "", defaultValue: opts.defaultValue ?? "",
    type: opts.type ?? (kind === "input" ? "text" : undefined),
    readOnly: !!opts.readOnly, disabled: !!opts.disabled,
    selectedIndex: opts.selectedIndex ?? 0,
    options: opts.options ?? [],
    _sending: !!opts.sending,
  };
  el.closest = sel => (sel === "[data-sending]" && el._sending ? {} : null);
  return el;
}
function root(fields) {
  return {
    querySelectorAll(sel) {
      if (sel.includes("select")) return fields.filter(f => f.tagName === "SELECT");
      return fields.filter(f =>
        (f.tagName === "INPUT" && f.type !== "date") || f.tagName === "TEXTAREA");
    },
  };
}

let main = root([]);
globalThis.document = { querySelector: sel => (sel === "main" ? main : null) };

const { hasInput } = await import("../src/runcoach/web/static/input-guard.js");

test("basic cases", () => {
  assert.equal(hasInput(root([])), false);
  assert.equal(hasInput(root([field("input")])), false, "an empty field does not count");
  assert.equal(hasInput(root([field("input", { value: "Hello" })])), true);
  assert.equal(hasInput(root([field("input", { value: "   " })])), false, "only spaces do not count");
  assert.equal(hasInput(root([field("input", { value: "x", defaultValue: "x" })])), false,
               "a prefilled, unchanged value does not count");
  assert.equal(hasInput(root([field("textarea", { value: "note" })])), true);
  assert.equal(hasInput(root([field("input", { type: "date", value: "2026-09-04" })])), false,
               "date fields are exempt (prefilled, not work in progress)");
});

test("the three exclusions", () => {
  assert.equal(hasInput(root([field("textarea", { value: "text", readOnly: true })])), false);
  assert.equal(hasInput(root([field("input", { value: "x", disabled: true })])), false);
  assert.equal(hasInput(root([field("input", { value: "x", sending: true })])), false,
               "a form that is being submitted is no longer open work");
});

test("selects", () => {
  const opt = (defaultSelected = false) => ({ defaultSelected });
  assert.equal(hasInput(root([field("select", { selectedIndex: 0, options: [opt(true), opt()] })])), false);
  assert.equal(hasInput(root([field("select", { selectedIndex: 1, options: [opt(true), opt()] })])), true);
  assert.equal(hasInput(root([field("select", { selectedIndex: 1, options: [opt(), opt(true)] })])), false,
               "a preselected option does not count");
  assert.equal(hasInput(root([field("select", { selectedIndex: 1, options: [opt(true), opt()],
                                                disabled: true })])), false);
});

test("the default root is `main`, not the visible view", () => {
  main = root([field("input", { value: "half a question" })]);
  assert.equal(hasInput(), true, "input in a neighbouring tab protects as well");
  main = root([]);
  assert.equal(hasInput(), false);
});
