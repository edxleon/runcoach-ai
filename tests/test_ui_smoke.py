"""Renders every tab of the real page in headless Chrome against a throw-away
demo server. Red on: an uncaught error in the console, the chassis' visible
"Page error" marker, or a page whose module never ran (`data-area` missing).

Unit tests cannot see a SyntaxError or ReferenceError in an inline module — the
page just stays blank. Skipped when no Chrome/Chromium is installed, and on
macOS CI runners, where headless Chrome does not return at all (see
`_unusable_browser`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

TABS = ("today", "runs", "trend", "coach")


def _chrome() -> str | None:
    for name in ("chrome", "google-chrome", "chromium", "chromium-browser", "msedge"):
        if found := shutil.which(name):
            return found
    candidates = [
        Path(os.environ.get(var, "")) / sub
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
        for sub in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe")
        if os.environ.get(var)
    ] + [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    return next((str(p) for p in candidates if p.is_file()), None)


CHROME = _chrome()
def _unusable_browser() -> str | None:
    """Why this file cannot run here, or `None` if it can.

    GitHub's macOS runners ship Chrome but headless Chrome never returns on
    them: every `--dump-dom` call sat until the 90 s timeout, six in a row,
    while the identical commit passed on Linux and Windows. It is a property of
    the runner (no window server session, cold profile, no GPU), not of macOS -
    a developer on a real Mac still gets the coverage, because CI is unset
    there.

    Skipping is honest here and would not be for a logic test: what this file
    guards (an inline module that never ran, a CSP violation, a console error)
    is identical on all three systems, so Linux and Windows already answer the
    question. Pretending otherwise would buy a red badge nobody can act on."""
    if CHROME is None:
        return "no Chrome/Chromium found"
    if sys.platform == "darwin" and os.environ.get("CI"):
        return "headless Chrome does not return on GitHub's macOS runners"
    return None


pytestmark = pytest.mark.skipif(_unusable_browser() is not None,
                                reason=_unusable_browser() or "")


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    from runcoach.web import server

    old_home = os.environ.get("RUNCOACH_HOME")
    os.environ["RUNCOACH_HOME"] = str(tmp_path_factory.mktemp("home"))
    app = server.App(demo=True, token=None)
    httpd = server.make_server("127.0.0.1", 0, app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()
    httpd.server_close()
    if old_home is None:
        os.environ.pop("RUNCOACH_HOME", None)
    else:
        os.environ["RUNCOACH_HOME"] = old_home


def render(url: str, profile: Path, *extra: str, size: str = "430,1400") -> tuple[str, str]:
    proc = subprocess.run(
        # `--no-sandbox` and `--disable-dev-shm-usage`: the standard pair for a
        # containerised runner, where the sandbox has no user namespace and
        # /dev/shm is 64 MB. Harmless on a desktop.
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
         f"--user-data-dir={profile}", "--enable-logging=stderr", "--v=0",
         f"--window-size={size}", "--virtual-time-budget=8000", *extra, "--dump-dom", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    return proc.stdout, proc.stderr


@pytest.mark.parametrize("tab", TABS)
def test_tab_renders_without_errors(base_url, tmp_path, tab):
    dom, log = render(f"{base_url}#{tab}", tmp_path / "profile")
    # A Content-Security-Policy violation is reported to the console but is not an
    # "Uncaught" error, so it used to slip through: the pre-paint theme script was
    # blocked for a while and the only symptom was a white flash nobody measured.
    bad = ("Uncaught", "SyntaxError", "Content Security Policy", "Refused to")
    console_errors = [line for line in log.splitlines()
                      if "CONSOLE" in line and any(w in line for w in bad)]
    assert not console_errors, console_errors[:3]
    assert 'data-area="runcoach"' in dom, "the page module never ran"
    assert "Page error" not in dom
    assert "demo" in dom.lower()


def test_today_shows_the_verdict_and_decision(base_url, tmp_path):
    dom, _ = render(f"{base_url}#today", tmp_path / "profile")
    assert any(v in dom for v in ("GO", "EASY", "REST"))
    assert "hard session" in dom or "Easy" in dom   # the decision sentence from logic.decide_today


def test_the_coach_tab_offers_a_way_to_delete_a_card(base_url, tmp_path):
    """The README calls the delete button the exit from a card that steers its
    own successors — `cleanup_cards()` pins the newest card of each kind forever
    and feeds it back into the next run of that kind. The server route and the
    README sentence both existed for a full review round while the button did
    not, and the only remedy was to find `~/.runcoach/cards/` in a file manager.

    This renders the real page, so a renamed CSS class or a handler that never
    binds shows up here and not in a unit test asserting a string."""
    from runcoach.web import jobs

    jobs.write_card("j-train-today-2026-09-16", {
        "kind": "train-today", "headline": "Green light, intervals today",
        "bullets": ["HRV balanced, Body Battery 88.", "Five days since the last hard run."],
        "verdict": "GO", "generated_at": "2026-09-16T07:00:00+02:00"})

    dom, log = render(f"{base_url}#coach", tmp_path / "profile")
    assert "Green light, intervals today" in dom, "the seeded card is on the page"
    assert 'data-card-del="j-train-today-2026-09-16"' in dom, \
        "no delete affordance on the card the README promises one for"
    assert not [ln for ln in log.splitlines()
                if "CONSOLE" in ln and ("Uncaught" in ln or "SyntaxError" in ln)]


@pytest.fixture(scope="module")
def first_run_url(tmp_path_factory):
    """A server with NO demo data and no Garmin session — the state every
    stranger from GitHub meets first, and the one this harness could not reach:
    `base_url` runs `demo=True`, and demo short-circuits both predicates."""
    from runcoach.web import server

    old_home = os.environ.get("RUNCOACH_HOME")
    old_cmd = os.environ.get("RUNCOACH_CLAUDE_CMD")
    os.environ["RUNCOACH_HOME"] = str(tmp_path_factory.mktemp("first-run-home"))
    # Stub the CLI so `claude_available()` is TRUE here. Without it the coach
    # buttons are disabled because no `claude` is on PATH - which is the case on
    # every CI runner - and the assertion below would pass no matter what the
    # empty-database lock does. Measured: with the lock removed and no CLI, the
    # test stayed green.
    os.environ["RUNCOACH_CLAUDE_CMD"] = '["python", "-c", "pass"]'
    app = server.App(demo=False, token=None)
    httpd = server.make_server("127.0.0.1", 0, app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()
    httpd.server_close()
    for name, value in (("RUNCOACH_HOME", old_home), ("RUNCOACH_CLAUDE_CMD", old_cmd)):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def test_the_first_screen_guides_instead_of_reporting_an_error(first_run_url, tmp_path):
    """Before the repair this page showed `No call · too little data for a
    verdict` over a column of dashes, under a red banner naming a
    GarminConnectAuthenticationError. Nothing there was wrong, and all of it
    read as a broken app."""
    dom, log = render(first_run_url, tmp_path / "profile")
    console_errors = [ln for ln in log.splitlines() if "CONSOLE" in ln
                      and any(w in ln for w in ("Uncaught", "SyntaxError",
                                                "Content Security Policy", "Refused to"))]
    assert not console_errors, console_errors[:3]
    assert 'data-area="runcoach"' in dom, "the page module never ran"

    assert "runcoach login" in dom, "the first step is not on the page"
    assert "runcoach sync --days 30" in dom, "the depth `doctor` recommends is not offered"
    assert "GarminConnect" not in dom and "AuthenticationError" not in dom, \
        "an exception name is not a first impression"
    assert "too little data for a verdict" not in dom, "the guide replaces the verdict"
    # Every coach button locked: an analysis of an empty database is a real,
    # paid agent run that can only answer "there is nothing here".
    assert "<button" in dom and 'data-tpl' in dom
    for chunk in dom.split('data-tpl')[1:]:
        assert "disabled" in chunk[:200], "a coach button is live on an empty database"


def test_with_data_the_coach_buttons_are_live(base_url, tmp_path):
    """The counter-case to the one above, and the reason it means anything: if
    the buttons were disabled for some OTHER reason - no CLI on PATH, a job in
    flight - the empty-database assertion would hold whatever the lock does."""
    dom, _ = render(f"{base_url}#coach", tmp_path / "profile-live")
    chunks = dom.split('data-tpl')[1:]
    assert chunks, "no coach buttons rendered at all"
    assert any("disabled" not in c[:200] for c in chunks), (
        "every button disabled although the demo store is full - the lock is untestable")


def test_the_desktop_layout_keeps_the_reload_button_reachable(base_url, tmp_path):
    """At 1280 px the page used to be a 480 px phone column with the tab bar
    stretched across the bottom. The tab bar now sits under the header — and the
    header STAYS sticky, because it carries ↻, the only reload handle on a
    desktop (pull-to-refresh is touch-only) and the one the first-run guide
    points at."""
    dom, log = render(f"{base_url}#today", tmp_path / "profile-wide", size="1280,900")
    console_errors = [ln for ln in log.splitlines() if "CONSOLE" in ln
                      and any(w in ln for w in ("Uncaught", "SyntaxError",
                                                "Content Security Policy", "Refused to"))]
    assert not console_errors, console_errors[:3]
    assert 'data-area="runcoach"' in dom, "the page module never ran at desktop width"
    assert 'id="refresh"' in dom

    css = (Path(server_static()) / "tokens.css").read_text(encoding="utf-8")
    wide = css.split("@media (min-width: 900px)", 1)
    assert len(wide) == 2, "the desktop block is gone"
    block = wide[1].split("\n}\n", 1)[0]
    assert "position: static" not in block, \
        "a static header takes ↻ out of reach after the first scroll"
    assert "var(--header-h)" in block, \
        "the tab bar must stick below the DECLARED header height, not a guessed number"


def server_static() -> str:
    from runcoach.web import server as _s

    return str(Path(_s.__file__).resolve().parent / "static")
