"""Where runspector keeps its data. Everything lives under one directory
(`~/.runspector`, override with `RUNSPECTOR_HOME`) so that uninstalling is `rm -r`."""

from __future__ import annotations

import json
import logging
import os
import shutil
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


# ── the move from runcoach-ai (v0.4 and earlier) ────────────────────────────
#
# Until v0.5.0 this app was runcoach-ai: data in `~/.runcoach`, the database
# `runcoach.db`, the variables `RUNCOACH_*`. A new version that silently
# ignored them would start on an empty directory, which looks exactly like
# lost data. v0.5 read the old variables with a warning; since v0.6 they are
# only NAMED, never read. The data move stays: someone coming from v0.4
# straight to a later version still finds their data.

LEGACY_ENV_PREFIX = "RUNCOACH_"
LEGACY_HOME = ".runcoach"
LEGACY_DB = "runcoach.db"
#: SQLite finds a database's journal by its file name, so they move together.
DB_SIDECARS = ("", "-wal", "-shm", "-journal")


#: Set by v0.4 for its own card runs, never by the athlete: a leftover one says
#: nothing about this install, and RUNCOACH_DB would aim the MCP server at the
#: very database the move renames.
LEGACY_PLUMBING = ("RUNCOACH_DB", "RUNCOACH_UNATTENDED")


def legacy_env_left() -> list[str]:
    """The `RUNCOACH_X` variables still set (present and not empty, as `home()`
    reads a variable) - no longer read since v0.6, so every start names them
    with the new name to use. The app's own v0.4 plumbing is not the athlete's
    and is not named."""
    return [name for name in sorted(os.environ)
            if name.startswith(LEGACY_ENV_PREFIX) and name not in LEGACY_PLUMBING
            and os.environ.get(name)]


def legacy_home() -> Path:
    return Path.home() / LEGACY_HOME


def legacy_program() -> str | None:
    """The old `runcoach` command, if it is still installed."""
    return shutil.which("runcoach")


def migrate_legacy_home() -> tuple[list[str], str | None]:
    """Move an old install's data to where this version looks for it.

    Returns (what was moved, why it refused). It refuses while a live old server
    holds the directory: renaming a database under a running process splits the
    data between the old place and the new. An EMPTY `RUNSPECTOR_HOME` counts as
    unset, as it does in `home()` - and did for `RUNCOACH_HOME` in v0.4."""
    done: list[str] = []
    root = Path(os.environ.get("RUNSPECTOR_HOME") or Path.home() / ".runspector")
    old, new = legacy_home(), Path.home() / ".runspector"
    move_dir = not os.environ.get("RUNSPECTOR_HOME") and old.is_dir() and not new.exists()
    old_db = (old if move_dir else root) / LEGACY_DB
    rename_db = old_db.is_file() and not (old_db.parent / "runspector.db").exists()
    # v0.4 takes no lock for `runcoach mcp` or a `runcoach sync` job: while it is
    # installed, the next Claude session or cron run recreates the old place,
    # empty, and the chat and the app coach on different data from then on.
    if (move_dir or rename_db) and (program := legacy_program()):
        return done, _still_installed(program)
    if move_dir:
        if (held := _live_lock(old)) is not None:
            return done, _in_use(held)
        try:
            old.rename(new)
        except OSError as exc:
            return done, f"could not move {display_path(old)} to {display_path(new)}: {exc}"
        done.append(f"{display_path(old)} -> {display_path(new)}")
    old_db, new_db = root / LEGACY_DB, root / "runspector.db"
    if old_db.is_file() and not new_db.exists():
        if (held := _live_lock(root)) is not None:
            return done, _in_use(held)
        try:
            # The journals first, the database last: as long as `runcoach.db`
            # is there, the move is unfinished and the next start completes it.
            # Renamed the other way round, a journal that could not move would be
            # left behind with nothing to say it belongs to the new name.
            for suffix in DB_SIDECARS[1:]:
                if (root / f"{LEGACY_DB}{suffix}").exists():
                    (root / f"{LEGACY_DB}{suffix}").replace(root / f"runspector.db{suffix}")
            old_db.rename(new_db)
        except OSError as exc:
            return done, f"could not rename {display_path(old_db)}: {exc}"
        done.append(f"{display_path(old_db)} -> {display_path(new_db)}")
    return done, None


def _live_lock(directory: Path) -> Path | None:
    """The `serve.lock` of a running old server in `directory`, if there is one -
    its demo instance included, which keeps its own home in `demo/`."""
    for lock in (directory / "serve.lock", directory / "demo" / "serve.lock"):
        if lock_held(lock):
            return lock
    return None


def _still_installed(program: str) -> str:
    return (f"runcoach-ai is still installed ({program}). It would keep writing to the old "
            f"place, so nothing is moved yet. Switch over first:\n"
            f"  uv tool uninstall runcoach-ai\n"
            f"  claude mcp list   # if runcoach is there: claude mcp remove runcoach, then\n"
            f"                    # claude mcp add runspector -- runspector mcp\n"
            f"  and in a Claude Desktop config or a cron job, `runcoach` becomes `runspector`.\n"
            f"Then start again - your data is moved on that start.")


def _in_use(lock: Path) -> str:
    return (f"{display_path(lock.parent)} is still used by a running runcoach server "
            f"(its lock: {display_path(lock)}).\n"
            f"Stop it (Ctrl-C in its window) and start again - your data is moved then. "
            f"If none runs, delete that lock file: a pid can be reused by an unrelated "
            f"program after a crash.")


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


#: How long a lock may sit before a live pid in it is presumed to be somebody
#: else's process. Long enough that a real, long-running server is never evicted
#: (the app is a personal tool that runs for a day at a time, not a service),
#: short enough that a crash on Monday does not block Tuesday.
LOCK_MAX_AGE_S = 36 * 3600


def lock_looks_recycled(parts: list[str]) -> bool:
    """Is this lock too old for its pid to still mean what it says?"""
    if len(parts) < 2:
        return False                    # no timestamp: trust the pid, as before
    try:
        taken = datetime.fromisoformat(parts[1])
    except ValueError:
        return False
    age = (datetime.now().astimezone() - taken).total_seconds()
    return age > LOCK_MAX_AGE_S


def pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True          # exists, owned by someone else
        return True
    import ctypes

    # SYNCHRONIZE (0x00100000) is the least privilege that still opens a handle
    # to a foreign process; PROCESS_QUERY_INFORMATION is denied across accounts.
    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def lock_held(lock: Path) -> bool:
    """Does a live process own this `serve.lock`? A lock left by a crash, or by a
    terminal window closed under the server, names a dead or recycled pid."""
    try:
        parts = lock.read_text(encoding="utf-8").split()
        pid = int(parts[0])
    except (OSError, ValueError, IndexError):
        return False                     # missing or garbled: `serve` takes it over too
    return pid != os.getpid() and pid_alive(pid) and not lock_looks_recycled(parts)


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
