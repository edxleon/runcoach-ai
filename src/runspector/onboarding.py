"""The first start, in the terminal `runspector serve` was typed into.

A new user used to meet four steps in two windows. `serve` opened a page
saying "run `runspector login`"; after the login the same page asked for
`runspector sync --days 30`; and most restarted `serve` in between, because
nothing said the running server would see the login. The two accounts the app
depends on are now checked before the server starts, in the terminal where
`serve` was typed: Garmin (signed in through `runspector login`'s own code)
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
import tempfile
import time
from collections.abc import Callable

from . import paths

#: How long a Claude status answer is reused by the web state. The CLI takes
#: about a second; short enough that a reload after `claude auth login` shows it.
CLAUDE_STATUS_TTL_S = 20
_cache: dict = {"at": 0.0, "status": None}


def api_key_allowed() -> bool:
    """The athlete chose Claude Code's API-key sign-in for the cards. A switch,
    not the default, and not dropped: the stripping protects against a key that
    was exported for something else, but Anthropic's conditions for products
    that run Claude Code ask them not to restrict its built-in sign-in methods
    (code.claude.com/docs/en/legal-and-compliance) - so the choice stays open."""
    return os.environ.get("RUNSPECTOR_ALLOW_API_KEY") == "1"


#: Everything that moves `claude` off the signed-in plan and onto a metered
#: account. Inheriting the parent environment made "no per-token bill" a hope
#: rather than a rule: anyone with `ANTHROPIC_API_KEY` exported for other work
#: was billed per token by an app that says it never bills - and since the cost
#: card was removed, with nothing on screen to notice it by. So the default
#: strips them, and `RUNSPECTOR_ALLOW_API_KEY=1` keeps them (`api_key_allowed`).
#:
#: A PREFIX, not a list of names. The first version named seven variables and
#: was already incomplete when it was written (`ANTHROPIC_CUSTOM_HEADERS` can
#: carry an `x-api-key` header; profile and workload-identity variables point at
#: an org account). An enumeration has to be re-checked against every CLI
#: release, a prefix does not — and this app needs no `ANTHROPIC_*` variable in
#: the child at all.
BILLING_ENV_PREFIXES = ("ANTHROPIC_",)
BILLING_ENV = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")


def strips_billing(name: str) -> bool:
    return name in BILLING_ENV or name.startswith(BILLING_ENV_PREFIXES)


def child_env() -> dict:
    """The environment the agent runs in: the parent's, minus everything that
    would redirect billing unless `api_key_allowed()`. All the rest is passed
    through — the CLI needs PATH, HOME/USERPROFILE, SYSTEMROOT and its own
    config directory."""
    if api_key_allowed():
        return dict(os.environ)
    return {k: v for k, v in os.environ.items() if not strips_billing(k)}


def claude_command() -> list[str] | None:
    override = os.environ.get("RUNSPECTOR_CLAUDE_CMD")   # JSON array; lets tests stub the CLI
    if override:
        return json.loads(override)
    found = shutil.which("claude")
    return [found] if found else None


#: What `claude auth status` reports for a Claude plan: the browser login, and the
#: long-lived token `claude setup-token` makes for a machine without a browser.
PLAN_SIGN_INS = ("claude.ai", "oauth_token")


def claude_status(*, fresh: bool = False) -> str:
    """`missing` (no CLI), `subscription` (signed in with a Claude plan),
    `other` (signed in another way - an API key or a cloud provider),
    `signed_out`, or `unknown` (the CLI did not say).

    Coach cards run `claude --print` on the athlete's own CLI session, so the
    subscription is not something to connect inside runspector - it is the
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
            # Asked in the environment the cards run in: with a plan login and a
            # key exported for other work the CLI reports the key, while the
            # cards - which strip it - run on the plan.
            # And in an empty directory, as the cards are: project settings in
            # whatever directory `serve` was started from can carry a sign-in.
            with tempfile.TemporaryDirectory(prefix="runspector-auth-") as empty:
                out = subprocess.run([*cmd, "auth", "status"],  # noqa: S603 — claude_command(), fixed args
                                     capture_output=True, text=True, env=child_env(), cwd=empty,
                                     timeout=20, encoding="utf-8", errors="replace")
            info = json.loads(out.stdout or "{}")
            if not isinstance(info, dict) or "loggedIn" not in info:
                status = "unknown"
            elif not info["loggedIn"]:
                status = "signed_out"
            else:
                # `oauth_token` is CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`:
                # a plan's token, which the cards keep (it is no ANTHROPIC_* key).
                status = ("subscription" if info.get("authMethod") in PLAN_SIGN_INS
                          else "other")
        except (OSError, ValueError, subprocess.SubprocessError):
            status = "unknown"
    _cache.update(at=now, status=status, key=key)
    return status


CLAUDE_LINES = {
    "missing": "not installed - coach cards need Claude Code and a paid Claude plan (or an API "
               "account): https://claude.com/claude-code, then `claude auth login` once. Step by step: "
               "https://github.com/edxleon/runspector#connecting-claude",
    "subscription": "signed in with your Claude subscription - coach cards run on it, "
                    "nothing to set up in runspector",
    # Measured in the cards' own environment, so this is what they will run on:
    # a key or a cloud provider kept in Claude Code's settings, which no
    # stripping of variables reaches.
    "other": "signed in with an API key or a cloud provider, not a Claude plan - coach cards "
             "run on it and are billed there; for your plan: `claude auth login`",
    "signed_out": "installed, not signed in - run `claude auth login`",
    "unknown": "installed (its login could not be checked - `claude auth status` tells you)",
}


def claude_line(status: str) -> str:
    """The one sentence about the Claude sign-in, for doctor and the first start."""
    if status == "other" and api_key_allowed():
        return ("signed in with an API key or a cloud provider - coach cards use it "
                "(RUNSPECTOR_ALLOW_API_KEY=1) and are billed there")
    return CLAUDE_LINES[status]


def first_start(*, interactive: bool, ask: Callable[[str], str] = input,
                garmin_login: Callable[[], int] | None = None,
                claude_login: Callable[[list[str]], int] | None = None,
                say: Callable[[str], None] = print, page_signin: bool = False) -> None:
    """Check Garmin and Claude before the server starts; in a terminal, offer
    to sign in to whichever is missing, right here.

    `page_signin`: the page about to open can take the Garmin login itself (a
    server on this computer). Then the terminal does not ask: a prompt that
    Enter answers with "yes" put a new user into the terminal login, past the
    sign-in the page was built for."""
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
    elif page_signin:
        say("  Garmin   not signed in - sign in on the page: Setup → Sign in to Garmin\n"
            "           (or `runspector login` in any terminal; the first sync starts right after)")
    else:
        say("  Garmin   not signed in - runspector reads your data with your own Garmin session")
        if yes("           Sign in now? (e-mail, password and the MFA code, once - only a\n"
               "           session token is kept, on this computer)"):
            ok = garmin_login() == 0
            say("  Garmin   signed in - the first sync fetches the last 30 days" if ok
                else "  Garmin   not signed in - `runspector login` any time; no restart needed")
        else:
            say("           later: `runspector login` in any terminal, then ↻ in the app - "
                "no restart needed")

    status = claude_status(fresh=True)
    say(f"  Claude   {claude_line(status)}")
    # An API-key sign-in the athlete allowed for the cards is a choice, not a gap.
    chosen = status == "other" and api_key_allowed()
    if status in ("signed_out", "other") and not chosen and (cmd := claude_command()) is not None:
        if yes("           Sign in to Claude now (opens your browser)?"):
            claude_login(cmd)
            status = claude_status(fresh=True)
            say(f"  Claude   {CLAUDE_LINES[status]}")
    say("")


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
