"""The athlete's own settings (`profile.json`): what they told the app, which
beats what the app read from their history.

The You tab shows the week the planner goes by - days, weekdays, long run,
days off, another sport - filled from the history (`habits.py`) and kept up
to date by it. Whatever the athlete sets here wins until they hand it back
("use my history"); `None` in a patch removes the key, and the history takes
over again. Next to the week, a free-text note to the coach. Everything is
validated here, once, for the web route and the CLI alike: the file is read
by the planner on every proposal, so a value it cannot use must never get in.
"""

from __future__ import annotations

import json
import os
import re

from . import paths

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
#: The week settings, as the You tab edits them.
WEEK_KEYS = ("days_per_week", "run_days", "long_run_day", "off_days", "sport_days")
#: Two running days at least must stay free: a long run and one more.
MAX_OFF_DAYS = 5
#: The athlete's note to the coach: free text, read by the coach (never by a
#: rule) before every answer - "gym three times a week", "less time lately".
NOTE_KEY = "note"
NOTE_MAX = 600
#: How the coach treats the athlete's note - shared by the card prompt
#: (`web/agent.py`) and `get_athlete_profile`, so a card and the chat read
#: the same note the same way.
NOTE_RULE = ("The athlete wrote this for their coach: take it into account - their time, other "
             "sports, what they want - wherever the answer has room for it, and say so when you "
             "do. It does not change today's decision, the spacing or the load rules, and it never "
             "stands in for their yes before anything goes on the watch.")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def load() -> dict:
    try:
        data = json.loads(paths.profile_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _days(value, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(d, str) and d in WEEKDAYS for d in value):
        raise ValueError(f"{key} must be a list of weekdays ({', '.join(WEEKDAYS)})")
    return [d for d in WEEKDAYS if d in value]          # calendar order, no duplicates


def validate(patch: dict, current: dict) -> dict:
    """The profile after `patch`, or ValueError with a sentence for the page.
    Only the week keys are accepted; the other keys of `current` (max_hr,
    goal …) pass through untouched."""
    if not isinstance(patch, dict) or not patch:
        raise ValueError("nothing to save")
    unknown = sorted(set(patch) - set(WEEK_KEYS) - {NOTE_KEY})
    if unknown:
        raise ValueError(f"unknown setting: {', '.join(unknown)}")
    out = dict(current)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif key == NOTE_KEY:
            if not isinstance(value, str):
                raise ValueError("the note must be text")
            text = _CONTROL_RE.sub("", value.replace("\r\n", "\n")).strip()
            if len(text) > NOTE_MAX:
                raise ValueError(f"the note is too long (max {NOTE_MAX} characters)")
            if text:
                out[key] = text
            else:
                out.pop(key, None)
        elif key == "days_per_week":
            if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 7:
                raise ValueError("days_per_week must be a whole number from 2 to 7")
            out[key] = value
        elif key == "long_run_day":
            if value not in WEEKDAYS:
                raise ValueError(f"long_run_day must be one of {', '.join(WEEKDAYS)}")
            out[key] = value
        else:
            out[key] = _days(value, key)
    off = set(out.get("off_days") or ())
    if len(off) > MAX_OFF_DAYS:
        raise ValueError(f"at most {MAX_OFF_DAYS} days off - a week needs two running days")
    if out.get("long_run_day") in off:
        raise ValueError(f"the long run cannot be on a day off ({out['long_run_day']})")
    # A day off leaves the running days here, where it is saved - the page then
    # never has to rewrite a setting the athlete did not touch.
    if off and out.get("run_days"):
        out["run_days"] = [d for d in out["run_days"] if d not in off]
    if off and out.get("days_per_week", 0) > 7 - len(off):
        raise ValueError(f"{out['days_per_week']} runs a week do not fit next to {len(off)} "
                         f"days off - at most {7 - len(off)}")
    return out


def write(data: dict) -> None:
    """Atomically and owner-only - the note is the athlete's own words, and the
    web route and the CLI both write here."""
    path = paths.profile_path()
    paths.write_json_atomic(path, data)
    try:
        os.chmod(path, 0o600)
    except OSError:          # Windows: no POSIX modes, the home directory guards it
        pass


def save(patch: dict) -> dict:
    """Validate and write. Returns the new profile."""
    new = validate(patch, load())
    write(new)
    return new
