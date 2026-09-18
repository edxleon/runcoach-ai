"""The palette claims in `tokens.css`, measured instead of asserted in prose.

`tokens.css` says the intensity bands were "checked with a palette validator,
not by eye", quotes a ΔE under deuteranopia for a scale that was rejected, and
gives a worst adjacent ΔE for the one that shipped. Nothing in the repo could
check any of that — "deuteranopia" and "palette validator" appeared only in
those comments, so a reader had to take three numbers on faith and a later edit
to a colour would have silently invalidated all of them.

So the validator lives here: sRGB → linear → LMS, the Viénot/Brettel
deuteranopia reduction, back to XYZ and CIE Lab, then ΔE76 between adjacent
bands. ~40 lines of stdlib, no dependency, and now the comment is a claim the
suite keeps honest.

ΔE76 is the crude one of the family, and deliberately so: it *overstates*
differences in the blue region, so a threshold that holds under ΔE76 is not
automatically safe — but a pair that FAILS here is certainly too close. The
numbers in the comment are ΔE76, and this is what produced them.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "runcoach" / "web" / "static"
TOKENS = STATIC / "tokens.css"

#: Below this, two adjacent bars in the same chart are not reliably separable.
#: Not a standard — WCAG has no colour-difference metric for graphics, only the
#: 3:1 contrast rule for non-text, which says nothing about two colours sitting
#: next to each other ABOVE the background. It is derived from the two scales
#: this project actually looked at: the five-zone scale that was rejected as
#: indistinguishable measures 8.8 here, the three-band scale that shipped has a
#: worst adjacent pair of 32.4. 15 sits clearly above the first and well below
#: the second. Treat it as "twice the pair we threw out", not as science.
MIN_ADJACENT_DE = 15.0


def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    return tuple(_linear(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))


def _deuteranope(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """Viénot, Brettel & Mollon (1999), the standard single-plane reduction."""
    r, g, b = rgb
    # linear RGB -> LMS (Hunt-Pointer-Estevez, sRGB primaries)
    lms = (17.8824 * r + 43.5161 * g + 4.11935 * b,
           3.45565 * r + 27.1554 * g + 3.86714 * b,
           0.0299566 * r + 0.184309 * g + 1.46709 * b)
    # the M cone is missing: reconstruct it from L and S
    long_, _mid, short = lms
    mid = 0.494207 * long_ + 1.24827 * short
    # LMS -> linear RGB (inverse of the above)
    return (0.0809444479 * long_ - 0.130504409 * mid + 0.116721066 * short,
            -0.0102485335 * long_ + 0.0540193266 * mid - 0.113614708 * short,
            -0.000365296938 * long_ - 0.00412161469 * mid + 0.693511405 * short)


def _lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = (max(0.0, min(1.0, c)) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e(a: str, b: str, *, deuteranopia: bool = False) -> float:
    ra, rb = _rgb(a), _rgb(b)
    if deuteranopia:
        ra, rb = _deuteranope(ra), _deuteranope(rb)
    la, lb = _lab(ra), _lab(rb)
    return sum((x - y) ** 2 for x, y in zip(la, lb, strict=True)) ** 0.5


def _blocks(prefix: str) -> dict[str, dict[str, str]]:
    """Every block in tokens.css that declares `--<prefix>-*`, keyed by a label.

    ALL of them, not only the two carrying an explicit `data-theme`. The palette
    exists four times over: a bare `:root` (the default), a
    `@media (prefers-color-scheme: light)` override, and the two `data-theme`
    blocks the toggle sets. The first two are what every first-time visitor
    sees — nobody has stored a choice yet, and every shipped screenshot shows
    the "Auto" pill. Reading only the explicit ones left half the file
    unvalidated, in a test whose opening line is that an edit to a colour must
    not slip through unnoticed."""
    css = TOKENS.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    pattern = (r"(?:@media\s*\(prefers-color-scheme:\s*(\w+)\)\s*\{\s*)?"
               r':root(?:\[data-theme="(\w+)"\])?\s*\{([^}]*)\}')
    for media, theme, body in re.findall(pattern, css):
        if f"--{prefix}-" not in body:
            continue
        label = theme or (f"media-{media}" if media else "root")
        out[label] = dict(re.findall(rf"--{prefix}-(\w+):\s*(#[0-9a-fA-F]{{6}})", body))
    assert len(out) >= 3, (f"expected the --{prefix}-* palette in the bare :root, a "
                           f"prefers-color-scheme block and both data-theme blocks; "
                           f"found {sorted(out)}")
    return out


def test_adjacent_intensity_bands_stay_apart_for_a_deuteranope():
    """The bands sit side by side in ONE bar, which is where "how much grey
    middle?" is read off. Two of them collapsing into one colour is not a
    cosmetic problem there — it is the chart losing its answer."""
    for label, cols in _blocks("band").items():
        assert set(cols) >= {"easy", "moderate", "hard"}, f"{label} is missing a band"
        for a, b, pair in ((cols["easy"], cols["moderate"], "easy/moderate"),
                           (cols["moderate"], cols["hard"], "moderate/hard")):
            normal, cvd = delta_e(a, b), delta_e(a, b, deuteranopia=True)
            assert normal >= MIN_ADJACENT_DE, f"{label} {pair}: dE {normal:.1f} normal vision"
            assert cvd >= MIN_ADJACENT_DE, f"{label} {pair}: dE {cvd:.1f} deuteranopia"


def test_the_sleep_ramp_separates_every_adjacent_phase():
    """Same claim, same measurement: four unrelated blue/violet tones put REM
    and light sleep at the same colour, and the fix was a single-hue ramp with
    monotonically falling lightness. The phases are stacked in one bar, so it is
    again the ADJACENT pairs that have to hold apart."""
    for label, cols in _blocks("sl").items():
        ramp = [cols[n] for n in ("deep", "light", "rem", "awake")]
        for a, b in zip(ramp, ramp[1:], strict=False):
            assert delta_e(a, b) >= MIN_ADJACENT_DE, label
            assert delta_e(a, b, deuteranopia=True) >= MIN_ADJACENT_DE, label


def _blend(fg: str, bg: str, alpha: float) -> str:
    """`fg` at `alpha` over `bg`, as a hex colour — what the browser composites."""
    def ch(c: str, i: int) -> int:
        return int(c.lstrip("#")[i:i + 2], 16)

    return "#" + "".join(f"{round(ch(fg, i) * alpha + ch(bg, i) * (1 - alpha)):02x}"
                         for i in (0, 2, 4))


def test_the_two_tone_intensity_bar_is_two_visible_tones():
    """The one NEW colour decision of this round, and the comment beside it says
    the first attempt "was simply invisible" — same hue, same weight, one rect
    drawn over the other. Opacity is not a colour, so ΔE cannot read it
    directly; what is measurable is the composite against the surface the bar
    sits on, which is what the eye actually compares."""
    css = TOKENS.read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    m = re.search(r"\.chart\.intensity \.barfill \{[^}]*opacity:\s*([0-9.]+)", html)
    assert m, "the intensity bar's outer opacity is no longer declared as expected"
    faint = float(m.group(1))

    checked = 0
    for theme in ("dark", "light"):
        block = re.search(rf':root\[data-theme="{theme}"\]\s*\{{([^}}]*)\}}', css)
        body = block.group(1) if block else ""
        accent = re.search(r"--accent:\s*(#[0-9a-fA-F]{6})", body)
        surface = re.search(r"--surface:\s*(#[0-9a-fA-F]{6})", body)
        if not (accent and surface):
            continue
        checked += 1
        blended = _blend(accent.group(1), surface.group(1), faint)
        got = delta_e(accent.group(1), blended)
        assert got >= MIN_ADJACENT_DE, (
            f"{theme}: the quality segment sits at dE {got:.1f} against the bar "
            f"around it - the caption promises a visible difference")
    assert checked == 2, "could not read --accent/--surface for both themes"


def test_the_rejected_five_zone_scale_really_does_fail():
    """A threshold nothing can fail is not a threshold. The Z3 yellow / Z4
    orange pair that was rejected as indistinguishable is pinned too: if this
    ever passes, the measurement above it has stopped measuring."""
    assert delta_e("#d9a441", "#e07a3c", deuteranopia=True) < MIN_ADJACENT_DE
