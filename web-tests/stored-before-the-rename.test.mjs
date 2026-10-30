// A browser that used the app as runcoach-ai: its access token and its theme
// sit in localStorage under the keys of that version. Names the user never
// sees, so they stay - a renamed key would sign the phone out at the upgrade.
import test from "node:test";
import assert from "node:assert/strict";

const stored = new Map([["runcoach_token", "t0k3n-from-v0.4"], ["runcoach-theme", "dark"]]);
globalThis.localStorage = {
  getItem: k => (stored.has(k) ? stored.get(k) : null),
  setItem: (k, v) => stored.set(k, String(v)),
  removeItem: k => stored.delete(k),
};
globalThis.location = { search: "", pathname: "/", hash: "" };
globalThis.history = { replaceState() {} };

const ui = await import("../src/runspector/web/static/ui.js");
const theme = await import("../src/runspector/web/static/theme.js");

test("a phone signed in under runcoach-ai still sends its token after the upgrade", () => {
  assert.equal(ui.apiHeaders()[ui.TOKEN_HEADER], "t0k3n-from-v0.4");
});

test("and keeps its theme", () => {
  assert.equal(theme.getTheme(), "dark");
});
