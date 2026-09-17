"""One-time Garmin login. Asks for e-mail, password and (if the account needs
it) the MFA code interactively, then stores only the resulting session tokens.
Neither the password nor the code is ever written to disk."""

from __future__ import annotations

import getpass
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


def interactive_login() -> int:
    from garminconnect import Garmin

    store = paths.garmin_dir()
    store.mkdir(parents=True, exist_ok=True)

    email = input("Garmin e-mail: ").strip()
    password = getpass.getpass("Garmin password (not stored): ")
    if not email or not password:
        print("E-mail and password are required.", file=sys.stderr)
        return 1

    client = Garmin(email=email, password=password,
                    prompt_mfa=lambda: input("MFA code (e-mail/SMS/app): ").strip())
    try:
        client.login()
    except Exception as exc:  # noqa: BLE001 — garminconnect raises many shapes
        print(f"Login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    _dump(client, str(store))
    try:
        name = client.get_full_name()
    except Exception:  # noqa: BLE001 — a nicety; never worth failing the login over
        name = "?"
    print(f"OK - tokens stored in {store} (signed in as {name}). Next: `runcoach serve`")
    return 0
