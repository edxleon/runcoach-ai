"""One-time Garmin login. Asks for e-mail, password and (if the account needs
it) the MFA code - in the terminal (`runcoach login`) or in the web page's
sign-in dialog (`WebLogin`) - then stores only the resulting session tokens.
Neither the password nor the code is ever written to disk."""

from __future__ import annotations

import getpass
import os
import sys
import textwrap
import threading
import time

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
#: explained. One list for both ways in: the terminal prints it, the web page's
#: sign-in dialog shows the same points (`/api/state` → `garmin_login`).
NOTICE_POINTS = (
    "Your e-mail and password go to Garmin only, once, to sign in. runcoach never stores them.",
    "Garmin answers with a session token. Only that token is saved, on this computer, in "
    "{store} - a folder only your user account can read.",
    "The token is sent to Garmin and nowhere else: runcoach uses it to read your data and, "
    "only after your yes, to put a workout on your watch.",
    "To revoke it: delete that folder, or change your Garmin password.",
)

LOGIN_NOTICE = "How the Garmin sign-in works\n" + "".join(
    textwrap.fill(p, 80, initial_indent="  - ", subsequent_indent="    ") + "\n"
    for p in NOTICE_POINTS)


def notice_points(store) -> list[str]:
    return [p.format(store=store) for p in NOTICE_POINTS]


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


def _token_dir():
    """The token folder, created and locked down - or `None` and the sentence
    that says why it cannot be used."""
    store = paths.garmin_dir()
    try:
        store.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # `RUNCOACH_GARMIN_TOKENS` pointing at a FILE is the likely mistake here
        # — garth writes token FILES, so naming one is a natural misreading —
        # and `mkdir` answered it with a raw FileExistsError traceback. Same
        # class as the no-terminal login: say which path and what it should be.
        return None, (f"Cannot use {store} as the token directory: {type(exc).__name__}: {exc}\n"
                      "RUNCOACH_GARMIN_TOKENS must name a DIRECTORY (garth writes several "
                      "token files into it), not a single file.")
    _lock_down(store)
    return store, ""


def interactive_login(next_step: str | None = "`runcoach serve`") -> int:
    from garminconnect import Garmin

    store, problem = _token_dir()
    if store is None:
        print(problem, file=sys.stderr)
        return 2
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


#: How long a web sign-in waits for its MFA code. Garmin's e-mail with the code
#: can take a minute or two; the pending sign-in holds Garmin's session cookies
#: in memory, so it does not wait for ever.
MFA_WAIT_S = 600

#: Codes one sign-in takes before it ends. Whether Garmin took a code before
#: something failed cannot be read reliably from the library (its token
#: exchange runs inside the code step), so a failed code is never assumed to
#: be retryable for ever: after this many, only a new start - and a new code -
#: helps, and Garmin's MFA endpoint is not hammered with a spent one.
MFA_TRIES = 3


def _why(exc: Exception, *, mfa: bool = False) -> str:
    """The athlete's sentence for a failed sign-in. The class name decides, never
    the message: garminconnect's texts are written for developers, and the page
    shows this one to the athlete."""
    name = type(exc).__name__
    if "TooManyRequests" in name:
        return ("Garmin is refusing sign-ins for now (too many attempts). Wait a few "
                "minutes, then try again.")
    if "Authentication" in name:
        # The library raises this for a wrong code AND for one it could not
        # get checked (offline, a Cloudflare page): say no more than that.
        return ("Garmin did not accept that code, or could not check it. Check it and "
                "try again - or start again for a new code." if mfa
                else "Garmin did not accept that e-mail and password.")
    return (f"The sign-in did not go through ({name}). Try again in a moment - "
            f"`runcoach login` in a terminal says more.")


class WebLogin:
    """`runcoach login` for the web page, in two calls: `start(email, password)`
    and, when Garmin asks for a code, `finish(code)`.

    Nothing is written until Garmin says yes, and then only the session token,
    exactly as in the terminal. The password lives for the length of `start`;
    a pending MFA sign-in (Garmin's session cookies, as sensitive as the
    password) lives in this object's memory only and is never part of any
    answer; past `MFA_WAIT_S` it is refused and dropped on the next call - the
    page asks on every load.

    One sign-in at a time, and a request while one runs is refused, not
    queued: queued, a page that gave up on a slow Garmin and asked again sent
    a second round of logins - and a second code - to the same account."""

    _BUSY = {"error": "A sign-in is already under way - wait for its answer.", "busy": True}

    def __init__(self, clock=time.monotonic):
        self._lock = threading.Lock()
        self._pending: tuple | None = None          # (client, state, since)
        self._tries = 0
        #: Sign-ins that went through. A page following a late answer compares
        #: it: "a session exists" is no proof, the expired one is still on disk.
        self.signins = 0
        self._clock = clock

    def waiting_for_code(self) -> bool:
        p = self._pending
        if p is not None and self._clock() - p[2] > MFA_WAIT_S:
            # Expired means gone, not only reported as gone: it holds
            # Garmin's session cookies. The page asks on every load.
            self._pending = p = None
        return p is not None

    def busy(self) -> bool:
        """A sign-in is talking to Garmin right now - the page that stopped
        waiting for its answer follows it through `/api/state`."""
        return self._lock.locked()

    def cancel(self) -> None:
        with self._lock:
            self._pending = None

    def start(self, email: str, password: str) -> dict:
        if not self._lock.acquire(blocking=False):
            return dict(self._BUSY)
        try:
            return self._start(email, password)
        finally:
            self._lock.release()

    def finish(self, code: str) -> dict:
        if not self._lock.acquire(blocking=False):
            return dict(self._BUSY)
        try:
            return self._finish(code)
        finally:
            self._lock.release()

    def _start(self, email: str, password: str) -> dict:
        from garminconnect import Garmin

        self._pending = None
        store, problem = _token_dir()
        if store is None:
            return {"error": problem}
        client = Garmin(email=email, password=password, return_on_mfa=True)
        try:
            status, state = client.login()
        except Exception as exc:  # noqa: BLE001 — garminconnect raises many shapes
            return {"error": _why(exc)}
        finally:
            # The MFA step does not need it, and a pending sign-in should not
            # keep the password in memory for ten minutes.
            client.password = None
        if status == "needs_mfa":
            self._pending, self._tries = (client, state, self._clock()), 0
            return {"state": "code"}
        return self._keep(client, store)

    def _finish(self, code: str) -> dict:
        p = self._pending
        if p is None or self._clock() - p[2] > MFA_WAIT_S:
            self._pending = None
            return {"error": "This sign-in has expired - start again with your e-mail "
                             "and password.", "restart": True}
        client, state, _ = p
        try:
            client.resume_login(state, code)
        except Exception as exc:  # noqa: BLE001
            # A token the client already holds IS the sign-in: Garmin took the
            # code and only the library's own profile load after it failed.
            if not getattr(getattr(client, "client", None), "is_authenticated", False):
                # Otherwise nothing tells a wrong code from one Garmin took
                # before a later step failed: offer both ways out, and end
                # the sign-in after `MFA_TRIES`.
                self._tries += 1
                if self._tries >= MFA_TRIES:
                    self._pending = None
                    return {"error": f"That was {MFA_TRIES} codes. Start again with your "
                                     f"e-mail and password for a new one.", "restart": True}
                return {"error": _why(exc, mfa=True)}
        self._pending = None
        store, problem = _token_dir()
        if store is None:
            return {"error": problem}
        return self._keep(client, store)

    def _keep(self, client, store) -> dict:
        try:
            _dump(client, str(store))
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Garmin signed you in, but the token could not be saved in "
                             f"{store} ({type(exc).__name__})."}
        _lock_down(store)
        self.signins += 1
        return {"state": "signed_in"}
