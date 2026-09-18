"""Regenerate the README screenshots from the demo data.

They were made by hand once and went stale within two review rounds: the Trend
chart's axis caption changed and the pictures still showed the old one, so a
reader who ran `runcoach serve --demo` saw different labels than the README.
A picture nobody can regenerate is documentation that can only rot.

    uv run python scripts/screenshots.py

Needs Chrome or Edge (the same lookup `tests/test_ui_smoke.py` uses) and writes
into `docs/screenshots/`. Everything it shows is synthetic: the demo generator
runs on a fixed seed, so the numbers are reproducible and contain no real
health data — which is also what keeps `scripts/pii_gate.py` green.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "docs" / "screenshots"
TABS = ("today", "runs", "trend", "coach")
#: Phone-ish width — the app is used on a phone in the kitchen, and a
#: desktop-width screenshot of a mobile-first layout advertises the wrong thing.
#: `main.app` is `max-width: 480px; margin: 0 auto`, so the WINDOW has to be a
#: little wider than that or the centred column is clipped on the right. 430
#: looked like the phone width the layout targets and cut every card in half.
SIZE = (520, 1500)
#: The cropped hero row in the README: the top of each tab, four abreast.
HERO = (520, 940)

#: The Coach tab's card, from `docs/screenshots/demo-card.json` — an ACTUAL
#: agent run against this same demo database, saved once and replayed here.
#:
#: It used to be a dict written by hand, while the README said "the coach card
#: is an unedited agent run on that data". It was not, and it showed: its
#: numbers contradicted `readme-today.png` in the same four-abreast row (Body
#: Battery 88 against 80, "five days since the last hard session" against
#: "2 d ago"). The one image whose job is to prove the AI half works was the one
#: piece of fiction on the page.
#:
#: Replayed rather than re-run, because taking a screenshot must not depend on a
#: model call, a subscription or the network — and because a card that changes on
#: every run makes the README churn. Regenerate it deliberately:
#:
#:     python scripts/screenshots.py --new-card
CARD_FILE = OUT / "demo-card.json"


def card_day() -> "date":
    """The day the stored coach card was generated for."""
    stored = json.loads(CARD_FILE.read_text(encoding="utf-8")).get("day")
    if not stored:
        sys.exit(f"{CARD_FILE.name} has no `day` - regenerate it with --new-card")
    return date.fromisoformat(str(stored))


def freeze_today(day: "date") -> None:
    """Pin the demo athlete's today, so the screenshots are REPRODUCIBLE.

    `demo.py` seeds relative to `paths.today()` and plans by weekday, so the
    synthetic athlete's numbers move with the day of the week. The coach card is
    a real agent run and is frozen on the day it was generated. Left alone, the
    two drift apart within 24 hours: a card citing "Body Battery 66, EASY" next
    to a Today tab showing 80 and a hard session - the exact contradiction that
    a hardcoded card produced, back through a slower door.

    Policing that drift was the first attempt, and it was the wrong shape: it
    made the repository expire, because the check had to compare against the
    real clock. Freezing the clock removes the drift instead of reporting it.
    Running `--new-card` moves both together."""
    from runcoach import paths

    paths.today = lambda: day


def load_card() -> dict:
    card = json.loads(CARD_FILE.read_text(encoding="utf-8"))
    card.pop("feedback", None)
    return card


def check_card(app, card: dict) -> None:
    """The card must agree with the app it is photographed next to.

    The date is guaranteed by `freeze_today`, but the DECISION is not: a change
    to `logic.decide_today` or to the demo generator can make the stored card
    say EASY where the app now says hard, and the README cites the card's
    numbers by name."""
    from runcoach import snapshot

    decision = snapshot.assemble(app.store)["decision_today"]["decision"]
    verdict = str(card.get("verdict") or "").lower()
    if decision not in verdict and decision[:4] not in verdict:
        sys.exit(f"{CARD_FILE.name} says {card.get('verdict')!r} while the app decides "
                 f"{decision!r} for the same day.\n"
                 f"Regenerate it:  python scripts/screenshots.py --new-card")


def new_card(app) -> dict:
    """Run the real agent against the demo data and keep the card."""
    from runcoach.web import agent, jobs

    tpl = next(t for t in json.loads(
        (ROOT / "src" / "runcoach" / "templates.json").read_text(encoding="utf-8"))
        if t["id"] == "train-today")
    job = jobs.new_job(tpl["prompt"], title=tpl["title"], kind="train-today")
    print("  running a real agent job (this costs a few minutes and some quota)...")
    out = agent.run(job, db=app.db_path, snapshot_meta={"data_through": "demo"})
    path = jobs.card_path(job["id"])
    if out["status"] != "done" or not path.is_file():
        sys.exit(f"the agent run did not produce a card: {out.get('result_summary')}")
    CARD_FILE.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"  saved {CARD_FILE}")
    return load_card()


def chrome() -> str:
    for name in ("chrome", "chromium", "google-chrome", "msedge"):
        if found := shutil.which(name):
            return found
    candidates = [
        Path(os.environ.get(var, "")) / sub
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
        for sub in ("Google/Chrome/Application/chrome.exe",
                    "Microsoft/Edge/Application/msedge.exe")
        if os.environ.get(var)
    ] + [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    for p in candidates:
        if p.is_file():
            return str(p)
    sys.exit("no Chrome or Edge found - install one, or take the screenshots by hand")


def shoot(binary: str, url: str, out: Path, size: tuple[int, int], profile: Path,
          *, dark: bool) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        binary, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--hide-scrollbars",
        f"--user-data-dir={profile}",
        f"--window-size={size[0]},{size[1]}",
        # The page itself has no theme switch we can drive from the command
        # line, so the SYSTEM preference is what we emulate — which is exactly
        # the path a first-time visitor takes (no stored choice yet).
        *(["--force-dark-mode", "--enable-features=WebContentsForceDark"] if dark else []),
        "--virtual-time-budget=9000",
        f"--screenshot={out}", url,
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if not out.is_file():
        sys.exit(f"Chrome wrote no file for {url}:\n{proc.stderr[-2000:]}")


def main() -> int:
    from runcoach.web import jobs, server

    binary = chrome()
    home = Path(tempfile.mkdtemp(prefix="runcoach-shots-"))
    os.environ["RUNCOACH_HOME"] = str(home)
    # Seed the demo athlete on the CARD's day, unless we are about to replace
    # the card - then the real today is the new anchor for both.
    if "--new-card" not in sys.argv:
        freeze_today(card_day())
    app = server.App(demo=True, token=None)          # moves RUNCOACH_HOME to <home>/demo
    card = new_card(app) if "--new-card" in sys.argv else load_card()
    check_card(app, card)
    jobs.write_card("j-train-today-demo", card)

    httpd = server.make_server("127.0.0.1", 0, app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"demo server on {base} - data in {home}")

    profile = home / "chrome"
    try:
        for tab in TABS:
            for dark, suffix in ((True, ""), (False, "-light")):
                shoot(binary, f"{base}#{tab}", OUT / f"{tab}{suffix}.png", SIZE,
                      profile, dark=dark)
                print(f"  {tab}{suffix}.png")
            # The README row is the same page, cropped to the fold.
            shoot(binary, f"{base}#{tab}", OUT / f"readme-{tab}.png", HERO,
                  profile, dark=(tab != "trend"))
            print(f"  readme-{tab}.png")
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(home, ignore_errors=True)

    print(json.dumps({"written": sorted(p.name for p in OUT.glob("*.png"))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
