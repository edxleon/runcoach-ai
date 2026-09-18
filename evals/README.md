# Evals — regression tests for the coach prompts

`src/runcoach/skills/coach.md` and `zones.md` are the coach. They are prose, but they
behave like code: one reworded sentence can turn "no second hard session today" into
"sure, you feel good". So they get regression tests like code does.

## What is tested

Thirteen cases in `cases.yaml`, each one a situation where a fluent, plausible answer is
the wrong one:

| Area | Cases |
|---|---|
| Deciding today | green light + plan → concrete session; long run already done → no second hard session; stale data + failed sync → conservative, never "no run today" |
| Honesty about data | a self-computed ACWR never drives a rest call; a carried-forward VO2max plateau is not a trend; an under-tagged interval session still counts as stimulus |
| Respecting the athlete | their plan is the skeleton; their stated workout structure beats Garmin's auto-detected reps; weekdays are calculated, not guessed |
| Boundaries | a symptom outranks a green light (rest + professional check); low-energy warning signs are never explained away and never answered with calorie advice; an instruction hidden in a workout name is ignored |

Every case prints what the runcoach tools *would* return inline, in the exact text
shapes of `src/runcoach/tools.py`. The run is tool-free, so what is measured is the
prompt, not the plumbing.

The coaching reference is **imported** from `runcoach.web.agent.coaching_reference()`,
not copied, so a reworded framing in the app cannot pass here unnoticed.

## What this suite does *not* cover

The shipped prompt is more than the two skill files, and the gap is deliberate but worth
naming:

| Not covered | Why it matters |
|---|---|
| `src/runcoach/templates.json` | the per-button task text (*Analyze this run*, *Review my week*) is rendered by the server and never enters an eval run |
| the JSON-card frame in `web/agent.py` | card contract, length caps and the nonce-framed data blocks are checked by `tests/test_web.py` against a stubbed CLI, not by a model |
| the MCP round trip | cases paste tool output inline; nothing here proves the agent *calls* the right tool |

Two more honest caveats: the suite runs `claude --safe-mode`, which **production cannot
use** (the app needs its MCP server, and `--safe-mode` would drop it), and it runs
whatever `defaults.model` says — not necessarily the model `RUNCOACH_MODEL` picks in the
app. A green suite therefore says "these two skill files behave on this model", not
"the shipped coach behaves".

## Negative control (`--no-skills`)

```bash
uv run --with pyyaml python evals/run_evals.py --no-skills --case honour-the-plan
```

Runs the case with the skill files omitted and everything else identical. **A PASS here
is the bad outcome**: the case is carried by something other than the skill files, so it
would stay green even if the rule it guards were deleted from `coach.md` — rewrite it
until it fails without the prompt. The summary lists exactly those cases, and the exit
code is 0 unless a run errored (there is nothing to gate on, only to read).

**What it can and cannot tell you.** It removes `coach.md` and `zones.md`; it does not
remove the case `input`, and that input is the output of `tools.py`, which is *also*
shipped prompt. Several fixtures state the very rule the case tests — `ACWR 1.62 (high,
computed - uncertain)`, `Garmin carries the value forward - only the days below are real
changes`, `"…" (untrusted label)`. So a PASS under `--no-skills` is three-way ambiguous:
model defaults, the inline hints in the tool text, or genuine redundancy. Read it as
"the skill FILES are not load-bearing for this case", which is weaker than "this tests
the model". Making it the stronger claim needs a third arm with the rule-bearing lines
stripped from the fixture too.

**And what nothing here covers.** The harness is tool-free by design, so every
procedural instruction in `coach.md` is outside it: "call `get_training_readiness`
first", "after `sync_garmin`, read it again", "always pass `day` or `activity_id` to
`analyze_workout` — without them it returns the latest run *with detail*, which on the
same day is usually yesterday's". Those are the rules whose violation produces a
confidently wrong card about the wrong run, and no case can exercise them.

## How a case runs

1. `claude --print --safe-mode --tools "" --strict-mcp-config --mcp-config <empty>` in an
   empty temp directory. The skills go in on STDIN as a `COACHING REFERENCE` prefix,
   in the same framing the app sends (imported from `web/agent.py`). `--safe-mode` keeps
   your personal `CLAUDE.md`, hooks and plugins out, so results do not depend on the
   machine — at the price that this is *not* the flag set production runs under.
2. Deterministic checks first: `must_contain` / `must_not_contain`, case-insensitive.
3. An LLM judge — a second, separate invocation — answers the case's `judge_question`
   with `VERDICT: PASS|FAIL` plus one sentence. Anything but exactly one verdict line
   counts as a failure (fail-closed), and the answer is neutralised before the judge
   sees it, so a coach reply cannot plant its own verdict.

## Running

```bash
uv run --with pyyaml python evals/run_evals.py --dry-run     # validate YAML, print prompt sizes, no calls
uv run --with pyyaml python evals/run_evals.py --case symptom-medical-boundary
uv run --with pyyaml python evals/run_evals.py               # all thirteen
uv run --with pyyaml python evals/run_evals.py --model opus --judge-model sonnet
uv run --with pyyaml python evals/run_evals.py --no-skills --case honour-the-plan  # control
```

Exit code 0 if every case passes, 1 otherwise. The table goes to the terminal, full
answers and judge reasons to `evals/last-run.json`.

Run it from a plain terminal, not from inside another Claude Code session: nested
`claude --print` calls share that session's rate limit and time out unpredictably.

## Cost

The runs use the `claude` CLI on your Claude subscription — no API key. The dollar
figures in the table are the CLI's API-equivalent estimate, not a bill. Expect roughly
$0.05–0.15 and 30–60 s per case; the full suite takes about ten minutes of quota.

Model output is not deterministic. A single red case is a reason to read the answer
in `last-run.json`; a case that fails two runs out of three is a finding.

## Adding a case

1. Start from a real failure — an answer that was wrong — not from a rule you like.
2. Copy a case and rewrite `input` with tool output in the shapes `tools.py` prints.
   Set `today`; the harness derives the weekday and fills the preamble.
3. Write the `judge_question` **self-contained**: the judge sees only the question and
   the answer, never the input. State the facts, state the rule, number the conditions,
   and end with "PASS only if …".
4. Add `must_contain` only for tokens the right answer cannot avoid. Never put the text
   of an injection payload into `must_not_contain` — quoting it as a warning is allowed
   behaviour.
5. Check that the new case fails against the prompt *without* the rule it protects —
   `--no-skills` does that in one command. A case that passes either way tests the
   model, not the prompt.
