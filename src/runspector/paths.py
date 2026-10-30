"""Where runspector keeps its data. Everything lives under one directory
(`~/.runspector`, override with `RUNSPECTOR_HOME`) so that uninstalling is `rm -r`."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, tzinfo
from pathlib import Path

log = logging.getLogger(__name__)


def home() -> Path:
    root = Path(os.environ.get("RUNSPECTOR_HOME") or Path.home() / ".runspector")
    existed = root.is_dir()
    root.mkdir(parents=True, exist_ok=True)
    if not existed and os.name != "nt":
        # Sleep, HRV, resting heart rate and the Garmin session tokens live in
        # here. At the default umask that is world-readable to every other
        # account on the machine. Only on creation, so we never fight a user who
        # deliberately widened it.
        try:
            root.chmod(0o700)
        except OSError:
            pass
    return root


def db_path() -> Path:
    return home() / "runspector.db"


def garmin_dir() -> Path:
    """Garmin session tokens (written by `runspector login`). `RUNSPECTOR_GARMIN_TOKENS`
    points at an existing python-garminconnect token directory instead."""
    override = os.environ.get("RUNSPECTOR_GARMIN_TOKENS")
    return Path(override).expanduser() if override else home() / "garmin"


def garmin_session_present() -> bool:
    """Has `runspector login` ever run? A token DIRECTORY with something in it -
    whether the tokens are still valid is a network question (`doctor` asks it).

    One definition for three readers: `doctor` reports it, the web app's state
    carries it so the page can tell "never logged in" from "the sync failed"
    (a first start used to show the second, as an error, with an exception name
    in it), and `serve()` reads it to not even attempt a startup sync that has
    no session to run on."""
    d = garmin_dir()
    try:
        return d.is_dir() and any(d.iterdir())
    except OSError:
        return False


def cards_dir() -> Path:
    d = home() / "cards"
    d.mkdir(exist_ok=True)
    return d


def jobs_dir() -> Path:
    d = home() / "jobs"
    d.mkdir(exist_ok=True)
    return d


def proposals_dir() -> Path:
    """Sessions proposed and not yet (or already) written to Garmin (`plan.py`)."""
    d = home() / "proposals"
    d.mkdir(exist_ok=True)
    return d


def profile_path() -> Path:
    return home() / "profile.json"


def account_path() -> Path:
    """What the athlete's Garmin account says about them (units, language,
    first weekday) - written by the sync, read as defaults the profile beats."""
    return home() / "account.json"


def local_tz() -> tzinfo | None:
    """The athlete's timezone: `RUNSPECTOR_TZ` (IANA name) or the system zone (None).

    A workout's calendar day must be the *local* day — a late-evening run stored
    in UTC would otherwise slide into tomorrow and skew every 7-day window."""
    name = os.environ.get("RUNSPECTOR_TZ")
    if not name:
        return None
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


def local_day(dt: datetime) -> date:
    """Calendar day of an aware datetime in the athlete's timezone."""
    return dt.astimezone(local_tz()).date()


def today() -> date:
    return datetime.now().astimezone(local_tz()).date()


def home_lock() -> Path:
    """The file that makes ONE `runspector serve` the owner of this data directory.

    `serve()` already refuses to start on a taken port, and the comment there
    explains why: a second instance's `recover_stale()` rewrites a job it does
    not own, flipping the live instance's running card to `failed`. But that
    guard only covers the same PORT, and `--port` is a documented flag. Two
    instances on one `RUNSPECTOR_HOME` also let the same template be started
    twice - two paid runs - because the `already_running` check only looks at
    job files, which carry no owner."""
    return home() / "serve.lock"


def display_path(p: Path) -> str:
    """`p` with the user's home written as `~` - for text a person reads."""
    try:
        return "~/" + Path(p).resolve().relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return str(p)


# ── atomic JSON files (jobs, cards, proposals, account settings) ─────────────

_REPLACE_ATTEMPTS = 6


def write_json_atomic(path: Path, data: dict) -> None:
    """Write atomically, via a temp file in the same directory. Shared by the
    job and card files, the proposals and the Garmin account settings.

    The temp name carries the PROCESS and the THREAD. Deriving it from the
    target (`path.with_suffix(".tmp")`) meant two writers of the same job - the
    worker finishing a run and an HTTP thread recording feedback - used one temp
    file, so one could unlink the other's half-written content out from under
    it. The process id alone did not fix that: those two writers are threads of
    one process, and the second swap left the first one a FileNotFoundError.

    The final attempt is INSIDE the loop. It used to sit after it, unguarded, so
    a busy moment surfaced as a raw `PermissionError` out of a background
    thread; one test caught it once in three runs, which on a Windows CI runner
    is a flake in every sense except the one that matters."""
    tmp = path.with_name(f"{path.stem}.{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1 * (attempt + 1))


def read_json(path: Path) -> dict | None:
    """Read one job or card, or `None` if it is genuinely not there.

    RETRIES a sharing violation, which is the reader's half of the rule
    `write_json_atomic` already follows. On Windows, opening a file at the instant
    `os.replace` swaps it raises `PermissionError` - and the callers here treat
    `None` as "this job does not exist", so a single unlucky read made a queued
    job INVISIBLE: `count_active()` undercounted, the queue cap let a fourth job
    through, and the job strip lost a row. It surfaced as an unreproducible
    flake in `test_spawn_queues_a_job_and_dedups`, three times, always under
    load - which is exactly when it matters in production too.

    A MISSING file returns immediately: waiting 200 ms for every deleted card
    would make `cleanup()` crawl."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except OSError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                log.warning("could not read %s after %d attempts", path.name, _REPLACE_ATTEMPTS)
                return None
            time.sleep(0.05 * (attempt + 1))
            continue
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
    return None
