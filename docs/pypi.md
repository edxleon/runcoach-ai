# Runspector

**A local-first AI running coach for your Garmin data.** Runspector syncs your Garmin Connect
data into a SQLite file on your own machine, decides today's training (GO / EASY / REST) with a
deterministic readiness engine, and lets Claude explain that decision and plan the week with you:
in a small web app, or as an MCP server for Claude Code and Claude Desktop. Nothing reaches your
watch without your yes.

Formerly `runcoach-ai`. Source, full documentation and the design decisions:
[github.com/edxleon/runspector](https://github.com/edxleon/runspector).

<p>
  <img src="https://raw.githubusercontent.com/edxleon/runspector/main/docs/screenshots/readme-today.png" alt="Today: readiness verdict, decision, week plan, signals" width="32%">
  <img src="https://raw.githubusercontent.com/edxleon/runspector/main/docs/screenshots/readme-coach.png" alt="Coach: a card written by Claude from the tools' data" width="32%">
  <img src="https://raw.githubusercontent.com/edxleon/runspector/main/docs/screenshots/readme-runs.png" alt="Runs: intensity bands and detected interval structure" width="32%">
</p>

## Quick start

```bash
uv tool install runspector    # needs uv: https://docs.astral.sh/uv/
# without uv:                 pipx install runspector
runspector serve --demo       # look around without any account
runspector serve              # the real thing: Garmin sign-in, then http://127.0.0.1:8765
```

Coach cards run on [Claude Code](https://claude.com/claude-code) on your machine, with your own
Claude sign-in: install it and run `claude auth login` once. A Claude plan is enough; no API key
is needed. Everything except the coach cards works without Claude.

## In Claude Code or Claude Desktop

```bash
claude mcp add runspector -- runspector mcp
# then ask: "should I train today?"
```

Or, without installing it first, in an MCP client's JSON configuration:

```json
{ "mcpServers": { "runspector": { "command": "uvx", "args": ["runspector", "mcp"] } } }
```

Sign in to Garmin once before that: `runspector login` (or `uvx runspector login`).

## Good to know

- Your Garmin password goes to Garmin once, to sign in; only the session token is kept, in a
  folder only your user account can read.
- Not affiliated with Garmin. It uses the unofficial
  [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library against
  your own account.
- A training tool, not medical advice.

MIT licensed. Issues: [github.com/edxleon/runspector/issues](https://github.com/edxleon/runspector/issues).

<!-- mcp-name: io.github.edxleon/runspector -->
