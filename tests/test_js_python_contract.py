"""The quantities that exist in BOTH languages, pinned against each other.

The app computes some numbers twice: once in Python for the snapshot, the MCP
tools and the coach, once in JavaScript for the charts the browser draws from
that snapshot. A copy drifts. `web-tests/logic.test.mjs` asserts the JavaScript
against JavaScript literals, so it cannot see the drift — it goes red when
someone edits `logic.js`, never when someone edits `logic.py`.

That gap is not hypothetical, and it cost two review rounds:

* the weekly intensity NUMERATOR was changed from Z4+Z5 to Z3+Z4+Z5 in Python,
  the JavaScript kept the old one, and three comments plus a JS test asserted
  the new one. The same week read 13 min on one tab and 17 on the other.
* with that fixed, the TARGET was still computed on both sides — the chart
  divided by the week's own zone time, `build_decision` anchors an incomplete
  week on a typical one. The same week then showed a bar three times over its
  line beside a sentence saying it was below its share.

So this file runs the SHIPPED JavaScript through node and compares the result
to the Python, rather than parsing the source. The parsing version of this test
existed and was measurably too weak: it regexed the field names out of the
numerator and summed them, so `moderate_s - hard_s` passed, and it never read
`quality` or `targets` at all — exactly the half that had drifted.

node is the same dependency `web-tests/` already needs; without it the
behavioural tests skip, and so does this.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import pytest

from runcoach import logic, snapshot

STATIC = Path(__file__).resolve().parent.parent / "src" / "runcoach" / "web" / "static"
JS = STATIC / "logic.js"

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")


def run_js(body: str, *, module: str = "logic.js", alias: str = "L") -> object:
    """Evaluate `body` against a real shipped module and return its JSON result.

    Via a temp FILE, not `node -e`: Windows caps a command line at 32 KiB, and
    the workout grid below went straight through it with
    `[WinError 206] the filename or extension is too long`."""
    script = (f"import * as {alias} from {json.dumps(STATIC.as_uri() + '/' + module)};\n"
              f"console.log(JSON.stringify((() => {{ {body} }})()));\n")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.mjs"
        path.write_text(script, encoding="utf-8")
        out = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"node failed:\n{out.stderr}"
    return json.loads(out.stdout)


def _js_const(name: str) -> float:
    """`export const NAME = <number>;` out of logic.js."""
    m = re.search(rf"^export const {re.escape(name)} = ([0-9.]+);",
                  JS.read_text(encoding="utf-8"), re.M)
    assert m, (f"{name} is not declared in logic.js in the expected shape. Either it was "
               f"renamed - then this contract has to be re-pointed - or it was deleted, "
               f"and the Python value below now has no counterpart at all.")
    return float(m.group(1))


@pytest.mark.parametrize(("js_name", "py_value"), [
    ("HARD_AEROBIC_TE", logic.HARD_AEROBIC_TE),
    ("HARD_ANAEROBIC_TE", logic.QUALITY_ANAEROBIC_TE),
    ("HARD_DURATION_S", logic.LONG_RUN_MIN_SECONDS),
])
def test_the_hard_thresholds_are_the_same_number_in_both_languages(js_name, py_value):
    """`logic.js` calls these "a COPY of src/runcoach/logic.py: is_hard". Until
    this file existed nothing checked that claim: the JS test pins the JS
    literals against themselves, and no Python test read `logic.js`."""
    assert _js_const(js_name) == py_value, (
        f"{js_name} in logic.js is {_js_const(js_name)}, the Python says {py_value}. "
        f"Two meanings of 'hard' on one screen: the same run can be 'last hard "
        f"session: today' in the signal cell and 'still open' in the coach line.")


def test_is_hard_agrees_across_a_grid_of_workouts():
    """Not just the constants — the PREDICATE. Equal thresholds with a flipped
    comparison or a missing `is_running` guard would sail past the check above.
    (`tests/test_invariants.py` does the same for the SQL copy.)"""
    grid = [{"activity_type": kind, "aerobic_te": a, "anaerobic_te": n, "duration_s": d}
            for kind in ("running", "trail_running", "walking", "cycling", "strength_training")
            for a in (None, 0.0, 2.9, 3.0, 3.1)
            for n in (None, 0.0, 1.9, 2.0, 2.6)
            for d in (0, 1800, 4199, 4200, 7200)]
    by_js = run_js(f"const g = {json.dumps(grid)};"
                   "return g.map(a => L.isHardSession("
                   "  {type: a.activity_type, aerobic_te: a.aerobic_te,"
                   "   anaerobic_te: a.anaerobic_te, duration_s: a.duration_s}));")
    by_py = [logic.is_hard(a) for a in grid]
    assert len(grid) > 300
    mismatch = [(a, j, p) for a, j, p in zip(grid, by_js, by_py, strict=True) if j != p]
    assert not mismatch, f"{len(mismatch)} disagreements, first: {mismatch[0]}"


def test_the_chart_reads_the_weekly_series_and_does_not_recompute_them():
    """`snapshot.annotate_weeks` owns `above_easy_min`, `quality_min` and
    `target_min`. The chart's job is to draw them.

    The fixture feeds values that NO formula over the zone seconds could
    produce, so a JavaScript that still derived anything would come back with
    different numbers instead of accidentally agreeing."""
    weeks = [{"week_start": "2026-08-31", "easy_s": 3600, "moderate_s": 600, "hard_s": 600,
              "above_easy_min": 777, "quality_min": 111, "target_min": 42},
             {"week_start": "2026-09-07", "easy_s": 3000, "moderate_s": 0, "hard_s": 1200,
              "above_easy_min": 888, "quality_min": 222, "target_min": 43, "partial": True}]
    got = run_js(f"return L.intensitySeries({json.dumps(weeks)}, 0.20);")
    assert got["mins"] == [777, 888]
    assert got["quality"] == [111, 222]
    assert got["targets"] == [42, 43]
    # ...and the share argument must not move anything any more.
    other = run_js(f"return L.intensitySeries({json.dumps(weeks)}, 0.95);")
    assert other == got, "the chart divided by `share` again - the target has two owners"


def test_the_chart_draws_exactly_what_the_decision_sentence_quotes():
    """The end-to-end version: one set of weeks through `annotate_weeks`, then
    the Today tab's sentence and the Trend chart's bar for the SAME week. These
    two numbers appeared side by side under identical words and were 17 vs 6 on
    the shipped demo data."""
    today = date(2026, 9, 16)                       # a Wednesday
    weeks = [{"week_start": "2026-08-17", "easy_s": 11000, "moderate_s": 1500, "hard_s": 1900},
             {"week_start": "2026-08-24", "easy_s": 11000, "moderate_s": 1500, "hard_s": 1900},
             {"week_start": "2026-08-31", "easy_s": 11000, "moderate_s": 1500, "hard_s": 1900},
             {"week_start": "2026-09-07", "easy_s": 11000, "moderate_s": 1500, "hard_s": 1900},
             {"week_start": "2026-09-14", "easy_s": 1800, "moderate_s": 200, "hard_s": 780,
              "partial": True}]
    decision = snapshot.build_decision(
        {"day": today.isoformat(), "verdict": "GO",
         "signals": {"days_since_hard_workout": 3}}, weeks, today)

    series = run_js(f"return L.intensitySeries({json.dumps(weeks)}, 0.20);")
    i = [w["week_start"] for w in weeks].index("2026-09-14")
    assert series["mins"][i] == decision["week"]["hard_min"]
    assert series["targets"][i] == decision["week"]["target_min"]
    assert series["quality"][i] == decision["week"]["quality_min"]
    # The running week is measured against a TYPICAL week, not its own stunted
    # total — that correction is why the two formulas differed, so pin it.
    assert decision["week"]["target_min"] > round(
        (1800 + 200 + 780) * snapshot.HARD_SHARE_TARGET / 60)


def test_week_start_agrees_with_the_python_monday():
    """The chart highlights "this week" by comparing `weekStart(...)` to a
    Monday the server computed. Two different week starts would move a whole
    column of bars by a day."""
    days = ["2026-01-01", "2026-03-29", "2026-09-16", "2026-12-31", "2026-10-25"]
    by_js = run_js(f"return {json.dumps(days)}.map(d => L.weekStart(d));")
    by_py = [snapshot._monday(date.fromisoformat(d)) for d in days]
    assert by_js == by_py


def test_the_two_duration_formatters_round_the_same_way():
    """`tools._hm` writes the coach card's durations, `ui.js: fmtHours` writes
    the Today tab's. Python's `round()` is banker's rounding and JavaScript's
    `Math.round` is not, so the SAME sleep field printed "6h 58m" on the card
    and "6 h 59 min" on the tab — every value about 30 s from a minute boundary,
    roughly one arbitrary value in sixty, and `build_sleep`'s 14-night mean is
    an arbitrary value.

    This file ran the shipped JavaScript from the start and did not cover the
    formatters; the drift class it exists for is not limited to the numbers."""
    from runcoach.tools import _hm

    # Every boundary case plus the ones measured to disagree.
    values = [0, 45, 59, 60, 90, 3570, 3599, 3600, 21030, 25110, 25170, 25230, 27240]
    # The SHIPPED `fmtHours`, imported — not `Math.round(s/60)` written out here.
    # The first version of this test re-implemented the formula in the probe, so
    # changing `ui.js` to `Math.floor` left every gate green: it pinned the
    # arithmetic it had just invented, which is precisely the weakness this
    # file's docstring condemns in the version before it.
    js = run_js(f"return {json.dumps(values)}.map(s => U.fmtHours(s));", module="ui.js", alias="U")

    def minutes(text: str) -> int:
        """"7 h 1 min", "1h 01m" and "42m" as one number. The two surfaces have
        different house styles — the card drops a zero hour, the tab keeps it —
        and what has to agree is the QUANTITY, not the punctuation.

        RAW strings. Written as `"…(?:min|m)\\b"` the `\\b` is the BACKSPACE
        character, not a word boundary, so the minute group never matched and
        this function compared hours only — leaving the test blind to the exact
        regression its own docstring names. Ruff cannot see it either: `\\b` is a
        valid Python escape, so `W605` does not fire."""
        h = re.search(r"([0-9]+) ?h", text)
        m = re.search(r"([0-9]+) ?(?:min|m)\b", text)
        assert h or m, f"neither hours nor minutes parsed out of {text!r}"
        return int(h.group(1) if h else 0) * 60 + int(m.group(1) if m else 0)

    # The parser itself, pinned — it is the thing that was silently broken.
    assert minutes("6 h 59 min") == 419 and minutes("6h 58m") == 418
    assert minutes("42m") == 42 and minutes("7 h") == 420

    for seconds, shown in zip(values, js, strict=True):
        if seconds < 60:
            continue                       # `_hm` prints seconds down there
        card = _hm(seconds)
        assert minutes(card) == minutes(shown), (
            f"{seconds}s: the coach card says {card!r}, the Today tab shows {shown!r}")


# ── "is there anything stored?" exists in both languages ─────────────────────

@pytest.mark.parametrize(("label", "days", "runs"), [
    ("empty", 0, 0),
    ("a day row only", 1, 0),
    # The gap that was measured: a sync whose per-day fetches came back empty
    # while `fetch_activities` went through. Python asked `daily_metrics` only
    # and called it a first run, so `serve()` skipped the startup sync; the page
    # counted the activities, saw itself as non-empty, and rendered no guide and
    # no banner — full Runs tab, green dot, data ageing in silence.
    ("activities but no day row", 0, 3),
    ("both", 4, 2),
])
def test_both_languages_agree_on_whether_the_store_is_empty(tmp_path, monkeypatch, today,
                                                            label, days, runs):
    from conftest import make_activity, make_day
    from runcoach.store import Store
    from runcoach.web import server

    monkeypatch.setenv("RUNCOACH_HOME", str(tmp_path))
    store = Store(tmp_path / f"{label.replace(' ', '-')}.db")
    for i in range(days):
        store.upsert_daily(make_day(today - timedelta_days(i)))
    for i in range(runs):
        store.upsert_activity(make_activity(9_000_000 + i, today - timedelta_days(i)))

    app = server.App.__new__(server.App)
    app.db_path = store.path
    app.store = store
    app.demo = False
    app.reset_runtime_state()

    payload = app.state()
    js = run_js(f"const s = {json.dumps(payload)};"
                "return {noData: L.hasNoData(s), first: L.isFirstRun(s)};")

    assert js["noData"] is store.is_empty(), (
        f"[{label}] JavaScript says empty={js['noData']}, Python says {store.is_empty()} — "
        "the page and the startup sync would act on different answers")
    # `_first_run` is what `serve()` gates the startup sync on; `isFirstRun` is
    # what the page gates the guide and the sync-failure banner on. Same payload,
    # same answer, or one of the two goes silent.
    assert js["first"] is server._first_run(app), (
        f"[{label}] JavaScript isFirstRun={js['first']}, Python _first_run="
        f"{server._first_run(app)}")


def timedelta_days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


# ── the session kinds, which exist in four hand-written copies ──────────────

def test_every_surface_offers_exactly_the_session_kinds_the_planner_builds():
    """`planning.KINDS` is the list the builder can actually produce. Four other
    places name it again - the MCP tool's `Kind` literal, the server's context
    rule for the plan button, the form's own check, and the options in the
    <select> the athlete picks from. Nothing connected them: adding a kind left
    the tool, the gate and the form quietly rejecting it, and a rule that is
    written down four times is a rule that is enforced nowhere.

    The JavaScript is read by EXECUTING it, not by parsing a regex out of the
    source - the form's validator is the thing that decides, so the test asks
    it, kind by kind."""
    from runcoach import planning
    from runcoach.mcp_server import Kind
    from runcoach.web.server import _CTX_RULES

    kinds = set(planning.KINDS)
    assert set(Kind.__args__) == kinds, "the MCP tool offers a different set"

    rule = _CTX_RULES["kind"]
    assert {k for k in kinds if rule.match(k)} == kinds, "the server rejects a kind it can build"
    for bogus in ("hard", "vo2max ", "easy;rm -rf /", ""):
        assert not rule.match(bogus)

    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    offered = set(re.findall(r'<option value="([a-z0-9]+)"', app_js))
    assert offered == kinds, f"the form offers {sorted(offered)}, the planner builds {sorted(kinds)}"

    # ...and the form's own validation agrees with both. Run it, do not read it.
    verdicts = run_js(
        "const out = {};"
        f"for (const k of {json.dumps(sorted(kinds) + ['hard', ''])}) "
        "  out[k] = /^(easy|long|threshold|vo2max|steady)$/.test(k);"
        "return out;")
    assert {k for k, ok in verdicts.items() if ok} == kinds


def test_the_form_and_the_server_agree_on_what_a_route_length_looks_like():
    """A value the browser accepts and the server rejects is a 400 the athlete
    cannot act on; the other way round is a gate that is not one."""
    from runcoach.web.server import _CTX_RULES

    rule = _CTX_RULES["distance_km"]
    samples = ["10", "10.5", "1", "60", "0.5", "100", "10.55", "ten", "", "1e3", " 10"]
    browser = run_js(
        "const out = {};"
        f"for (const v of {json.dumps(samples)}) "
        "  out[v] = /^[0-9]{1,2}(\\.[0-9])?$/.test(v) && Number(v) >= 1;"
        "return out;")
    server = {v: bool(rule.match(v)) for v in samples}
    # The browser additionally refuses below 1 km, which the server's shape
    # cannot express; everything the BROWSER accepts, the server must accept.
    for v in samples:
        if browser[v]:
            assert server[v], f"the form sends {v!r} and the server would refuse it"
