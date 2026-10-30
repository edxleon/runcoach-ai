"""The landing page (docs/index.html, served by GitHub Pages) against the facts it
repeats from the README and the files it points at.

It is a second copy of the pitch and the FAQ, written for search engines and for
shared links, and nothing renders it in CI: a moved screenshot or an answer
rewritten in the README alone would go stale there without a sound."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
PAGE = (DOCS / "index.html").read_text(encoding="utf-8")

def _meta(attr: str, name: str) -> str:
    m = re.search(rf'<meta {attr}="{re.escape(name)}" content="([^"]*)">', PAGE)
    assert m, f"no <meta {attr}={name!r}>"
    return html.unescape(m.group(1))


def _jsonld() -> dict:
    (block,) = re.findall(r'<script type="application/ld\+json">(.*?)</script>', PAGE, re.S)
    return json.loads(block)


def _plain(text: str) -> str:
    """Markdown or HTML prose reduced to its words: links to their text, tags
    dropped, whitespace folded."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def test_the_description_leads_with_the_keyword_and_is_one_text():
    description = _meta("name", "description")
    assert description.startswith("AI running coach for Garmin")
    assert len(description) <= 155, len(description)
    assert _meta("property", "og:description") == description == _jsonld()["description"]


def test_the_faq_says_word_for_word_what_the_readme_says():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    faq = PAGE[PAGE.index('<section class="faq">'):PAGE.index("</section>")]
    pairs = re.findall(r"<h3>(.*?)</h3>\s*<p>(.*?)</p>", faq, re.S)
    assert pairs, "no questions on the page"
    for question, answer in pairs:
        m = re.search(rf"^### {re.escape(html.unescape(question))}\n\n(.*?)\n\n", readme, re.S | re.M)
        assert m, f"the README does not ask {question!r}"
        assert _plain(answer) == _plain(m.group(1)), question


def test_every_file_the_page_points_at_is_in_docs():
    targets = re.findall(r'(?:src|href|poster)="([^"#:]+)"', PAGE)
    assert targets, "the page points at no file"
    for target in targets:
        if target in ("./", "/"):
            continue
        assert (DOCS / target).is_file(), target


def test_search_engines_have_a_way_to_the_page():
    """A new page that nothing links to stays "unknown to Google" however often
    indexing is requested: the sitemap and the README badge are its two ways in."""
    (canonical,) = re.findall(r'<link rel="canonical" href="([^"]+)">', PAGE)
    sitemap = (DOCS / "sitemap.xml").read_text(encoding="utf-8")
    assert re.findall(r"<loc>(.*?)</loc>", sitemap) == [canonical]
    assert f"]({canonical})" in (ROOT / "README.md").read_text(encoding="utf-8")
