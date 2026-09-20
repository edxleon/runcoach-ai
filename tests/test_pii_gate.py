"""The release gate, tested — it had no tests at all.

`scripts/pii_gate.py` is the last thing between this repository and a public
profile, and `grep -rn pii tests/` came back empty: every line of it could be
deleted invisibly, including the binary scanning added to close a finding.

The gate was also built the wrong way round. Its extension list was an
ALLOW-list: a suffix in neither `TEXT_SUFFIXES` nor `BINARY_SUFFIXES` was
skipped in silence, so a `.csv` Garmin export — the single most likely real leak
in a project about Garmin data — passed with `clean`, along with `.ps1`,
`.ipynb`, `.xml`, `.ini`, `.log`, `.tsv` and `.pyi`. Its own docstring says a
false positive costs a minute and a leak cannot be taken back; an allow-list
trades those the wrong way round. These cases pin the inversion.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts" / "pii_gate.py"

#: One line that trips two different rules, so a case fails loudly either way.
#: Invented values, not anyone's: this file is itself scanned by the gate it
#: tests (it is in `SKIP_FILES` for exactly that reason), and a fixture that
#: contained a real address would make the guard's own test the leak.
LEAK = "contact a.person@mailhost.tld or look in D:/Users/someone/secret"

#: The extensions that used to walk straight through. Not a guess: this is the
#: list a reviewer got past the gate, plus the ones a Garmin workflow produces.
UNCOVERED_BEFORE = (".csv", ".ps1", ".ipynb", ".xml", ".ini", ".log", ".tsv",
                    ".pyi", ".fit", ".gpx", ".tcx", ".bak", ".env.example")


def run_gate(tree: Path) -> tuple[int, str]:
    out = subprocess.run([sys.executable, str(tree / "scripts" / "pii_gate.py")],
                         capture_output=True, text=True, timeout=120)
    return out.returncode, out.stdout


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A throw-away repo with just the gate in it, so a case cannot be masked by
    — or accidentally rewrite — the real tree."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "pii_gate.py").write_text(GATE.read_text(encoding="utf-8"),
                                                      encoding="utf-8")
    (tmp_path / "docs").mkdir()
    return tmp_path


def test_a_clean_tree_is_clean(tree):
    rc, out = run_gate(tree)
    assert rc == 0 and "clean" in out


@pytest.mark.parametrize("suffix", UNCOVERED_BEFORE)
def test_no_extension_walks_through_unscanned(tree, suffix):
    """The inversion, one extension at a time. A release gate may not decide
    that a file is safe because it does not recognise the name."""
    (tree / "docs" / f"export{suffix}").write_text(LEAK, encoding="utf-8")
    rc, out = run_gate(tree)
    assert rc == 1, f"{suffix} passed the gate"
    assert "e-mail address" in out and "home path" in out


def test_a_leak_inside_a_binary_is_found(tree):
    """A PNG `tEXt` chunk, an EXIF comment, or a text file saved with the wrong
    extension — that is what a leak looks like inside a binary. The gate could
    not read `.png` at all, which is the one asset class in this repo that could
    ever carry a picture of real health data."""
    png = b"\x89PNG\r\n\x1a\n" + b"tEXtComment\x00" + LEAK.encode() + b"\x00\xff\xfe"
    (tree / "docs" / "shot.png").write_bytes(png)
    rc, out = run_gate(tree)
    assert rc == 1 and "shot.png" in out


def test_compressed_pixels_do_not_raise_a_false_alarm(tree):
    """...and the counter-case, which decides whether the alarm is usable at
    all. Matching the raw bytes flagged every screenshot on the "German
    leftover" rule, because 0xE4 is also just a byte — an alarm that can never
    go green teaches the reader to skip the line where the real ones appear."""
    import random

    rnd = random.Random(7)
    noise = bytes(rnd.randrange(256) for _ in range(40000))
    (tree / "docs" / "pixels.png").write_bytes(b"\x89PNG\r\n\x1a\n" + noise)
    rc, out = run_gate(tree)
    assert rc == 0, f"random bytes raised: {out[:400]}"


def test_a_real_screenshot_from_this_repo_stays_clean(tree):
    """The gate runs against the shipped screenshots on every CI build. If
    scanning them were noisy, the gate would be turned off within a week."""
    shots = sorted((ROOT / "docs" / "screenshots").glob("*.png"))
    assert shots, "no screenshots to check"
    (tree / "docs" / "real.png").write_bytes(shots[0].read_bytes())
    rc, out = run_gate(tree)
    assert rc == 0, out[:400]


def test_the_private_term_hook_still_works(tree, monkeypatch):
    """`RUNCOACH_PII_EXTRA` adds names without committing them — the mechanism
    that lets this repo be checked for a person's actual name."""
    (tree / "docs" / "notes.md").write_text("a note about Hauptbahnhof", encoding="utf-8")
    env = {**dict(__import__("os").environ), "RUNCOACH_PII_EXTRA": "Hauptbahnhof"}
    out = subprocess.run([sys.executable, str(tree / "scripts" / "pii_gate.py")],
                         capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 1 and "private term" in out.stdout


def test_the_gate_skips_its_own_patterns_and_the_usual_noise(tree):
    """`pii_gate.py` contains every pattern it looks for, and `.venv` contains
    half the internet. Both are excluded by name — if that stopped working the
    gate would fail on itself and be ignored from then on."""
    venv = tree / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "somepackage.py").write_text(LEAK, encoding="utf-8")
    rc, _ = run_gate(tree)
    assert rc == 0


def test_an_unknown_extension_holding_binary_data_is_still_scanned(tree):
    """The two halves of the rule, separated. A `.png` is caught by the suffix
    list; a `.fit` (Garmin's own workout format, and not on any list) has to be
    caught by the UTF-8 sniff — "I do not recognise this" is a reason to look
    harder, not to look away."""
    blob = b"\x0e\x10\x00\x00.FIT" + b"\x00\xff" * 20 + LEAK.encode() + b"\xfe\xff" * 20
    (tree / "docs" / "activity.fit").write_bytes(blob)
    rc, out = run_gate(tree)
    assert rc == 1 and "activity.fit" in out


def test_the_suffix_list_is_an_optimisation_not_the_boundary(tree):
    """Stated in the gate's own comment, so it should be true: emptying
    `BINARY_SUFFIXES` must not let anything through, because the sniff catches
    what the list would have."""
    gate = tree / "scripts" / "pii_gate.py"
    src = re.sub(r"BINARY_SUFFIXES = \{[^}]*\}", "BINARY_SUFFIXES = set()",
                 gate.read_text(encoding="utf-8"))
    gate.write_text(src, encoding="utf-8")
    png = b"\x89PNG\r\n\x1a\ntEXtComment\x00" + LEAK.encode() + b"\x00\xff\xfe"
    (tree / "docs" / "shot.png").write_bytes(png)
    rc, out = run_gate(tree)
    assert rc == 1 and "shot.png" in out


def test_a_citation_is_not_a_leak(tree):
    """`zones.md` cites its sources, and two of them tripped the gate: the ten
    digits inside `mss.0b013e3180304570` read as a chat id, and the umlaut in
    Stöggl as a German leftover. Mangling an author's name or exempting the one
    file most likely to carry a personal HR default were both wrong answers.
    A line with a DOI on it is a citation; the identifier and the names beside
    it are public by definition."""
    (tree / "docs" / "zones.md").write_text(
        "| polarised | Stöggl & Sperlich 2014, doi:10.3389/fphys.2014.00033 |\n"
        "| intervals | Helgerud 2007, doi:10.1249/mss.0b013e3180304570 |\n",
        encoding="utf-8")
    rc, out = run_gate(tree)
    assert rc == 0, out


def test_the_citation_exemption_covers_only_the_citation(tree):
    """The counter-cases, so the exemption cannot quietly become a blanket:
    a chat id sitting NEXT to a DOI is still found, and the umlaut is still
    found on a line that has no DOI."""
    (tree / "docs" / "a.md").write_text(
        "| x | Someone 2020, doi:10.1000/xyz123 - ask user 700100200300 |\n", encoding="utf-8")
    rc, out = run_gate(tree)
    assert rc == 1 and "700100200300" in out, out

    (tree / "docs" / "a.md").write_text("Stöggl without a citation\n", encoding="utf-8")
    rc, out = run_gate(tree)
    assert rc == 1 and "German leftover" in out, out
