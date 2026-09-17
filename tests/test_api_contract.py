"""Contract test against the REAL `garminconnect` API surface.

`garmin._safe()` swallows soft failures on purpose — a missing field must not lose
a whole day. It also catches `AttributeError`, though, and that is the hole this
file closes: if upstream renames an endpoint, every call through `_safe` degrades
to one log line, the column goes permanently NULL, and every other test in this
suite stays green, because they all drive *fake* clients.

So this test does the one thing the fake clients cannot: it asks the installed
`garminconnect.Garmin` class whether the methods `garmin.py` calls still exist,
and whether they still accept the arguments we pass.

The method list is derived by parsing `src/runcoach/garmin.py` with `ast` — a
hardcoded list would rot the moment someone adds a call. Nothing here touches the
network or needs credentials: the class is introspected, never instantiated.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from garminconnect import Garmin

GARMIN_PY = Path(__file__).resolve().parent.parent / "src" / "runcoach" / "garmin.py"

#: Sanity floor for the AST walk. If a refactor renames the `client` variable or
#: moves the calls behind a helper, the walk would find nothing and this file
#: would pass vacuously — the exact failure mode it exists to prevent.
MIN_EXPECTED_CALLS = 10


class CallSite:
    """One `client.<method>(...)` in garmin.py: what we call and how."""

    def __init__(self, node: ast.Call, method: str) -> None:
        self.method = method
        self.line = node.lineno
        self.positional = sum(1 for a in node.args if not isinstance(a, ast.Starred))
        self.keywords = frozenset(k.arg for k in node.keywords if k.arg is not None)
        # `f(*args)` / `f(**kwargs)` would make the arity unknowable from source.
        self.unpacks = (any(isinstance(a, ast.Starred) for a in node.args)
                        or any(k.arg is None for k in node.keywords))

    def __repr__(self) -> str:  # pragma: no cover - pytest ids only
        args = [f"<{self.positional} positional>"] + sorted(f"{k}=" for k in self.keywords)
        return f"garmin.py:{self.line} client.{self.method}({', '.join(args)})"


def _call_sites() -> list[CallSite]:
    """Every `client.<name>(...)` call in garmin.py, found by parsing the source.

    `client` is the name the module uses for the garminconnect handle throughout
    (including inside the deferred `lambda`s handed to `_safe`), so attribute
    calls on it are exactly the endpoints we depend on."""
    tree = ast.parse(GARMIN_PY.read_text(encoding="utf-8"), filename=str(GARMIN_PY))
    sites = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "client"):
            sites.append(CallSite(node, node.func.attr))
    return sorted(sites, key=lambda s: (s.method, s.line))


CALL_SITES = _call_sites()


def test_ast_walk_still_finds_the_calls():
    """Guard against a silently empty contract: if the walk stops matching, this
    file must go red rather than pass by finding nothing to check."""
    assert len(CALL_SITES) >= MIN_EXPECTED_CALLS, (
        f"only {len(CALL_SITES)} client.* call(s) parsed out of {GARMIN_PY.name} - the walk "
        f"is probably broken (renamed variable? calls moved behind a helper?). Fix the walk "
        f"or lower MIN_EXPECTED_CALLS deliberately; do not leave it passing on nothing."
    )


def test_every_called_endpoint_exists_on_the_real_client():
    """The renamed-endpoint alarm. `_safe` turns an `AttributeError` into a warning
    and a NULL column, so nothing else in this suite would notice."""
    missing = sorted({s.method for s in CALL_SITES if not hasattr(Garmin, s.method)})
    assert not missing, (
        f"garmin.py calls {len(missing)} method(s) that garminconnect "
        f"{_installed_version()} does not have: {', '.join(missing)}. "
        f"Upstream renamed or removed them; every value fetched through the affected "
        f"endpoint would silently become NULL (garmin._safe catches AttributeError)."
    )


@pytest.mark.parametrize("site", CALL_SITES, ids=repr)
def test_call_site_matches_the_real_signature(site: CallSite):
    """A method can survive a rename of its *parameters*. Bind our actual argument
    shape against the real signature so `latest=` or `maxchart=` disappearing is
    caught here instead of in a silently empty column."""
    if not hasattr(Garmin, site.method):
        pytest.skip("absence is reported by test_every_called_endpoint_exists_on_the_real_client")
    if site.unpacks:
        pytest.skip("call uses */** unpacking - arity is not knowable from the source")

    func = getattr(Garmin, site.method)
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):  # C-implemented or otherwise unintrospectable
        pytest.skip(f"{site.method} has no introspectable signature")

    # Unbound class attribute -> `self` is still in the signature; a sentinel fills
    # it and every other slot. Only names and arity are checked, never behaviour.
    args = ["<self>"] + ["<arg>"] * site.positional
    kwargs = dict.fromkeys(site.keywords, "<kwarg>")
    try:
        signature.bind(*args, **kwargs)
    except TypeError as exc:
        pytest.fail(
            f"{site!r} no longer fits garminconnect {_installed_version()}: "
            f"Garmin.{site.method}{signature} -> {exc}"
        )


def _installed_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("garminconnect")
    except PackageNotFoundError:  # pragma: no cover
        return "(version unknown)"
