"""The first start, in the terminal `runcoach serve` was typed into.

A new user used to meet four steps in two windows. `serve` opened a page
saying "run `runcoach login`"; after the login the same page asked for
`runcoach sync --days 30`; and most restarted `serve` in between, because
nothing said the running server would see the login. The two accounts the app
depends on are now checked before the server starts, in the terminal where
`serve` was typed: Garmin (signed in through `runcoach login`'s own code)
and Claude (the `claude` CLI, signed in with the subscription). The first
sync then fetches 30 days by itself.

No terminal (a service manager, a pipe, CI): nothing is asked, the status is
printed, and the page in the browser says the same.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable

from . import paths

#: How long a Claude status answer is reused by the web state. The CLI takes
#: about a second; short enough that a reload after `claude auth login` shows it.
CLAUDE_STATUS_TTL_S = 20
_cache: dict = {"at": 0.0, "status": None}


def claude_command() -> list[str] | None:
    override = os.environ.get("RUNCOACH_CLAUDE_CMD")   # JSON array; lets tests stub the CLI
    if override:
        return json.loads(override)
    found = shutil.which("claude")
    return [found] if found else None


def claude_status(*, fresh: bool = False) -> str:
    """`missing` (no CLI), `subscription` (signed in with a Claude plan),
    `other` (signed in another way - an API key or a cloud provider),
    `signed_out`, or `unknown` (the CLI did not say).

    Coach cards run `claude --print` on the athlete's own CLI session, so the
    subscription is not something to connect inside runcoach - it is the
    CLI's login. `claude auth status` reports it as JSON."""
    now = time.monotonic()
    cmd = claude_command()
    # Keyed by WHICH claude was asked: a different binary on PATH, or a stub
    # in a test, is a different answer, not the cached one.
    key = tuple(cmd or ())
    if (not fresh and _cache["status"] and _cache.get("key") == key
            and now - _cache["at"] < CLAUDE_STATUS_TTL_S):
        return _cache["status"]
    if cmd is None:
        status = "missing"
    else:
        try:
            out = subprocess.run([*cmd, "auth", "status"],  # noqa: S603 — claude_command(), fixed args
                                 capture_output=True, text=True,
                                 timeout=20, encoding="utf-8", errors="replace")
            info = json.loads(out.stdout or "{}")
            if not isinstance(info, dict) or "loggedIn" not in info:
                status = "unknown"
            elif not info["loggedIn"]:
                status = "signed_out"
            else:
                status = "subscription" if info.get("authMethod") == "claude.ai" else "other"
        except (OSError, ValueError, subprocess.SubprocessError):
            status = "unknown"
    _cache.update(at=now, status=status, key=key)
    return status


CLAUDE_LINES = {
    "missing": "not installed - coach cards need Claude Code: https://claude.com/claude-code "
               "(install it, then run `claude auth login` once)",
    "subscription": "signed in with your Claude subscription - coach cards run on it, "
                    "nothing to set up in runcoach",
    "other": "signed in, but not with a Claude subscription (API key or cloud provider) - "
             "runcoach strips API-key variables, so sign in with `claude auth login`",
    "signed_out": "installed, not signed in - run `claude auth login`",
    "unknown": "installed (its login could not be checked - `claude auth status` tells you)",
}


def first_start(*, interactive: bool, ask: Callable[[str], str] = input,
                garmin_login: Callable[[], int] | None = None,
                claude_login: Callable[[list[str]], int] | None = None,
                say: Callable[[str], None] = print) -> None:
    """Check Garmin and Claude before the server starts; in a terminal, offer
    to sign in to whichever is missing, right here."""
    if garmin_login is None:
        from .auth import interactive_login

        def garmin_login() -> int:
            return interactive_login(next_step=None)
    if claude_login is None:
        def claude_login(cmd: list[str]) -> int:
            return subprocess.run([*cmd, "auth", "login"]).returncode  # noqa: S603 — as `auth status` above

    def yes(question: str) -> bool:
        if not interactive:
            return False
        try:
            return ask(f"{question} [Y/n] ").strip().lower() in ("", "y", "yes", "j", "ja")
        except (EOFError, KeyboardInterrupt):
            say("")
            return False

    say("Setup check")
    if paths.garmin_session_present():
        say("  Garmin   signed in")
    else:
        say("  Garmin   not signed in - runcoach reads your data with your own Garmin session")
        if yes("           Sign in now? (e-mail, password and the MFA code, once - only a\n"
               "           session token is kept, on this computer)"):
            ok = garmin_login() == 0
            say("  Garmin   signed in - the first sync fetches the last 30 days" if ok
                else "  Garmin   not signed in - `runcoach login` any time; no restart needed")
        else:
            say("           later: `runcoach login` in any terminal, then ↻ in the app - "
                "no restart needed")

    status = claude_status(fresh=True)
    say(f"  Claude   {CLAUDE_LINES[status]}")
    if status in ("signed_out", "other") and (cmd := claude_command()) is not None:
        if yes("           Sign in to Claude now (opens your browser)?"):
            claude_login(cmd)
            status = claude_status(fresh=True)
            say(f"  Claude   {CLAUDE_LINES[status]}")
    say("")


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
