"""The generated eval fixture must stay identical to what the tools emit.

`evals/README.md` claims the cases are written "in the exact text shapes of
`src/runcoach/tools.py`". That claim was false and nothing could notice: the
fixtures are hand-written, and the shipped `get_training_readiness` had grown a
decision block and a calendar line that none of them contained — so every case
was testing the coach against a prompt it never receives.

One case is now rendered by `evals/fixture_gen.py` from a seeded store. This
test re-renders it and compares, byte for byte. When a tool's wording changes,
this goes red with a diff and the fixture gets regenerated; without it the
fixture would quietly drift again, and an eval measuring a stale prompt is worse
than no eval, because it reports green.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

yaml = pytest.importorskip("yaml", reason="pyyaml is an eval-only dependency")


def test_the_generated_case_matches_the_live_tool_output():
    import fixture_gen

    data = yaml.safe_load((ROOT / "evals" / "cases.yaml").read_text(encoding="utf-8"))
    case = next((c for c in data["cases"] if c["id"] == fixture_gen.CASE_ID), None)
    assert case is not None, f"{fixture_gen.CASE_ID} is gone from cases.yaml"

    expected = fixture_gen.fixture_input().strip()
    actual = case["input"].strip()
    if expected != actual:
        import difflib

        diff = "\n".join(difflib.unified_diff(actual.splitlines(), expected.splitlines(),
                                              "cases.yaml", "fixture_gen.py", lineterm=""))
        pytest.fail("the fixture no longer matches the tools. Regenerate it with\n"
                    "    uv run python evals/fixture_gen.py\n"
                    "and paste the result into the case.\n\n" + diff)


def test_the_generated_case_actually_carries_the_decision_block():
    """The point of generating it. If a future refactor drops the block from
    `get_training_readiness`, the test above goes red on the diff — this one says
    what was lost."""
    import fixture_gen

    text = fixture_gen.fixture_input()
    assert "The app's decision for today:" in text
    assert "do not silently replace it with your own" in text
    assert "of which 0 above threshold" in text, "both axes reach the model"
    assert "Garmin calendar:" in text


def test_no_case_is_built_on_a_label_the_code_cannot_produce():
    """An eval fixture is a claim about what the tools emit. Case 10 showed a
    run tagged `(Easy)` at aerobic TE 3.1 — but `HARD_AEROBIC_TE` is 3.0, so
    `run_kind` returns `Quality` for that input and the situation the case tests
    cannot occur. A case measuring an impossible state measures nothing, and it
    passes, which is worse than failing."""
    import re

    from runcoach.logic import run_kind

    text = (ROOT / "evals" / "cases.yaml").read_text(encoding="utf-8")
    # `- 2026-06-25 running (Easy): 9.8 km/55m / load 148 TE 2.9/1.8 / 156 bpm`
    # and `19.0 km/1h 52m / load 205 TE 3.8/0.5`. The hour is optional and was
    # missed at first, which made a long run parse as 52 minutes - a guard with
    # its own bug, which is the thing this file exists to prevent.
    line = re.compile(r"running \((?P<label>Easy|Quality|Long Run)\):"
                      r"[^/]*/(?:(?P<h>\d+)h )?(?P<min>\d+)m"
                      r" / load \d+ TE (?P<te>[\d.]+)/(?P<an>[\d.]+)")
    seen = 0
    for m in line.finditer(text):
        seen += 1
        minutes = int(m["min"]) + 60 * int(m["h"] or 0)
        got = run_kind({"activity_type": "running", "aerobic_te": float(m["te"]),
                        "anaerobic_te": float(m["an"]), "duration_s": minutes * 60})
        assert got == m["label"], (
            f"a fixture shows '({m['label']})' for TE {m['te']}/{m['an']}, but "
            f"logic.run_kind produces '{got}' - the case describes a state the "
            f"code cannot reach")
    assert seen >= 10, f"only {seen} activity lines parsed - the shape changed"
