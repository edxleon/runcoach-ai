"""The vendor's API surface, pinned - and the double checked against it.

Every test in this repository that exercises the write path runs against
`conftest.FakeGarmin`. That is the right trade (nobody's Garmin account should
be a test fixture), and it has one hole: NOTHING said the double still looks
like the library. A renamed method, a parameter that became keyword-only, an
endpoint the vendor dropped - the suite stays green through all of it and the
first person to find out is the athlete whose session did not arrive.

So this file asks three questions, all of them offline:

1. Does the real `garminconnect.Garmin` still have every method this app calls?
2. Does it still accept the arguments this app passes?
3. Does the double offer the same set, so a test can never pass on a method
   production does not have - or miss one production does?

`garminconnect` is a pinned dependency and is imported here directly; no
network, no account.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from conftest import FakeGarmin

garminconnect = pytest.importorskip("garminconnect")

SRC = Path(__file__).resolve().parent.parent / "src" / "runcoach"

#: How this app calls the vendor's write methods: (positional args, keyword
#: args) at each call site. Checked against the real signature by binding.
WRITE_CALLS = {
    "upload_running_workout": ((object(),), {}),
    "schedule_workout": ((123, "2026-09-24"), {}),
    "unschedule_workout": ((456,), {}),
    "push_workout_to_device": ((), {"workout_id": 123}),
    "get_workout_by_id": ((123,), {}),
}


def _client_methods() -> set[str]:
    """Every attribute this app reads off a Garmin client, from the source.

    Read with the AST rather than a regex: a call the parser cannot see is a
    call this test cannot pin, and silently missing one is the failure mode
    the whole file is about."""
    names: set[str] = set()
    for path in (SRC / "garmin.py", SRC / "auth.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "client"):
                names.add(node.attr)
    return names


def test_the_app_reads_a_meaningful_number_of_client_methods():
    """A guard on the guard: if the AST walk stops finding calls - the variable
    is renamed, the module is split - the two tests below would pass by
    checking nothing at all."""
    found = _client_methods()
    assert len(found) >= 15, f"only found {sorted(found)} - is the walk still looking in the right place?"
    assert "upload_running_workout" in found and "get_activities" in found


def test_every_client_method_this_app_calls_still_exists_in_the_library():
    missing = sorted(n for n in _client_methods() if not hasattr(garminconnect.Garmin, n))
    assert not missing, (
        f"the pinned garminconnect no longer offers: {missing}. The write path would fail at "
        f"runtime with AttributeError, and every test here would stay green because the double "
        f"still has them.")


@pytest.mark.parametrize("name", sorted(WRITE_CALLS))
def test_the_library_still_accepts_the_arguments_the_write_path_passes(name):
    """Binding, not comparing: the point is whether OUR call would go through,
    not whether the signature is byte-identical to what it was."""
    args, kwargs = WRITE_CALLS[name]
    sig = inspect.signature(getattr(garminconnect.Garmin, name))
    sig.bind(object(), *args, **kwargs)          # `self` plus what we pass


def test_the_double_offers_exactly_what_the_app_asks_of_a_client():
    """Both directions. A method the double lacks turns a covered path into an
    AttributeError the moment a test reaches it; a method it has and the app
    never calls is a fixture pretending to cover something."""
    used, fake = _client_methods(), set(dir(FakeGarmin))
    # `login` is called on the client inside our own `login()`; the double is
    # handed to the code already "logged in", so it does not need one.
    expected = used - {"login"}
    missing = sorted(expected - fake)
    assert not missing, f"FakeGarmin does not implement: {missing}"

    # What the double offers beyond that has to earn its place: `delete_workout`
    # exists so a test can prove NOTHING ever calls it (there is no delete path).
    extra = sorted(n for n in fake - expected
                   if not n.startswith("_") and callable(getattr(FakeGarmin, n, None))
                   and n not in {"upload_workout", "delete_workout"})
    assert not extra, f"FakeGarmin offers methods the app never calls: {extra}"
