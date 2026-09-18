"""Renders every tab of the real page in headless Chrome against a throw-away
demo server. Red on: an uncaught error in the console, the chassis' visible
"Page error" marker, or a page whose module never ran (`data-area` missing).

Unit tests cannot see a SyntaxError or ReferenceError in an inline module — the
page just stays blank. Skipped when no Chrome/Chromium is installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
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
pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome/Chromium found")


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


def render(url: str, profile: Path, *extra: str) -> tuple[str, str]:
    proc = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         f"--user-data-dir={profile}", "--enable-logging=stderr", "--v=0",
         "--window-size=430,1400", "--virtual-time-budget=8000", *extra, "--dump-dom", url],
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
