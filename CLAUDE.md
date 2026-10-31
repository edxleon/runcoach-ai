# runspector — how changes land

A public repo: everything here ships to strangers. `scripts/pii_gate.py` keeps personal data out;
real Garmin data only ever goes into a throw-away `RUNSPECTOR_HOME`, never into the tree.

## Before every commit

```bash
uv run ruff check && uv run pytest -q && node --test "web-tests/*.test.mjs" && uv run python scripts/pii_gate.py \
  && npx --yes @biomejs/biome@2.5.15 lint --error-on-warnings
```

The pre-push hook (`.githooks/pre-push`, opt-in: `git config core.hooksPath .githooks`) runs this
again without the node tests and blocks a push whose coach prompt changed without an eval run.
CI also holds a coverage floor (92 % branch, one leg); a drop under it is a missing test, not a
number to lower. A Biome version bump changes `ci.yml`, the hook, this line and `$schema` in
`biome.jsonc` together.

## When you change X, also do Y

| Change | Also |
|---|---|
| `src/runspector/skills/*.md` or `evals/cases.yaml` | full eval run (`uv run python evals/run_evals.py`), it rewrites `evals/RESULTS.md`; a new case also once with `--no-skills` - it must FAIL there; a flaky case: narrow the judge question first (`evals/README.md`) |
| the wording of a tool in `tools.py` | `uv run python evals/fixture_gen.py [case-id]` and paste into `cases.yaml`; `tests/test_eval_fixture.py` says which |
| `facts.py`, the athlete block in `web/agent.py`, `get_athlete_profile` | the same re-render (`open-question-biggest-lever-first` shows the profile); the athlete block stays before `TASK:` and outside `PROMPT_CAP` (`tests/test_web.py`) |
| a key in `profile.py` | `validate` and its test in `tests/test_profile.py`, the row in `you.js` (`WEEK_ROWS`), and the planner's reading of it in `habits.week` |
| a rule in `logic.py`, `planning.py`, `plan.py` or `habits.py` | a scenario test in `tests/test_what_comes_next.py`, red against the old code first; then `RUNSPECTOR_SIM_SEEDS=40 uv run pytest -q tests/test_simulation.py`; a week that moves without habits fails `tests/test_build_week_golden.py` - regenerate it only when that is the point. The weekly `mutation.yml` lists the mutants of these files no test kills: a survivor in the function you changed is the test you still owe |
| the number of tests or eval cases | README count and badge - pinned by `tests/test_invariants.py` |
| a Garmin endpoint | `tests/test_api_contract.py` |
| an MCP tool added or removed | the tool sets in `tests/test_tools.py` and `tests/test_web.py`, the README diagram, and `EXPECTED_TOOLS` in `scripts/install_check.py` (CI's install job - not run by the pre-push hook) |
| a migration | `tests/test_migrations.py`; columns in `models.py` |

## Deep review before the commit

The write path and the agent's fence get a deep review (`/deep-review`, the skill in
`.claude/skills/deep-review/`) before they are committed, not after: `plan.py` (apply, undo,
claims), `garmin.py` write calls, `web/agent.py` (allowlist, `--disallowedTools`), `mcp_server.py`
tool set, `.github/workflows/**`, a migration - and any change over ~1,000 lines. Fresh reviewers,
every important finding proven by a red test, a live run on throw-away data, a fresh reader last.
A write against a real Garmin account only after the maintainer's explicit yes, with
`undo_workout` as the way back.

## Release

1. Bump `pyproject.toml`, `src/runspector/__init__.py` and the `@vX.Y.Z` install pin in the README;
   `uv lock`.
2. Notes in `docs/releases/vX.Y.Z.md`.
3. `git tag vX.Y.Z && git push origin main vX.Y.Z` - `.github/workflows/release.yml` checks the
   versions agree, runs the suite, builds, attests and publishes. Push and release only on the maintainer's word.

## Style

Commit messages: one sentence on what changed for the user, no prefix. Comments say why, as the
surrounding code does.
