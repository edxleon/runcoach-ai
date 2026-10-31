"""The move from runcoach-ai: an install of v0.4 or earlier keeps its data.

Its data sits in `~/.runcoach`, its database is `runcoach.db`, its settings are
`RUNCOACH_*`. A v0.5 that ignored any of them would start empty - which looks,
to the athlete, exactly like the app having lost a year of their training."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from runspector import cli, paths


@pytest.fixture
def user_home(tmp_path, monkeypatch) -> Path:
    """A home directory with no RUNSPECTOR_HOME: the default locations apply."""
    fake = tmp_path / "user"
    fake.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake)
    monkeypatch.delenv("RUNSPECTOR_HOME")
    return fake


def _old_install(home: Path) -> Path:
    old = home / ".runcoach"
    (old / "garmin").mkdir(parents=True)
    (old / "garmin" / "garmin_tokens.json").write_text("{}", encoding="utf-8")
    (old / "cards").mkdir()
    (old / "cards" / "c1.json").write_text("{}", encoding="utf-8")
    return old


def _live_lock(directory: Path) -> None:
    """A lock as a running v0.4 server writes it: a live pid (this test's
    parent, which is not us) and the time it was taken."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "serve.lock").write_text(
        f"{os.getppid()} {datetime.now().astimezone().isoformat()}\n", encoding="utf-8")


def _dead_pid() -> int:
    child = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                           capture_output=True, text=True, check=True)
    return int(child.stdout)


def _db_with_unflushed_wal(directory: Path, name: str) -> None:
    """A database whose last write is still only in the WAL, the state a crash
    or a hard kill leaves behind: copied while the writer is still open."""
    scratch = directory.parent / "writer"
    scratch.mkdir()
    con = sqlite3.connect(scratch / "w.db")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES ('kept')")
    con.commit()
    for suffix in paths.DB_SIDECARS:
        if (scratch / f"w.db{suffix}").exists():
            shutil.copy(scratch / f"w.db{suffix}", directory / f"{name}{suffix}")
    con.close()
    assert (directory / f"{name}-wal").exists(), "the test needs a WAL to move"


def test_an_old_install_moves_with_everything_in_it(user_home):
    old = _old_install(user_home)
    _db_with_unflushed_wal(old, "runcoach.db")

    moved, refused = paths.migrate_legacy_home()

    new = user_home / ".runspector"
    assert refused is None and len(moved) == 2
    assert not old.exists()
    assert (new / "garmin" / "garmin_tokens.json").exists()
    assert (new / "cards" / "c1.json").exists()
    assert not (new / "runcoach.db").exists()
    con = sqlite3.connect(new / "runspector.db")
    assert con.execute("SELECT v FROM t").fetchall() == [("kept",)], \
        "the rows still only in the WAL came along"
    con.close()


def test_an_empty_old_home_variable_means_the_default_as_it_did(user_home, monkeypatch):
    """v0.4 read `RUNCOACH_HOME=` (set, but empty) as the default directory; an
    env file or a launchd entry may well carry it."""
    old = _old_install(user_home)
    monkeypatch.setenv("RUNCOACH_HOME", "")

    moved, refused = paths.migrate_legacy_home()

    assert refused is None and moved
    assert (user_home / ".runspector" / "cards" / "c1.json").exists() and not old.exists()


def test_a_move_that_stops_half_way_is_finished_by_the_next_start(user_home, monkeypatch):
    """A journal that cannot be renamed (a scanner holding it on Windows) must
    not leave the database renamed without it: the next start would see nothing
    to do, and the rows only in the WAL would be gone."""
    old = _old_install(user_home)
    _db_with_unflushed_wal(old, "runcoach.db")
    failed = []

    def flaky(original):
        def move(self, target):
            if self.name == "runcoach.db-wal" and not failed:
                failed.append(self)
                raise PermissionError("held by another process")
            return original(self, target)
        return move

    monkeypatch.setattr(Path, "rename", flaky(Path.rename))
    monkeypatch.setattr(Path, "replace", flaky(Path.replace))

    _moved, refused = paths.migrate_legacy_home()
    assert failed and refused, "the first start says it could not finish"
    _moved, refused = paths.migrate_legacy_home()
    assert refused is None

    con = sqlite3.connect(user_home / ".runspector" / "runspector.db")
    assert con.execute("SELECT v FROM t").fetchall() == [("kept",)]
    con.close()


def test_nothing_moves_while_the_old_program_is_still_installed(user_home, monkeypatch):
    """v0.4 takes no lock for `runcoach mcp` or a `runcoach sync` job. Still
    installed, the next Claude session or cron run recreates ~/.runcoach, empty,
    and from then on the chat coaches on other data than the app."""
    old = _old_install(user_home)
    monkeypatch.setattr(paths, "legacy_program", lambda: "/usr/local/bin/runcoach")

    moved, refused = paths.migrate_legacy_home()

    assert moved == [] and old.exists() and not (user_home / ".runspector").exists()
    assert "uv tool uninstall runcoach-ai" in refused


def test_a_running_old_server_keeps_its_directory(user_home):
    old = _old_install(user_home)
    _live_lock(old)

    moved, refused = paths.migrate_legacy_home()

    assert moved == [] and "serve.lock" in refused
    assert old.exists() and not (user_home / ".runspector").exists()


def test_a_lock_left_by_a_closed_terminal_does_not_block_the_move(user_home):
    """Closing the window a v0.4 server ran in skips its cleanup and leaves the
    lock behind. That lock names a dead pid; the move must not wait for it."""
    old = _old_install(user_home)
    (old / "serve.lock").write_text(
        f"{_dead_pid()} {datetime.now().astimezone().isoformat()}\n", encoding="utf-8")

    _moved, refused = paths.migrate_legacy_home()

    assert refused is None and (user_home / ".runspector" / "cards" / "c1.json").exists()


def test_a_running_old_demo_server_keeps_the_directory_too(user_home):
    old = _old_install(user_home)
    _live_lock(old / "demo")

    moved, refused = paths.migrate_legacy_home()

    assert moved == [] and "demo" in refused and old.exists()


def test_the_refusal_stops_the_command_before_it_creates_an_empty_home(user_home, capsys):
    old = _old_install(user_home)
    _live_lock(old)

    assert cli.main(["doctor", "--offline"]) == 1
    assert "still used by a running runcoach server" in capsys.readouterr().err
    assert not (user_home / ".runspector").exists()


def test_an_existing_new_home_wins_and_the_old_one_is_left_alone(user_home):
    old = _old_install(user_home)
    (user_home / ".runspector").mkdir()

    assert paths.migrate_legacy_home() == ([], None)
    assert (old / "cards" / "c1.json").exists()


def test_doctor_names_an_old_directory_that_was_left_behind(user_home, capsys):
    _old_install(user_home)
    (user_home / ".runspector").mkdir()

    cli.main(["doctor", "--offline"])
    assert "the old data directory ~/.runcoach is still there" in capsys.readouterr().out


def test_doctor_does_not_advise_deleting_the_directory_that_holds_the_data(user_home, capsys):
    """An empty new home beside a full old one (a manual copy back, say): the
    app shows nothing, and "delete the old one" would delete everything."""
    old = _old_install(user_home)
    _db_with_unflushed_wal(old, "runcoach.db")
    (user_home / ".runspector").mkdir()

    cli.main(["doctor", "--offline"])
    out = capsys.readouterr().out
    assert "still holds a database" in out and "delete the old one" not in out


def test_a_home_of_its_own_gets_the_database_renamed(tmp_path, monkeypatch):
    """`RUNCOACH_HOME=/somewhere` becomes RUNSPECTOR_HOME and is used where it
    is; only the database inside carries the old name."""
    custom = tmp_path / "custom"
    custom.mkdir()
    _db_with_unflushed_wal(custom, "runcoach.db")
    monkeypatch.setenv("RUNSPECTOR_HOME", str(custom))

    moved, refused = paths.migrate_legacy_home()

    assert refused is None and moved
    assert (custom / "runspector.db").exists() and not (custom / "runcoach.db").exists()
    assert not (custom / "runcoach.db-wal").exists()


def test_a_refusal_names_the_way_out_when_no_server_runs(user_home):
    """A lock whose pid was recycled to an unrelated process looks held; the
    message must still say what to do when no old server is running."""
    old = _old_install(user_home)
    _live_lock(old)

    _moved, refused = paths.migrate_legacy_home()

    assert "if none runs, delete" in refused.lower()


def test_doctor_names_the_old_program_while_it_is_installed(capsys, monkeypatch):
    monkeypatch.setattr(paths, "legacy_program", lambda: "/usr/local/bin/runcoach")

    cli.main(["doctor", "--offline"])
    assert "runcoach-ai is still installed" in capsys.readouterr().out


# ── v0.6: the old variable names are no longer read ──────────────────────────

def test_old_variables_are_no_longer_read_and_the_start_says_so(capsys, monkeypatch):
    """v0.5 read every RUNCOACH_X as RUNSPECTOR_X, with a warning that this
    ends in v0.6. It ends: the old name is not read, and every start names it
    with the new one to use."""
    monkeypatch.setenv("RUNCOACH_MODEL", "opus")
    monkeypatch.delenv("RUNSPECTOR_MODEL", raising=False)

    cli.main(["doctor", "--offline"])

    assert "RUNSPECTOR_MODEL" not in os.environ
    err = capsys.readouterr().err
    assert "RUNCOACH_MODEL is no longer read" in err and "RUNSPECTOR_MODEL" in err


def test_an_old_data_folder_variable_stops_the_start_instead_of_an_empty_app(capsys, monkeypatch,
                                                                            tmp_path):
    """Ignored in silence, `RUNCOACH_HOME=/data/rc` would start the app on an
    empty ~/.runspector - which looks exactly like lost data. It stops with
    the rename to make, until the athlete says which folder they mean."""
    monkeypatch.setenv("RUNCOACH_HOME", str(tmp_path / "rc"))
    monkeypatch.delenv("RUNSPECTOR_HOME", raising=False)

    assert cli.main(["doctor", "--offline"]) == 2
    err = capsys.readouterr().err
    assert "RUNCOACH_HOME" in err and "RUNSPECTOR_HOME" in err

    # With the new name set the old one is only named, and the start goes on.
    monkeypatch.setenv("RUNSPECTOR_HOME", str(tmp_path / "rs"))
    cli.main(["doctor", "--offline"])
    err = capsys.readouterr().err
    assert "RUNCOACH_HOME is no longer read" in err and "Not starting" not in err


def test_the_apps_own_old_plumbing_and_empty_old_names_are_not_mentioned(capsys, monkeypatch):
    """RUNCOACH_DB and RUNCOACH_UNATTENDED were set by v0.4 for its own card
    runs, not by the athlete; an empty variable is not set."""
    monkeypatch.setenv("RUNCOACH_DB", "/somewhere/runcoach.db")
    monkeypatch.setenv("RUNCOACH_UNATTENDED", "1")
    monkeypatch.setenv("RUNCOACH_HOME", "")
    assert paths.legacy_env_left() == []
