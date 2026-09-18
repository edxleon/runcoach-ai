/* Regression check for the progress strip (ui.js) and the chassis' safety net.
 *
 * The bug it pins: a refresh handler returns `true` ("I report the end
 * myself") and then never calls `progressEnd`. The counter kept running and
 * claimed a sync in progress long after it had finished. `mountRefresh` in
 * chassis.js closes the strip anyway.
 *
 * The safety-net test drives the REAL `mountRefresh`. It used to copy the
 * condition (`!selfReported || progressActive()`) into the test body, where
 * deleting the net from chassis.js left the test green — it tested its own
 * copy. No jsdom needed: a stub document just big enough for the strip and
 * for theme.js, which applies a theme at import time. */
import test from "node:test";
import assert from "node:assert/strict";

const el = {
  id: "progress", className: "", _k: {},
  get classList() {
    return {
      contains: c => el.className.split(" ").includes(c),
      add: c => { el.className = (el.className + " " + c).trim(); },
      remove: c => { el.className = el.className.split(" ").filter(x => x !== c).join(" "); },
    };
  },
  setAttribute() {}, innerHTML: "",
  querySelector(sel) { return (this._k[sel] ||= { textContent: "" }); },
};
globalThis.document = {
  getElementById: id => (id === "progress" ? el : null),
  createElement: () => el,
  // Only the strip's anchor resolves; "#refresh" and "#pull" stay null, which
  // is the state of a page without those controls — `mountRefresh` is written
  // to survive it.
  querySelector: sel => (sel === "header.app" ? { after() {} } : null),
  querySelectorAll: () => [],
  documentElement: { dataset: {} },
  body: { prepend() {} },
};
globalThis.addEventListener = () => {};

const ui = await import("../src/runcoach/web/static/ui.js");
const { mountRefresh } = await import("../src/runcoach/web/static/chassis.js");

const stripText = () => el.querySelector("#progress-text").textContent;

test("start and end", () => {
  ui.progressStart();
  assert.ok(ui.progressActive(), "progressStart makes the strip active");
  ui.progressEnd("Updated.");
  assert.ok(!ui.progressActive(), "progressEnd ends it");
  assert.equal(el.className, "done");
});

test("the chassis safety net closes a strip the handler forgot", async () => {
  // Exactly the broken handler: claims to report the end, never does.
  const reload = mountRefresh({ onRefresh: async () => true });
  await reload();
  assert.ok(!ui.progressActive(), "the net in mountRefresh ended the strip");
  assert.equal(stripText(), "Updated.", "and reported it generically");
});

test("a handler that DID report keeps its own end text", async () => {
  const reload = mountRefresh({
    onRefresh: async () => { ui.progressEnd("Already up to date — 2 sessions"); return true; },
  });
  await reload();
  assert.ok(!ui.progressActive());
  assert.equal(stripText(), "Already up to date — 2 sessions",
               "a blanket 'Updated.' would overwrite the handler's own result line");
});

test("a handler that does NOT self-report is ended by the chassis", async () => {
  const reload = mountRefresh({ onRefresh: async () => ({ payload: "truthy but not true" }) });
  await reload();
  assert.ok(!ui.progressActive());
  assert.equal(stripText(), "Updated.");
});

/* The error branch of `mountRefresh` is NOT driven from here: it raises a
   toast, and a toast arms a 3.5-second timer that would hold the test process
   open. What that branch does to the strip is the same `progressEnd(…, {warn})`
   the last test pins directly. */

test("an error end stays visible but is no longer active", () => {
  ui.progressStart();
  ui.progressEnd("Sync error", { warn: true });
  assert.ok(!ui.progressActive());
  assert.equal(el.className, "error");
  ui.progressHide();                               // clears timers so the test process can exit
  assert.equal(el.className, "");
});
