"""The move from runcoach-ai: an install of v0.4 or earlier keeps its data.

Its data sits in `~/.runcoach`, its database is `runcoach.db`, its settings are
`RUNCOACH_*`. A v0.5 that ignored any of them would start empty - which looks,
to the athlete, exactly like the app having lost a year of their training."""

from __future__ import annotations

import os
import shutil
import sqlite3
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


def test_a_running_old_server_keeps_its_directory(user_home):
    old = _old_install(user_home)
    (old / "serve.lock").write_text("123 2026-10-30T10:00:00+01:00\n", encoding="utf-8")

    moved, refused = paths.migrate_legacy_home()

    assert moved == [] and "serve.lock" in refused
    assert old.exists() and not (user_home / ".runspector").exists()


def test_the_refusal_stops_the_command_before_it_creates_an_empty_home(user_home, capsys):
    old = _old_install(user_home)
    (old / "serve.lock").write_text("123\n", encoding="utf-8")

    assert cli.main(["doctor", "--offline"]) == 1
    assert "still held" in capsys.readouterr().err
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


def test_old_variables_are_read_until_they_are_renamed(monkeypatch):
    monkeypatch.setenv("RUNCOACH_MODEL", "opus")
    monkeypatch.setenv("RUNCOACH_TZ", "America/New_York")    # RUNSPECTOR_TZ is set
    monkeypatch.setenv("RUNSPECTOR_MODEL", "placeholder")    # registered for the undo
    monkeypatch.delenv("RUNSPECTOR_MODEL")

    assert paths.adopt_legacy_env() == ["RUNCOACH_MODEL"]
    assert os.environ["RUNSPECTOR_MODEL"] == "opus"
    assert os.environ["RUNSPECTOR_TZ"] == "Europe/Berlin", "a new name set itself wins"
