"""The first start: Garmin and Claude checked in the terminal `serve` runs in.

A new user met four steps in two windows - `serve`, a page saying "run
`runcoach login`", the login in another terminal, then `runcoach sync --days
30` - and most restarted `serve` in between. These pin the shorter way."""

from __future__ import annotations

import json
import sys

import pytest

from runcoach import onboarding, paths


def _claude(monkeypatch, answer: dict | None, *, rc: int = 0):
    """Stub `claude auth status` with a one-line Python program."""
    body = json.dumps(answer) if answer is not None else "not json"
    monkeypatch.setenv("RUNCOACH_CLAUDE_CMD", json.dumps(
        [sys.executable, "-c", f"import sys; print({body!r}); sys.exit({rc})"]))
    onboarding._cache.update(at=0.0, status=None)


@pytest.mark.parametrize("answer,status", [
    ({"loggedIn": True, "authMethod": "claude.ai"}, "subscription"),
    ({"loggedIn": True, "authMethod": "apiKey"}, "other"),
    ({"loggedIn": False}, "signed_out"),
    (None, "unknown"),
])
def test_the_claude_login_is_read_from_the_cli_itself(monkeypatch, answer, status):
    """The subscription is not connected inside runcoach: it is the `claude`
    CLI's own login, and `claude auth status` says which one it is."""
    _claude(monkeypatch, answer)
    assert onboarding.claude_status(fresh=True) == status


def test_no_cli_is_missing(monkeypatch):
    monkeypatch.delenv("RUNCOACH_CLAUDE_CMD", raising=False)
    monkeypatch.setattr(onboarding.shutil, "which", lambda name: None)
    assert onboarding.claude_status(fresh=True) == "missing"


def _first_start(monkeypatch, tmp_path, *, interactive, answers, signed_in_garmin=False):
    monkeypatch.setenv("RUNCOACH_HOME", str(tmp_path))
    if signed_in_garmin:
        paths.garmin_dir().mkdir(parents=True, exist_ok=True)
        (paths.garmin_dir() / "garmin_tokens.json").write_text("{}", encoding="utf-8")
    calls, out = {"garmin": 0, "claude": 0}, []
    it = iter(answers)

    def garmin_login():
        calls["garmin"] += 1
        return 0

    def claude_login(cmd):
        calls["claude"] += 1
        return 0

    onboarding.first_start(interactive=interactive, ask=lambda q: next(it),
                           garmin_login=garmin_login, claude_login=claude_login,
                           say=out.append)
    return calls, "\n".join(out)


def test_serve_offers_the_garmin_login_in_its_own_terminal(monkeypatch, tmp_path):
    _claude(monkeypatch, {"loggedIn": True, "authMethod": "claude.ai"})
    calls, out = _first_start(monkeypatch, tmp_path, interactive=True, answers=[""])
    assert calls["garmin"] == 1, out
    assert "first sync fetches the last 30 days" in out
    assert "subscription" in out


def test_without_a_terminal_nothing_is_asked_and_the_way_is_named(monkeypatch, tmp_path):
    _claude(monkeypatch, {"loggedIn": False})
    calls, out = _first_start(monkeypatch, tmp_path, interactive=False, answers=[])
    assert calls == {"garmin": 0, "claude": 0}
    assert "runcoach login" in out and "no restart needed" in out
    assert "claude auth login" in out


def test_a_signed_out_claude_is_offered_its_login(monkeypatch, tmp_path):
    _claude(monkeypatch, {"loggedIn": False})
    calls, out = _first_start(monkeypatch, tmp_path, interactive=True, answers=["y"],
                              signed_in_garmin=True)
    assert calls == {"garmin": 0, "claude": 1}, out
    assert "Garmin   signed in" in out


def test_the_first_sync_of_an_empty_store_takes_a_month(tmp_path, monkeypatch):
    """`runcoach sync --days 30` was a step of its own on the first-run page."""
    from runcoach import garmin, sync
    from runcoach.web import server

    monkeypatch.setenv("RUNCOACH_HOME", str(tmp_path))
    asked = []

    class Rep:
        fatal = errors = soft_errors = None

        def summary(self):
            return "[OK]"

    monkeypatch.setattr(garmin, "login", lambda tokenstore=None: object())
    monkeypatch.setattr(sync, "run", lambda store, client, days, say=print: asked.append(days) or Rep())
    app = server.App(demo=False, token=None)
    try:
        app._sync(0)
        assert asked == [sync.FIRST_SYNC_DAYS]
    finally:
        app.worker_stop.set()
