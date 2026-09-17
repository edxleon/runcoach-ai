"""Where runcoach keeps its data. Everything lives under one directory
(`~/.runcoach`, override with `RUNCOACH_HOME`) so that uninstalling is `rm -r`."""

from __future__ import annotations

import os
from datetime import date, datetime, tzinfo
from pathlib import Path


def home() -> Path:
    root = Path(os.environ.get("RUNCOACH_HOME") or Path.home() / ".runcoach")
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
    return home() / "runcoach.db"


def garmin_dir() -> Path:
    """Garmin session tokens (written by `runcoach login`). `RUNCOACH_GARMIN_TOKENS`
    points at an existing python-garminconnect token directory instead."""
    override = os.environ.get("RUNCOACH_GARMIN_TOKENS")
    return Path(override).expanduser() if override else home() / "garmin"


def cards_dir() -> Path:
    d = home() / "cards"
    d.mkdir(exist_ok=True)
    return d


def jobs_dir() -> Path:
    d = home() / "jobs"
    d.mkdir(exist_ok=True)
    return d


def profile_path() -> Path:
    return home() / "profile.json"


def local_tz() -> tzinfo | None:
    """The athlete's timezone: `RUNCOACH_TZ` (IANA name) or the system zone (None).

    A workout's calendar day must be the *local* day — a late-evening run stored
    in UTC would otherwise slide into tomorrow and skew every 7-day window."""
    name = os.environ.get("RUNCOACH_TZ")
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
    """The file that makes ONE `runcoach serve` the owner of this data directory.

    `serve()` already refuses to start on a taken port, and the comment there
    explains why: a second instance's `recover_stale()` rewrites a job it does
    not own, flipping the live instance's running card to `failed`. But that
    guard only covers the same PORT, and `--port` is a documented flag. Two
    instances on one `RUNCOACH_HOME` also let the same template be started
    twice - two paid runs - because the `already_running` check only looks at
    job files, which carry no owner."""
    return home() / "serve.lock"
