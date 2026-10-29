// The "You" tab: what the coach has learned about the athlete from their own
// data (`facts.py`, computed at every load - nothing stored). Pure: it turns
// the snapshot's `facts` into HTML, so web-tests can render it without a page.
import { esc } from "./ui.js";

const HEAD = `<div class="subhead">What the coach has learned about you</div>`;

/** The facts as a list - value, the evidence it rests on, and since when it
 *  holds where it moved. Every field is model-free text from the server, but
 *  escaped like everything else on the page. */
export function youHtml(facts) {
  if (!facts || !facts.length) {
    return `${HEAD}<div class="empty-note">Nothing yet — after a few weeks of synced running
      the coach knows your days, your long run and how you train.</div>`;
  }
  const rows = facts.map(f => `<div class="fact">
      <div class="fact-k">${esc(f.label)}</div>
      <div class="fact-v">${esc(f.value)}</div>
      <div class="note">${esc(f.evidence)}${f.change
        ? ` · <span class="fact-change">${esc(f.change)}</span>` : ""}</div>
    </div>`).join("");
  return `${HEAD}
    <div class="note">Read from your own data every time the page loads — nothing is stored or
      guessed. The coach reads this before every answer.</div>
    <div class="facts">${rows}</div>
    <div class="note" style="margin-top:8px">Not right? Your profile wins over your history:
      <code>runcoach profile --days-per-week 4 --long-run-day sun</code>.</div>`;
}
