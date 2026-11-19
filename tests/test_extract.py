"""Unit tests for markdown extraction (no browser)."""
from __future__ import annotations

from extract import to_markdown

_ARTICLE = """
<html><head><title>Sample</title></head><body>
<nav>home about contact</nav>
<article>
<h1>Main Heading</h1>
<p>First paragraph with a <a href="https://example.com">link</a> inside it,
long enough to be considered real content by the extractor and not boilerplate.</p>
<h2>Subsection</h2>
<ul><li>Point one of the list</li><li>Point two of the list</li></ul>
<p>Closing paragraph that also carries a fair amount of meaningful text so the
main-content heuristics keep it in the extracted body.</p>
</article>
<footer>copyright 2026 boilerplate footer text goes here</footer>
</body></html>
"""


def test_extracts_markdown() -> None:
    md = to_markdown(_ARTICLE, "https://example.com/post")
    assert md is not None
    assert "Main Heading" in md
    # boilerplate nav/footer should be dropped
    assert "copyright 2026" not in md
    # markdown structure preserved
    assert "-" in md  # list markers


def test_empty_input_returns_none() -> None:
    assert to_markdown("", "https://x.com") is None
    assert to_markdown(None) is None
