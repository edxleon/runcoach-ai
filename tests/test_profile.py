"""The athlete's own settings (`profile.py`): the week they set on the You tab
and their note to the coach. Validated once, for the web route and the CLI."""

from __future__ import annotations

import json
import os

import pytest

from runcoach import paths, profile


def test_a_setting_is_saved_and_none_hands_it_back_to_the_history():
    paths.profile_path().write_text(json.dumps({"max_hr": 190}), encoding="utf-8")
    saved = profile.save({"long_run_day": "sun", "off_days": ["thu", "mon", "thu"]})
    assert saved == {"max_hr": 190, "long_run_day": "sun", "off_days": ["mon", "thu"]}
    assert profile.load() == saved
    assert profile.save({"long_run_day": None}) == {"max_hr": 190, "off_days": ["mon", "thu"]}


@pytest.mark.skipif(os.name == "nt", reason="no POSIX modes on Windows")
def test_the_profile_is_owner_only():
    profile.save({"days_per_week": 3})
    assert paths.profile_path().stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("patch, says", [
    ({}, "nothing to save"),
    ({"max_hr": 200}, "unknown setting"),
    ({"days_per_week": 1}, "2 to 7"),
    ({"days_per_week": True}, "2 to 7"),
    ({"days_per_week": "4"}, "2 to 7"),
    ({"long_run_day": "sunday"}, "long_run_day"),
    ({"run_days": "mon"}, "list of weekdays"),
    ({"run_days": ["mon", "funday"]}, "list of weekdays"),
    ({"off_days": ["mon", "tue", "wed", "thu", "fri", "sat"]}, "at most 5"),
    ({"long_run_day": "sat", "off_days": ["sat"]}, "long run cannot be on a day off"),
    ({"days_per_week": 6, "off_days": ["mon", "wed", "fri"]}, "6 runs a week do not fit"),
    ({"note": 42}, "must be text"),
    ({"note": "x" * 601}, "too long"),
])
def test_a_value_the_planner_cannot_use_never_gets_in(patch, says):
    with pytest.raises(ValueError, match=says):
        profile.save(patch)
    assert profile.load() == {}


def test_a_day_off_on_the_saved_long_run_day_is_refused():
    profile.save({"long_run_day": "sat"})
    with pytest.raises(ValueError, match="long run cannot be on a day off"):
        profile.save({"off_days": ["sat"]})


def test_the_note_is_kept_as_written_minus_control_characters():
    profile.save({"note": "  Gym Mon, Wed, Fri.\r\nLess time\x07 until December.  "})
    assert profile.load()["note"] == "Gym Mon, Wed, Fri.\nLess time until December."
    assert "note" not in profile.save({"note": "   "})


def test_the_cli_refuses_a_long_run_on_a_day_off_and_keeps_the_rest():
    from runcoach import cli

    profile.save({"off_days": ["sat"]})
    assert cli.main(["profile", "--long-run-day", "sat"]) == 2
    assert profile.load() == {"off_days": ["sat"]}
    assert cli.main(["profile", "--long-run-day", "sun", "--goal", "sub-50 10k"]) == 0
    assert profile.load() == {"off_days": ["sat"], "goal": "sub-50 10k", "long_run_day": "sun"}
    if os.name != "nt":
        assert paths.profile_path().stat().st_mode & 0o077 == 0       # the CLI keeps it owner-only


def test_a_day_off_leaves_the_athletes_own_running_days():
    """The overlap is resolved where it is saved, not by the page: the editor
    then never has to rewrite a setting the athlete did not touch."""
    profile.save({"run_days": ["tue", "thu", "sat"]})
    assert profile.save({"off_days": ["thu"]})["run_days"] == ["tue", "sat"]
    assert profile.save({"run_days": ["mon", "thu"], "off_days": ["thu"]})["run_days"] == ["mon"]
