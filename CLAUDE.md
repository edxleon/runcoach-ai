# runcoach-ai — how changes land

A public repo: everything here ships to strangers. `scripts/pii_gate.py` keeps personal data out;
real Garmin data only ever goes into a throw-away `RUNCOACH_HOME`, never into the tree.

## Before every commit

```bash
uv run ruff check && uv run pytest -q && node --test "web-tests/*.test.mjs" && uv run python scripts/pii_gate.py
```

The pre-push hook (`.githooks/pre-push`, opt-in: `git config core.hooksPath .githooks`) runs the
Python half of this again and blocks a push whose coach prompt changed without an eval run.

## When you change X, also do Y

| Change | Also |
|---|---|
| `src/runcoach/skills/*.md` or `evals/cases.yaml` | full eval run (`uv run --with pyyaml python evals/run_evals.py`), it rewrites `evals/RESULTS.md`; a new case also once with `--no-skills` - it must FAIL there; a flaky case: narrow the judge question first (`evals/README.md`) |
| the wording of a tool in `tools.py` | `uv run python evals/fixture_gen.py [case-id]` and paste into `cases.yaml`; `tests/test_eval_fixture.py` says which |
| a rule in `logic.py`, `planning.py` or `plan.py` | a scenario test in `tests/test_what_comes_next.py`, red against the old code first; then `RUNCOACH_SIM_SEEDS=40 uv run pytest -q tests/test_simulation.py` |
| the number of tests or eval cases | README count and badge - pinned by `tests/test_invariants.py` |
| a Garmin endpoint | `tests/test_api_contract.py` |
| a migration | `tests/test_migrations.py`; columns in `models.py` |

## Deep review before the commit

The write path and the agent's fence get a deep review (`/iter` from personal-ai) before they are
committed, not after: `plan.py` (apply, undo, claims), `garmin.py` write calls, `web/agent.py`
(allowlist, `--disallowedTools`), `mcp_server.py` tool set, `.github/workflows/**`. A write against
a real Garmin account only after the maintainer's explicit yes, with `undo_workout` as the way back.

## Release

1. Bump `pyproject.toml`, `src/runcoach/__init__.py` and the `@vX.Y.Z` install pin in the README;
   `uv lock`.
2. Notes in `docs/releases/vX.Y.Z.md`.
3. `git tag vX.Y.Z && git push origin main vX.Y.Z` - `.github/workflows/release.yml` checks the
   versions agree, runs the suite, builds, attests and publishes. Push and release only on the maintainer's word.

## Style

Commit messages: one sentence on what changed for the user, no prefix. Comments say why, as the
surrounding code does.
