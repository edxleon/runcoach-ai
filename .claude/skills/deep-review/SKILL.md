---
name: deep-review
description: Deep review of a change before it is committed - for the write path to Garmin, the agent's fence, the MCP tool set, the workflows, a migration, or any change over ~1,000 lines. Fresh reviewers, every important finding proven by a red test, a second round on the fixes, a live run on throw-away data, a fresh reader last.
---

# Deep review

CI proves the code does what its tests say. This review looks for what no test says yet. It runs
before the commit, on the uncommitted diff (`git diff HEAD`), or on the last commit
(`git show HEAD`) when the diff is empty.

## When

| Change | Review |
|---|---|
| `plan.py` (apply, undo, claims), `garmin.py` write calls, `web/agent.py`, `mcp_server.py` tool set, `.github/workflows/**`, `src/runspector/migrations/*.sql`, or over ~1,000 lines | **full**: steps 1-5 |
| a rule (`logic.py`, `planning.py`, `plan.py`, `habits.py`) or a prompt (`skills/*.md`) | **light**: steps 1-4, one reviewer |
| anything else | the checks in `CLAUDE.md` are enough |

## What counts

Only what makes the change wrong for a runner:

- a correctness bug: a concrete input or state that gives a wrong result or a crash - above all a
  wrong call for the day or a wrong week;
- a security hole: injection (Garmin's free text and the athlete's own text reach prompts and the
  page), a secret in a log, argv or URL, a tool an unattended card run must not reach, a write to
  Garmin without the athlete's yes;
- data loss, or a silently wrong number (training load, HRV, a pace, minutes of a week);
- a test that no longer tests: an assertion deleted or weakened, a new skip, a mock that hides the
  change;
- a migration that breaks an applied schema or deletes data.

Every finding names `file:line` and a concrete failure scenario. Style, naming, docs, speculative
performance and unchanged code are not findings, unless the change makes old code reachable.
At most five nits; an empty review is a valid result.

## Step 1 - Fresh reviewers

Start subagents in parallel. They judge the code and the requirement; they do not get your
reasoning, earlier results or any claim about quality ("fixed", "safe", "tested") - those steer a
reviewer away from the bug.

- **Correctness** gets the diff and the requirement as it stood before the code (the issue, the
  roadmap section, the plan line), as a claim to check against the code.
- **Security and data** (full only) gets the diff and goes through "What counts" point by point:
  what an unattended card run can reach, what reaches the watch, what free text reaches a prompt.
- Over ~1,000 lines: one correctness reviewer per group of files (rules, store and migrations;
  tools, MCP server and web; simulator and evals).

The prompt for each:

```
Review this diff against "What counts" in .claude/skills/deep-review/SKILL.md. Report what it
calls important and at most five nits. Each finding: file:line, a concrete failure scenario
(input or state -> wrong result), pre-existing yes/no. Nothing found = an empty list.
```

**Done when** every reviewer returned a list, empty or not.

## Step 2 - Prove

The referee is a red test, not a second opinion.

- **Important finding:** a test written from the finding's scenario, not from the code. It counts
  only when it was seen red at its own assertion while the fix was missing - run it before the
  fix, or take the fix lines out briefly and put them back.
- **Not reproducible:** "plausible, not reproduced"; the live run or the maintainer decides. It is
  never counted as refuted.
- **Refuted** only with the `file:line` that contradicts it.
- **Nit:** fix it, or drop it with one sentence why.

## Step 3 - Fix, round 2

Fix each proven finding until its test is green. Then a fresh reviewer gets the fixes, one line
per class of error found in round 1 ("a check-in was not counted as an input of the coach card"),
and the question whether a fix removed a check, a guard or a test.

When round 2 finds a new important finding of a class from round 1, close the class instead of the
case: one function every caller asks, or a property test over a grid of inputs ("no check-in ever
makes a week bigger"). Where that is not possible, the maintainer decides.

## Step 4 - Live run

At least one real story end to end, on data no test generated:

- a throw-away `RUNSPECTOR_HOME` - a copy of a real database (`sqlite3 <db> ".backup <copy>"`,
  without the `garmin/` token folder), deleted afterwards - or `runspector serve --demo`;
- the page in a browser, `runspector today`, a real Claude chat over `runspector mcp`, and for a
  fence change a real card run that tries what it must not do;
- a write to a real Garmin account only after the maintainer's explicit yes, with `undo_workout`
  as the way back.

**Done when** what you saw matches the requirement; otherwise back to step 2.

## Step 5 - Fresh reader (full only)

A new agent sees only the final diff - no findings, no history, no claim about quality:

```
Name the most likely way this breaks in production: file:line, input or state -> wrong result.
If you find none, write "nothing found"; "unsure: <what I could not check>" is allowed.
```

A concrete scenario goes back to step 2. "Nothing found" and "unsure" end the review.

## Finish

- **Ship** when no proven finding is open, the live run matched, and the reader had no concrete
  scenario.
- Code comments give the reason for a line; the review's story goes into the commit message, in
  plain words: what was found, what was fixed, and an "unsure:" line for what the reader or the
  live run could not settle. A bug found only in step 4 or 5 gets its regression test and a line
  there.
