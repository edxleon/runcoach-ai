"""The first start: Garmin and Claude checked in the terminal `serve` runs in.

A new user met four steps in two windows - `serve`, a page saying "run
`runspector login`", the login in another terminal, then `runspector sync --days
30` - and most restarted `serve` in between. These pin the shorter way."""

from __future__ import annotations

import json
import sys

import pytest

from runspector import onboarding, paths


def _claude(monkeypatch, answer: dict | None, *, rc: int = 0):
    """Stub `claude auth status` with a one-line Python program."""
    body = json.dumps(answer) if answer is not None else "not json"
    monkeypatch.setenv("RUNSPECTOR_CLAUDE_CMD", json.dumps(
        [sys.executable, "-c", f"import sys; print({body!r}); sys.exit({rc})"]))
    onboarding._cache.update(at=0.0, status=None)


@pytest.mark.parametrize("answer,status", [
    ({"loggedIn": True, "authMethod": "claude.ai"}, "subscription"),
    ({"loggedIn": True, "authMethod": "apiKey"}, "other"),
    ({"loggedIn": False}, "signed_out"),
    (None, "unknown"),
])
def test_the_claude_login_is_read_from_the_cli_itself(monkeypatch, answer, status):
    """The subscription is not connected inside runspector: it is the `claude`
    CLI's own login, and `claude auth status` says which one it is."""
    _claude(monkeypatch, answer)
    assert onboarding.claude_status(fresh=True) == status


def test_no_cli_is_missing(monkeypatch):
    monkeypatch.delenv("RUNSPECTOR_CLAUDE_CMD", raising=False)
    monkeypatch.setattr(onboarding.shutil, "which", lambda name: None)
    assert onboarding.claude_status(fresh=True) == "missing"


def _first_start(monkeypatch, tmp_path, *, interactive, answers, signed_in_garmin=False):
    monkeypatch.setenv("RUNSPECTOR_HOME", str(tmp_path))
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
    assert "runspector login" in out and "no restart needed" in out
    assert "claude auth login" in out


def test_a_signed_out_claude_is_offered_its_login(monkeypatch, tmp_path):
    _claude(monkeypatch, {"loggedIn": False})
    calls, out = _first_start(monkeypatch, tmp_path, interactive=True, answers=["y"],
                              signed_in_garmin=True)
    assert calls == {"garmin": 0, "claude": 1}, out
    assert "Garmin   signed in" in out


def test_an_api_key_sign_in_is_offered_the_plan_login_and_the_switch(monkeypatch, tmp_path):
    _claude(monkeypatch, {"loggedIn": True, "authMethod": "apiKey"})
    calls, out = _first_start(monkeypatch, tmp_path, interactive=True, answers=["n"],
                              signed_in_garmin=True)
    assert calls["claude"] == 0 and "RUNSPECTOR_ALLOW_API_KEY=1" in out


def test_an_allowed_api_key_sign_in_is_a_choice_not_a_gap(monkeypatch, tmp_path):
    """With the switch set, `other` is the sign-in the athlete chose: asking
    them to sign in again with a plan would be asking them to undo it."""
    _claude(monkeypatch, {"loggedIn": True, "authMethod": "apiKey"})
    monkeypatch.setenv("RUNSPECTOR_ALLOW_API_KEY", "1")
    calls, out = _first_start(monkeypatch, tmp_path, interactive=True, answers=[],
                              signed_in_garmin=True)
    assert calls["claude"] == 0
    assert "coach cards use it" in out


def test_the_first_sync_of_an_empty_store_takes_a_month(tmp_path, monkeypatch):
    """`runspector sync --days 30` was a step of its own on the first-run page."""
    from runspector import garmin, sync
    from runspector.web import server

    monkeypatch.setenv("RUNSPECTOR_HOME", str(tmp_path))
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


def test_the_login_says_what_happens_to_the_password_before_asking_for_it(tmp_path, monkeypatch, capsys):
    """What is kept, where, and how to take it back - before the e-mail prompt."""
    from runspector import auth

    monkeypatch.setenv("RUNSPECTOR_HOME", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
    assert auth.interactive_login() == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "runspector never stores them" in out
    assert "Only that token is saved, on this computer" in out
    assert str(paths.garmin_dir()) in out and "To revoke it" in out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_token_folder_is_readable_by_its_owner_only(tmp_path, monkeypatch):
    """The notice promises it for whatever folder is used - also one that
    `RUNSPECTOR_GARMIN_TOKENS` points at outside the 0700 home."""
    from runspector import auth

    store = tmp_path / "elsewhere"
    store.mkdir(mode=0o755)
    (store / "garmin_tokens.json").write_text("{}", encoding="utf-8")
    (store / "garmin_tokens.json").chmod(0o644)
    auth._lock_down(store)
    assert store.stat().st_mode & 0o777 == 0o700
    assert (store / "garmin_tokens.json").stat().st_mode & 0o777 == 0o600


def test_the_data_folder_is_shown_without_the_account_name(tmp_path, monkeypatch):
    """The footer names where the data lives; a screenshot of it should not
    name the user."""
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.display_path(tmp_path / ".runspector") == "~/.runspector"
    # Outside the home the path is shown as the system writes it (backslashes on Windows).
    assert paths.display_path(Path("/opt/elsewhere")) == str(Path("/opt/elsewhere"))
