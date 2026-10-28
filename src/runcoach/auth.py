"""One-time Garmin login. Asks for e-mail, password and (if the account needs
it) the MFA code interactively, then stores only the resulting session tokens.
Neither the password nor the code is ever written to disk."""

from __future__ import annotations

import getpass
import os
import sys

from . import paths


def _dump(client, store: str) -> None:
    for attr in ("garth", "client"):
        obj = getattr(client, attr, None)
        if obj is not None and hasattr(obj, "dump"):
            obj.dump(store)
            return
    raise RuntimeError("no dump()-capable token object found on the Garmin client")


def verify() -> int:
    """Test the token resume only — no credentials involved."""
    from .garmin import login

    try:
        client = login()
        print(f"OK - session resumed from {paths.garmin_dir()} (signed in as {client.get_full_name()})")
        return 0
    except Exception as exc:  # noqa: BLE001 — any failure means "log in again"
        print(f"Resume failed: {type(exc).__name__}: {exc}\nRun `runcoach login`.", file=sys.stderr)
        return 2


#: Said BEFORE the e-mail is asked for: what happens to it, what is kept, where,
#: and how to take it back. A login prompt without it asks for trust it has not
#: explained.
LOGIN_NOTICE = """\
How the Garmin sign-in works
  - Your e-mail and password go to Garmin only, once, to sign in. runcoach never
    stores them.
  - Garmin answers with a session token. Only that token is saved, on this
    computer, in {store} - a folder only your user account can read.
  - The token is sent to Garmin and nowhere else: runcoach uses it to read your
    data and, only after your yes, to put a workout on your watch.
  - To revoke it: delete that folder, or change your Garmin password.
"""


def _lock_down(store) -> None:
    """The token folder and its files readable by this user only. The home
    directory is created 0700, but `RUNCOACH_GARMIN_TOKENS` can point anywhere,
    and the notice above promises it for whatever folder is used. Windows has
    no POSIX modes; there the user profile's ACL is what protects it."""
    if os.name == "nt":
        return
    try:
        store.chmod(0o700)
        for f in store.iterdir():
            if f.is_file():
                f.chmod(0o600)
    except OSError:
        pass


def interactive_login(next_step: str | None = "`runcoach serve`") -> int:
    from garminconnect import Garmin

    store = paths.garmin_dir()
    try:
        store.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # `RUNCOACH_GARMIN_TOKENS` pointing at a FILE is the likely mistake here
        # — garth writes token FILES, so naming one is a natural misreading —
        # and `mkdir` answered it with a raw FileExistsError traceback. Same
        # class as the no-terminal login: say which path and what it should be.
        print(f"Cannot use {store} as the token directory: {type(exc).__name__}: {exc}\n"
              "RUNCOACH_GARMIN_TOKENS must name a DIRECTORY (garth writes several "
              "token files into it), not a single file.", file=sys.stderr)
        return 2

    _lock_down(store)
    print(LOGIN_NOTICE.format(store=store))
    # `input()` raises EOFError when there is no terminal - a pipe, a cron, a
    # CI job, `< /dev/null` - and KeyboardInterrupt on Ctrl+C. Both used to end
    # in a traceback, which is the wrong answer to "I ran this in the wrong
    # place": say what the command needs instead.
    try:
        email = input("Garmin e-mail: ").strip()
        password = getpass.getpass("Garmin password (not stored): ")
    except EOFError:
        print("\n`runcoach login` is interactive: run it in a terminal where it can ask "
              "for e-mail, password and the MFA code.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nLogin cancelled.", file=sys.stderr)
        return 1
    if not email or not password:
        print("E-mail and password are required.", file=sys.stderr)
        return 1

    client = Garmin(email=email, password=password,
                    prompt_mfa=lambda: input("MFA code (e-mail/SMS/app): ").strip())
    try:
        client.login()
    except (EOFError, KeyboardInterrupt):
        print("\nLogin cancelled before the MFA code was entered.", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — garminconnect raises many shapes
        print(f"Login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    _dump(client, str(store))
    _lock_down(store)
    try:
        name = client.get_full_name()
    except Exception:  # noqa: BLE001 — a nicety; never worth failing the login over
        name = "?"
    print(f"OK - only the session token was saved, in {store} (signed in as {name})."
          + (f" Next: {next_step}" if next_step else ""))
    return 0
