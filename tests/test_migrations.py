"""Schema migrations and the data-directory helpers."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from conftest import make_activity, make_day
from runcoach import paths
from runcoach.store import Store


def test_fresh_database_is_at_version_1(tmp_path):
    store = Store(tmp_path / "fresh.db")
    assert store.schema_version() == 1
    assert store.migrate() == 1                      # nothing pending


def test_opening_twice_is_idempotent_and_keeps_data(tmp_path):
    path = tmp_path / "t.db"
    Store(path).upsert_daily(make_day(date(2026, 6, 1), steps=100))
    again = Store(path)
    assert again.schema_version() == 1
    assert again.get_day(date(2026, 6, 1))["steps"] == 100


def test_schema_has_the_expected_tables_and_indexes(tmp_path):
    path = tmp_path / "t.db"
    Store(path)
    conn = sqlite3.connect(path)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'index') "
            "AND name NOT LIKE 'sqlite_%'")}
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()
    assert {"daily_metrics", "activities", "activity_splits", "scheduled_workouts",
            "activities_local_day_idx", "scheduled_workouts_day_idx"} <= names


def test_model_columns_exist_in_the_schema(tmp_path):
    """The dataclasses are the single source for INSERT column lists."""
    from runcoach.models import Activity, ActivitySplit, DailyMetrics

    path = tmp_path / "t.db"
    Store(path)
    conn = sqlite3.connect(path)
    try:
        def cols(table):
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}

        assert set(DailyMetrics.column_names()) <= cols("daily_metrics")
        assert set(Activity.column_names()) <= cols("activities")
        assert set(ActivitySplit.column_names()) == cols("activity_splits")
    finally:
        conn.close()


def test_migration_files_are_numbered_sql():
    files = sorted(p.name for p in (Path(paths.__file__).parent / "migrations").glob("*.sql"))
    assert files and files[0] == "0001_init.sql"
    assert all(name[:4].isdigit() and name[4] == "_" for name in files)


def test_default_store_path_is_under_runcoach_home(tmp_path):
    store = Store()
    assert store.path == str(tmp_path / "home" / "runcoach.db")
    assert Path(store.path).is_file()


def test_last_synced_at(store):
    assert store.last_synced_at() is None
    store.upsert_daily(make_day(date(2026, 6, 1), steps=1))
    first = store.last_synced_at()
    assert first and datetime.fromisoformat(first).tzinfo is not None
    store.upsert_activity(make_activity(1, date(2026, 6, 1)))
    assert store.last_synced_at() >= first


# ── paths ────────────────────────────────────────────────────────────────────

def test_home_and_subdirectories(tmp_path):
    assert paths.home() == tmp_path / "home" and paths.home().is_dir()
    assert paths.cards_dir().is_dir() and paths.jobs_dir().is_dir()
    assert paths.profile_path() == tmp_path / "home" / "profile.json"


def test_garmin_dir_default_and_override(tmp_path, monkeypatch):
    assert paths.garmin_dir() == tmp_path / "home" / "garmin"
    monkeypatch.setenv("RUNCOACH_GARMIN_TOKENS", str(tmp_path / "tokens"))
    assert paths.garmin_dir() == tmp_path / "tokens"


def test_local_day_uses_runcoach_tz(monkeypatch):
    late = datetime(2026, 6, 7, 23, 30, tzinfo=timezone.utc)
    assert paths.local_day(late) == date(2026, 6, 8)             # Europe/Berlin (autouse)
    monkeypatch.setenv("RUNCOACH_TZ", "UTC")
    assert paths.local_day(late) == date(2026, 6, 7)
    monkeypatch.delenv("RUNCOACH_TZ")
    assert paths.local_tz() is None                              # falls back to the system zone
    assert isinstance(paths.today(), date)
