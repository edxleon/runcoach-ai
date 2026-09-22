// Pure logic of the coach cards (static/cards.js). cards.js imports ui.js; its
// token bootstrap catches the missing `location` itself. `renderCard` needs NO
// DOM (a pure string function) and is therefore tested too — the escaping
// invariant from the module header would otherwise rest on reading alone.
import test from "node:test";
import assert from "node:assert/strict";
import {
  truncate, modelShort, metaParts, avgDurationSec, fmtExpectation, groupCards,
  classify409, todayCard, splitBullets, textPlain, jobsForStrip, isActive, renderCard,
} from "../src/runcoach/web/static/cards.js";

test("truncate", () => {
  assert.equal(truncate("short", 10), "short");
  const k = truncate("This is a long sentence with many words in it", 20);
  assert.ok(k.endsWith("…") && k.length <= 20, k);
  assert.ok(!/\s…$/.test(k), "no space before the ellipsis");
  assert.equal(truncate(null, 5), "", "null becomes empty, not 'null'");
});

test("modelShort", () => {
  assert.equal(modelShort("claude-opus-5"), "opus-5");
  assert.equal(modelShort("claude-sonnet-5-20260901"), "sonnet-5", "date suffix is dropped");
  assert.equal(modelShort(""), "");
  assert.equal(modelShort(undefined), "");
});

test("metaParts", () => {
  const fmt = { day: d => "D:" + d, ago: a => "A:" + a, num: (n, dig) => n.toFixed(dig) };
  let m = metaParts({ data_through: "2026-09-06", generated_at: "2026-09-06T07:45:01+02:00",
                      model: "claude-opus-5", cost_usd: 1.7 }, fmt);
  assert.equal(m[0], "data up to D:2026-09-06", "meta starts with the data status, not with '3 h ago'");
  assert.equal(m[1], "generated A:2026-09-06T07:45:01+02:00");
  // No price on the card: `cost_usd` is in the input above and must NOT appear.
  assert.deepEqual(m.slice(2), ["opus-5"]);
  m = metaParts({ day: "2026-08-13", written: "2026-08-13T08:16:23" }, fmt);
  assert.equal(m.join("|"), "D:2026-08-13|generated A:2026-08-13T08:16:23",
               "card without stamps: day/written as fallback, no model");
  assert.equal(metaParts({ cost_usd: null, model: "" }, fmt).length, 0, "nothing invented for an empty card");
});

test("avgDurationSec / fmtExpectation", () => {
  const J = (status, started, finished) => ({ id: "j", status, started, finished });
  const jobs = [
    J("done", "2026-09-06T07:45:00+02:00", "2026-09-06T07:47:00+02:00"),   // 120 s
    J("done", "2026-09-06T08:00:00+02:00", "2026-09-06T08:04:00+02:00"),   // 240 s
    J("failed", "2026-09-06T09:00:00+02:00", "2026-09-06T09:30:00+02:00"), // does not count
    J("running", "2026-09-06T10:00:00+02:00", null),
    J("done", null, "2026-09-06T11:00:00+02:00"),                          // no `started`
  ];
  assert.equal(avgDurationSec(jobs), 180, "average only over finished runs with both stamps");
  assert.equal(avgDurationSec([]), null);
  assert.equal(avgDurationSec(null), null);
  assert.equal(fmtExpectation(180), "~3 min");
  assert.equal(fmtExpectation(44), "~40 s", "seconds rounded to tens");
  assert.equal(fmtExpectation(3), "~10 s", "never below 10 s");
  assert.equal(fmtExpectation(null), "");
});

test("groupCards", () => {
  const C = (id, extra = {}) => ({ id, job_id: id, kind: "train-today", ...extra });
  const cards = [C("j-3", { parent: "j-1" }), C("j-2", { parent: "j-1" }), C("j-1"),
                 C("j-0", { parent: "j-gone" })];          // orphan: parent not in the list
  let g = groupCards(cards);
  assert.equal(g.main.map(c => c.id).join(), "j-1,j-0", "main list: parent + orphan, source order");
  assert.equal(g.children.get("j-1").map(c => c.id).join(), "j-2,j-3", "children oldest first");
  assert.ok(!g.children.has("j-gone"), "unknown parents create no entry");
  g = groupCards([C("j-5", { parent: "j-5" })]);
  assert.equal(g.main.length, 1, "a self reference lands in the main list");
  assert.equal(groupCards([]).main.length, 0);
  assert.equal(groupCards(null).main.length, 0);
});

test("classify409 switches on the code, never on the message", () => {
  let k = classify409({ code: "already_running", ref: "j-20260906-074501-a44a", message: "whatever" });
  assert.deepEqual(k, { type: "running", ref: "j-20260906-074501-a44a" });
  k = classify409({ code: "card_current", ref: "c-1" });
  assert.deepEqual(k, { type: "current", ref: "c-1" });
  assert.equal(classify409({ code: "queue_full" }).type, "queue_full");
  // The thrown error carries the raw body in `data` as well.
  assert.equal(classify409({ data: { code: "card_current", ref: "c-2" } }).ref, "c-2");
  assert.equal(classify409({ message: "already running (j-1)" }).type, "other",
               "message text alone decides nothing");
  assert.deepEqual(classify409(undefined), { type: "other", ref: null });
});

test("todayCard", () => {
  const H = (id, day, gen, extra = {}) => ({ id, job_id: id, kind: "train-today", day,
                                             generated_at: gen, ...extra });
  const cards = [
    H("j-b", "2026-09-06", "2026-09-06T07:45:01+02:00"),
    H("j-c", "2026-09-06", "2026-09-06T09:00:00+02:00", { parent: "j-b" }),   // an answer does not count
    H("j-a", "2026-09-05", "2026-09-05T07:45:01+02:00"),
    { id: "j-x", job_id: "j-x", kind: "week-review", day: "2026-09-06" },
  ];
  let h = todayCard(cards, "2026-09-06");
  assert.ok(h && h.card.id === "j-b" && h.current === true);
  h = todayCard(cards, "2026-09-07");
  assert.ok(h && h.card.id === "j-b" && h.current === false, "next day: same card, but not current");
  assert.equal(todayCard([cards[3]], "2026-09-06"), null, "other kind → null");
  assert.equal(todayCard([], "2026-09-06"), null);
  h = todayCard([H("j-old", "2026-09-06", "2026-09-06T06:00:00+02:00"),
                 H("j-new", "2026-09-06", "2026-09-06T12:00:00+02:00")], "2026-09-06");
  assert.equal(h.card.id, "j-new", "same day: the later generated_at wins");
});

test("splitBullets", () => {
  const bl = ["a", "b", "c", "d", "e", "f", "g", 7, ""];
  let t = splitBullets(bl);
  assert.equal(t.visible.length, 2);
  assert.equal(t.rest.join(""), "cdefg", "non-strings and empties are dropped");
  t = splitBullets(bl, 5);
  assert.equal(t.rest.join(""), "fg");
  assert.equal(splitBullets(["a", "b"]).rest.length, 0);
  assert.equal(splitBullets(null).visible.length, 0);
});

test("textPlain", () => {
  assert.equal(textPlain("# Briefing\r\n\r\n**Sessions:** 3 today\n\n\n\n- point"),
               "Briefing\n\nSessions: 3 today\n\n- point");
  assert.equal(textPlain("<b>x</b>"), "<b>x</b>", "textPlain does NOT escape — esc() does that on render");
  assert.equal(textPlain(null), "");
  assert.equal(textPlain("*Briefing* · Wednesday"), "Briefing · Wednesday", "single-asterisk bold goes");
  assert.equal(textPlain("_34 items in total_"), "34 items in total", "underscore italics go");
  assert.equal(textPlain(["```", "Unclear: X  -182.75", "```"].join("\n")), "Unclear: X  -182.75");
  // The more important group: what must NOT be touched.
  assert.equal(textPlain("3 * 4 = 12"), "3 * 4 = 12", "a single asterisk stays (multiplication)");
  assert.equal(textPlain("snake_case_name stays"), "snake_case_name stays");
});

test("jobsForStrip / isActive", () => {
  const now = new Date("2026-09-06T12:00:00+02:00").getTime();
  const jl = [
    { id: "r", status: "running", started: "2026-09-06T11:58:00+02:00" },
    { id: "q", status: "queued", created: "2026-09-06T11:59:00+02:00" },
    { id: "f-new", status: "failed", finished: "2026-09-06T03:10:00+02:00" },   // 9 h old
    { id: "f-old", status: "failed", finished: "2026-09-04T03:10:00+02:00" },   // >24 h
    { id: "c", status: "cancelled", finished: "2026-09-05T13:00:00+02:00" },    // 23 h old
    { id: "d-new", status: "done", finished: "2026-09-06T08:00:00+02:00" },
    { id: "d-old", status: "done", finished: "2026-09-01T08:00:00+02:00" },
    { status: "running" },                                                       // no id
  ];
  assert.equal(jobsForStrip(jl, { now }).map(j => j.id).join(), "r,q,f-new,c");
  assert.equal(jobsForStrip(jl, { now, finished: true }).map(j => j.id).join(), "r,q,f-new,c,d-new");
  assert.equal(jobsForStrip(jl, { now, max: 2 }).length, 2);
  assert.ok(isActive({ status: "queued" }) && !isActive({ status: "done" }) && !isActive(null));
});

test("renderCard escapes everything that comes from a model, a user or Garmin", () => {
  const EVIL = "<img src=x onerror=alert(1)>";
  const card = {
    id: "c-1", job_id: "j-1", kind: "train-today", day: "2026-09-07",
    generated_at: "2026-09-07T07:46:00+02:00", model: "claude-opus-5",
    headline: EVIL, verdict: EVIL, bullets: [EVIL, "second " + EVIL],
    delta: EVIL, ref: EVIL,
    feedback: { value: "good", text: EVIL, ts: "2026-09-07T08:00:00+02:00" },
  };
  const openHtml = renderCard(card, { open: true });
  assert.ok(typeof openHtml === "string" && openHtml.length > 0, "renderCard works without a DOM");
  assert.ok(!openHtml.includes("<img src=x"));
  // NOT a check for "onerror=alert": after escaping that legitimately stands in
  // the card as PLAIN TEXT. The invariant is that no `<` of the payload
  // arrives as markup.
  const known = /<(?:\/?)(?:article|button|span|h3|div|p|details|summary|input)[^>]*>/gi;
  assert.ok(!/<img|<script|<\/?[a-z]+ [a-z-]*=/i.test(openHtml.replace(known, "")), "no foreign markup");
  assert.ok(openHtml.includes("&lt;img"), "the text is there, only escaped");
  assert.ok(openHtml.includes('aria-pressed="true"'), "feedback value 'good' marks its button");

  const closedHtml = renderCard(card, { open: false });
  assert.ok(!closedHtml.includes("<img src=x") && closedHtml.includes("&lt;img"));
  const withChild = renderCard({ ...card, feedback: null },
                               { open: true, children: [{ ...card, id: "c-2", parent: "c-1" }] });
  assert.ok(!withChild.includes("<img src=x"), "child cards escape as well");
  const withRef = renderCard(card, { open: false, refText: () => EVIL });
  assert.ok(!withRef.includes("<img src=x"), "a refText result is escaped");
});

test("renderProposal: the preview is escaped, the button is the only write, and it says why it is off", async () => {
  const { renderProposal } = await import("../src/runcoach/web/static/cards.js");
  const p = { id: "p-20260922-101010-abcd", day: "2026-09-24", status: "open",
              preview: "VO2max 4x4 min <img src=x onerror=alert(1)>\n  warmup  2.6 km  no target",
              warnings: [] };
  const html = renderProposal(p, {});
  assert.ok(html.includes("&lt;img"), "the preview is escaped, not rendered");
  assert.ok(html.includes('data-apply="p-20260922-101010-abcd"'), "the apply button carries the id");
  assert.ok(!html.includes("disabled"), "with a session the button is live");

  const off = renderProposal(p, { canApply: false, applyHint: "demo data - no Garmin account" });
  assert.ok(off.includes("disabled") && off.includes("demo data"), "off, and it says why");

  const done = renderProposal({ ...p, status: "applied", workout_id: 900001 }, {});
  assert.ok(!done.includes("data-apply") && done.includes("On Garmin"), "applied: no button, a pill");
  assert.equal(renderProposal(null, {}), "");
});
