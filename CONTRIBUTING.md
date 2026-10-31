# Contributing

Thanks for looking. Runspector decides a runner's training day, so a wrong rule is a wrong
session on someone's watch; the process below exists for that.

## Set up

```bash
uv sync
git config core.hooksPath .githooks     # optional: the checks below run before every push
```

`runspector serve --demo` runs the app on synthetic data, no Garmin account needed.

## Before every commit

```bash
uv run ruff check && uv run pytest -q && node --test "web-tests/*.test.mjs" && uv run python scripts/pii_gate.py \
  && npx --yes @biomejs/biome@2.5.15 lint --error-on-warnings
```

CI runs the same on three operating systems, plus a coverage floor and an install of the built
wheel. `CLAUDE.md` lists what else a change pulls along (a rule needs a scenario test that was red
first, a new tool changes the tool counts, a prompt change needs an eval run).

## Changes that need a deep review

The write path to Garmin (`plan.py` apply/undo, `garmin.py` write calls), the agent's fence
(`web/agent.py`, the MCP tool set), the workflows, a migration, and any change over ~1,000 lines
get a deep review before they are committed. With Claude Code it is the `/deep-review` skill
(`.claude/skills/deep-review/SKILL.md`); without it, the same steps by hand or with any tool:

1. Reviewers who did not write the change read the diff against the requirement it was written
   for, and against a short list of what counts: a wrong result, a security hole, lost data or a
   silently wrong number, a test that no longer tests, a migration that breaks.
2. Every important finding is proven by a test that fails without the fix, then fixed.
3. A second look at the fixes; a class of error that comes back is closed as a class.
4. A live run on throw-away data (`--demo` or a copy of a database), never on someone's real
   Garmin account without their yes.
5. A last reader who sees only the final diff names the most likely way it breaks.

Say in the pull request what the review found. No personal data in the tree - `scripts/pii_gate.py`
checks it, and `RUNSPECTOR_PII_EXTRA` adds your own private terms without committing them.
