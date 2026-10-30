// The Garmin sign-in dialog (signin.js) and the setup step that opens it.
import test from "node:test";
import assert from "node:assert/strict";
import { signinHtml } from "../src/runspector/web/static/signin.js";
import { setupSteps, signInHere } from "../src/runspector/web/static/logic.js";

test("sign-in: offered on this computer only - the server refuses a password from elsewhere", () => {
  for (const h of ["127.0.0.1", "localhost", "LOCALHOST", "[::1]", "::1"]) assert.equal(signInHere(h), true, h);
  for (const h of ["192.168.1.20", "kubik.local", "", undefined, "127.0.0.1.evil.example"]) {
    assert.equal(signInHere(h), false, String(h));
  }
});

test("sign-in: the account step says what happens first, and the fields are the password manager's", () => {
  const html = signinHtml("account", ["Your e-mail and password go to Garmin only.", "<b>x</b>"]);
  assert.ok(html.indexOf("go to Garmin only") < html.indexOf('name="email"'), "the notice comes before the fields");
  assert.match(html, /&lt;b&gt;x&lt;\/b&gt;/, "the notice is escaped");
  assert.match(html, /type="email" name="email" autocomplete="username"/);
  assert.match(html, /type="password" name="password" autocomplete="current-password"/);
  assert.match(html, /data-signin-form="account"/);
  assert.doesNotMatch(html, /name="code"/);
});

test("sign-in: the code step asks for the one-time code only", () => {
  const html = signinHtml("code", ["ignored"]);
  assert.match(html, /name="code" inputmode="numeric" autocomplete="one-time-code"/);
  assert.match(html, /not kept/);
  assert.doesNotMatch(html, /name="password"/);
  assert.doesNotMatch(html, /ignored/);
});

test("setup: on this computer the Garmin step is a button, elsewhere the terminal command", () => {
  const s = { garmin_session: false, claude_status: "subscription", counts: {} };
  const here = setupSteps(s, { signInHere: true })[0].text;
  assert.match(here, /data-signin/);
  assert.match(here, /first sync starts right after/);
  assert.doesNotMatch(here, /runspector login/);
  const away = setupSteps(s)[0].text;
  assert.doesNotMatch(away, /data-signin/);
  assert.match(away, /runspector login/);
});
