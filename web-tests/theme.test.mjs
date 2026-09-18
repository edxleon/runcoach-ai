// Theme switch (theme.js): the cycle Auto → Light → Dark → Auto and the
// constants that tokens.css and the page's boot line depend on.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { nextTheme, THEMES, THEME_KEY } from "../src/runcoach/web/static/theme.js";

test("three states in a fixed order", () => {
  assert.equal(THEMES.join(), "auto,light,dark");
});

test("the cycle closes", () => {
  assert.equal(nextTheme("auto"), "light");
  assert.equal(nextTheme("light"), "dark");
  assert.equal(nextTheme("dark"), "auto");
  assert.equal(nextTheme("nonsense"), "light", "anything unknown counts as auto");
  assert.equal(nextTheme(undefined), "light");
});

test("the pre-paint boot script reads the same storage key", () => {
  assert.equal(THEME_KEY, "runcoach-theme");
  const dir = new URL("../src/runcoach/web/static/", import.meta.url);
  const boot = readFileSync(new URL("theme-boot.js", dir), "utf8");
  assert.ok(boot.includes(`localStorage.getItem("${THEME_KEY}")`),
            "theme-boot.js applies the stored theme under the same key theme.js writes");
  // It has to be a file, not an inline <script>: the server sends
  // `script-src 'self'`, which blocks inline scripts — silently, so the white
  // flash would come back without anything failing.
  const html = readFileSync(new URL("index.html", dir), "utf8");
  assert.ok(html.includes('<script src="/static/theme-boot.js"></script>'),
            "index.html loads the boot script");
  assert.ok(!/<script>(?!\s*<\/script>)/.test(html), "no inline <script> survives the CSP");
});
