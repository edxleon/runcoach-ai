# Architecture

## Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `garmin.py` | Garmin endpoints → dataclasses. Clamps every value, separates soft failures (→ NULL) from hard ones (auth / 429 / connection → propagate) | garminconnect |
| `models.py` | Dataclasses; column lists are the single source for the upserts | – |
| `store.py` | SQLite. Upserts, aggregate reads (MCP), series reads (UI), migrations via `PRAGMA user_version` | logic, models |
| `logic.py` | Pure functions: `readiness_verdict`, `decide_today`, `interval_facts`, `run_kind` | – |
| `sync.py` | One sync run: days → activities → detail backfill → side channels (threshold, predictions, calendar) | garmin, store |
| `snapshot.py` | Everything the UI shows, assembled once per request | store, logic |
| `tools.py` / `mcp_server.py` | Text handlers and their MCP registration | store, snapshot |
| `web/server.py` | stdlib HTTP server, auth, templates, dedup, refresh thread, worker thread | snapshot, web/jobs, web/agent |
| `web/jobs.py` | Jobs and cards as atomic JSON files | – |
| `web/agent.py` | Builds the prompt, runs the CLI under an allowlist, validates the card | web/jobs |
| `demo.py` | Deterministic synthetic athlete | store |
| `web/static/` | The frontend itself — ~3,900 lines of vanilla ES modules, CSS and one HTML file, no build step. **`app.js` is the page**: the only module `index.html` loads, and every card, chart and cell is rendered there. Beneath it: `chassis.js` (tabs, loading and error states, the refresh button), `cards.js` (coach cards and the job strip, including their polling), `ui.js` (formatting, escaping, `apiGet`/`apiPost`), `logic.js` (the pure arithmetic, tested in `web-tests/` and pinned against Python in `tests/test_js_python_contract.py`), `input-guard.js`, `theme.js` + `theme-boot.js` (the pre-paint one is a separate file because the CSP forbids inline script), `tokens.css` + `cards.css` | the JSON of `/api/state` |

## Data model

Four tables (`migrations/0001_init.sql`): `daily_metrics` (one row per local day), `activities`
(summary + separately written detail columns), `activity_splits`, `scheduled_workouts`.

Two invariants worth knowing:

1. **Profile values are not daily metrics.** Lactate threshold and race predictions arrive with every
   sync whether or not the watch was worn. They never create a day row (`has_any_metric` ignores them),
   the daily upsert writes them with `COALESCE` (a NULL from the daily fetch means "don't know", not
   "delete"), and the threshold lives on the row of the day it was *measured*.
2. **A workout's day is the local day**, materialised on write (`activities.local_day`). A 23:30 run
   stored in UTC must not slide into tomorrow and skew the 7/28-day load windows.

## Sync failure policy

| Failure | Effect |
|---|---|
| one endpoint of a day fails softly | the column is left AS IT WAS (never NULLed over), listed in `unknown`, counted as a soft error |
| one endpoint of a workout's detail fails softly | same — and if it was a ZONE endpoint (`DETAIL_ESSENTIAL`), the workout is not stamped as detailed, so the next sync retries it. Writing the stamp anyway deleted a run's HR zones permanently on one transient 500, while the sync printed "1 with detail, 0 errors" |
| the workout list endpoint answers with something unreadable | treated as a FAILURE (`None`), not as "no workouts this week" |
| Garmin stops us (429 / auth / connection) | `rep.fatal` — the sync aborts, exit code 2, and the web refresh reports it as an error rather than "Updated" |
| a whole day fails (after one retry) | day skipped, counted as error → exit code 3 |
| 429 / auth / connection during detail backfill | backfill aborts (no hammering), rest next run |
| a *run* returns completely empty detail | not marked as synced → retried next run |
| side channel fails (threshold, predictions, calendar) | reported as soft error, exit code unaffected |
| calendar month fetched incompletely | local mirror is left untouched |

## Coach job lifecycle

```
POST /api/spawn {template_id, ctx}
  └─ server renders the prompt from templates.json (ctx values regex-checked, replace() not format())
  └─ dedup: same template+ctx running → 409 already_running
            latest card newer than the last data write → 409 card_current (force = recompute)
  └─ job file written (status queued)
worker thread
  └─ agent.run(): temp cwd, mcp.json with one server, claude --print … (stdin = skills + frame + task
     + previous card of the same kind incl. user feedback, nonce-framed as DATA)
  └─ quota error → job stays running with note "waiting for quota", retries every 5 min
  └─ answer → last JSON object → contract check → stamped (model, cost, data version) → card file
UI polls /api/jobs, then re-reads /api/state
```

Untrusted text appears in three places — workout names, previous cards, user feedback — and is handled
the same way each time: capped, framed with a random nonce, declared as data, with the binding
instruction placed *before* the block so truncation can never promote data to instruction. Garmin free
text is additionally stripped of every Unicode control and format character, including the tag block
(U+E0000–E007F) and bidi overrides: those survive into a model's context while rendering as nothing at
all, which would defeat the two mitigations that depend on the athlete *seeing* the label — the
"(untrusted label)" marker and `coach.md`'s "mention that you ignored it".

**Where the isolation ends.** `--tools ""` withholds every built-in tool, `--strict-mcp-config` with a
one-server `--mcp-config` excludes every other MCP server, and the working directory is an empty temp
dir. What that does *not* exclude is the host's own Claude Code configuration: user-level `CLAUDE.md`
is still loaded, and user-level or plugin `SessionStart`/`SubagentStart` hooks still fire in every
`claude --print`. That is host-owned config rather than anything an injected workout title can reach,
so the claim "an injected label cannot reach anything but this app's own tools" holds — but a
compromised Claude Code plugin runs in every card job, and the sentence "empty temp directory" should
not be read as more isolation than that.

**A card is deletable, and that matters.** `cleanup_cards()` pins the newest card of each kind forever
(it is the dedup anchor and the memory of the next run) and `previous_card_block()` feeds it into every
later card of that kind. So a card that a poisoned label steered keeps steering until it is deleted —
which is why the delete button is part of the security design and not a convenience.

## Web security

- Loopback by default; non-loopback bind requires `RUNCOACH_TOKEN` (constant-time compare, global
  back-off on failures). Token bootstrap via `?token=` is moved to `localStorage` and stripped from the URL.
- **DNS rebinding**: in the token-less loopback case every request — GET and POST — must carry a
  `Host` of `127.0.0.1`, `localhost` or `::1`. A rebound `evil.example` resolves to loopback but
  still sends its own `Host`, so it is answered with 403 (`_host_ok`). With a token set the check
  steps aside; the token is then the authenticator.
- POSTs with a foreign `Origin` are refused (CSRF against the token-less localhost case).
- **Framing**: every response carries `X-Frame-Options: DENY` and a CSP with
  `frame-ancestors 'none'`, so the app cannot be embedded and clickjacked from a page the browser
  is already allowed to load.
- Static files: filename allowlist regex, no path components.
- All model-written content is rendered escaped; the server enforces structure and length only.

**One trust boundary this does not cover.** Everything under `~/.runcoach/` — cards, job files, the
database — is read back without any integrity check and flows straight into both the DOM and the
next agent prompt (a card is re-injected as context for the following card of its kind). Whoever can
write that directory can therefore write the UI and steer the coach. For a single-user local tool
that is the same privilege the user already has, and no boundary is crossed. It stops being true the
moment `RUNCOACH_HOME` points at a cloud-synced or shared folder, which is then a different threat
model than the one this design assumes.

## Testing

| Layer | What | Needs |
|---|---|---|
| `tests/` | logic, store (real tmp SQLite), garmin normalisation (fake clients), sync policy, snapshot, tools, web (stubbed CLI) | nothing |
| `tests/test_js_python_contract.py` | the quantities that exist in BOTH languages, pinned against each other: it RUNS the shipped `logic.js` through node and compares the result to the Python — `is_hard` over a 375-workout grid, the weekly intensity series, `weekStart`. `web-tests/` asserts JavaScript against JavaScript literals, so it goes red when someone edits `logic.js` and never when someone edits `logic.py` — which is how the intensity numerator drifted for one review round (13 min on one tab, 17 on the other) and the target line for the next (17 of 43 against 17 of 6). A first version of this file parsed the source instead of running it and was measurably too weak: it summed the field names out of the numerator, so a flipped operator passed | node |
| `tests/test_palette.py` | the colour claims in `tokens.css`, measured: a Viénot/Brettel deuteranopia simulation and ΔE76 over every adjacent pair, in all four palette blocks (`:root`, the `prefers-color-scheme` override and both `data-theme` blocks). The comments quoted three numbers nothing could re-check; now they are output | nothing |
| `tests/test_eval_fixture.py` | one eval case's input is generated from the real tools (`evals/fixture_gen.py`) and compared byte for byte, so the prompt shape a case measures cannot drift from the prompt that ships | pyyaml |
| `tests/test_invariants.py` | the promises that span two or more modules: a predicate that exists in three languages, one anchor date for every surface reporting a training load, a stamp that may only be written after what it promises, a word that has to mean the same thing on both tabs. Deliberately one file — splitting them by module would put each half of an invariant next to code that cannot break it alone | nothing |
| `tests/test_api_contract.py` | the one test that does *not* use a fake client: parses `garmin.py` with `ast` and asserts every `client.*` endpoint it calls still exists on the installed `garminconnect.Garmin`, with the arguments we pass. `_safe()` catches `AttributeError`, so a renamed endpoint is otherwise one log line and a permanently NULL column | garminconnect installed (no network, no credentials) |
| `tests/test_ui_smoke.py` | headless Chrome renders all four tabs against a throw-away demo server. Red on an uncaught console error, the visible "Page error" marker, or a module that never ran (`data-area` missing) — a SyntaxError in an inline module is otherwise just a blank page no unit test can see. Skipped when no Chrome is installed | Chrome/Chromium |
| `web-tests/` | pure frontend logic (`node --test`) | node |
| `evals/` | coach behaviour, LLM-as-judge | Claude CLI |
| `scripts/pii_gate.py` | release gate against personal data | nothing |
