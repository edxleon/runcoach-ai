"""Release gate: fail if anything personal or monorepo-internal leaked into the
tree. Runs in CI and before every publish. Patterns are deliberately blunt —
a false positive costs a minute, a leak cannot be taken back.

    python scripts/pii_gate.py            # exit 0 = clean
    RUNCOACH_PII_EXTRA="name1|name2" ...  # add private terms without committing them
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "dist", ".ruff_cache"}
#: `pii_gate.py` contains every pattern it looks for, and `test_pii_gate.py`
#: contains the sample leaks it is checked against - both would fail on
#: themselves forever, which is how a gate gets switched off.
SKIP_FILES = {"pii_gate.py", "test_pii_gate.py", "uv.lock", "last-run.json"}
#: EVERYTHING is scanned. There is no allow-list of "text" extensions any more.
#:
#: There was, and it was the wrong default for a release gate: a suffix in
#: neither list was skipped in silence, so a `.csv` Garmin export - the single
#: most likely real leak in this project - passed with `clean`, along with
#: `.ps1`, `.ipynb`, `.xml`, `.ini`, `.log`, `.tsv` and `.pyi`. The gate's own
#: docstring says a false positive costs a minute and a leak cannot be taken
#: back; an allow-list trades those the wrong way round.
#:
#: Files that are not UTF-8 are read as bytes and searched for printable ASCII
#: runs, which is how a leak looks inside a binary.

#: Known binaries, scanned for READABLE STRINGS rather than as text. The
#: screenshots are the only files here that could ever carry a picture of real
#: HRV, sleep, resting-HR or workout names — and the gate that exists to prevent
#: exactly that could not read them at all: a `.png` holding the same address a
#: `.txt` was rejected for came back clean. Anything else that fails to decode
#: as UTF-8 is treated the same way, so this list is an optimisation, not the
#: boundary.
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".db", ".ico",
                   ".woff", ".woff2", ".zip", ".gz"}

#: Printable ASCII runs of at least this length, which is what metadata looks
#: like and what compressed pixels are not.
_STRINGS = re.compile(r"[ -~]{6,}")

#: ...and only the patterns that need several characters to match.
BINARY_PATTERNS = ("e-mail address", "home path", "IBAN",
                   "private term (RUNCOACH_PII_EXTRA)")

PATTERNS = {
    "e-mail address": re.compile(r"[\w.+-]+@(?!example\.|users\.noreply\.|anthropic\.)[\w-]+\.[a-z]{2,}",
            re.I),
    "home path": re.compile(r"[A-Z]:[\\/]+Users[\\/]+\w+|/home/\w+/|/Users/\w+/", re.I),
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}(?: ?\d{4}){3,}"),
    "long numeric id (chat/user id?)": re.compile(r"(?<![\d.])-?\d{9,12}(?![\d.])"),
    "German leftover": re.compile(r"[äöüÄÖÜß]"),
}
# Long numbers that are legitimately part of the project (synthetic demo ids).
ALLOWED_NUMBERS = re.compile(r"9[_]?000[_]?000[_]?\d{3}|1234567890")

#: A DOI is a public identifier, and a line that carries one is a citation.
#: The identifier itself holds digit runs the chat-id rule reads as an id
#: (`mss.0b013e3180304570`), and the authors next to it carry umlauts the
#: German-leftover rule reads as a leak (Stöggl). Neither is one. The DOI
#: tokens are cut out before the line is scanned and the umlaut rule is off for
#: that line - everything else on it (an e-mail, a home path, a real chat id
#: outside the DOI) is still checked.
_DOI = re.compile(r"doi:10\.\d{4,}/\S+")


def extra() -> re.Pattern | None:
    terms = os.environ.get("RUNCOACH_PII_EXTRA", "").strip()
    return re.compile(terms, re.I) if terms else None


def main() -> int:
    patterns = dict(PATTERNS)
    if (e := extra()) is not None:
        patterns["private term (RUNCOACH_PII_EXTRA)"] = e
    hits = 0
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.name in SKIP_FILES:
            continue
        if SKIP_DIRS & set(p.name for p in path.parents):
            continue
        binary = path.suffix in BINARY_SUFFIXES
        if not binary:
            # Anything that is not valid UTF-8 is treated as a binary: an
            # unknown extension is a reason to look harder, not to look away.
            try:
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                binary = True
            except OSError:
                continue
        try:
            if binary:
                # The READABLE STRINGS inside the file, one per line — a PNG
                # `tEXt` chunk, an EXIF comment, or a text file saved with the
                # wrong extension. That is what a leak looks like inside a
                # binary; compressed pixel data is high entropy and yields
                # almost none. Matching the raw bytes instead flagged every
                # screenshot on the "German leftover" rule, because 0xE4 is also
                # just a byte — an alarm that can never go green.
                text = "\n".join(_STRINGS.findall(path.read_bytes().decode("latin-1")))
            else:
                text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # A single umlaut is evidence in source and noise in a byte stream.
        active = ({k: v for k, v in patterns.items() if k in BINARY_PATTERNS}
                  if binary else patterns)
        for no, line in enumerate(text.splitlines(), 1):
            citation = not binary and _DOI.search(line) is not None
            if citation:
                line = _DOI.sub(" ", line)
            for label, rx in active.items():
                if citation and label == "German leftover":
                    continue
                m = rx.search(line)
                if not m or (label.startswith("long numeric") and ALLOWED_NUMBERS.search(line)):
                    continue
                hits += 1
                print(f"{path.relative_to(ROOT)}:{no}: {label}: {m.group(0)[:60]}")
    print(f"{hits} hit(s)" if hits else "clean")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
