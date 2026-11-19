"""Unit tests for the outcome classifier (no browser)."""
from __future__ import annotations

from browser.context import MIN_CONTENT_CHARS, _classify, _decide_outcome

_BODY = "x" * 500  # long enough to clear the "empty" threshold
_GOOD_CONTENT = "c" * (MIN_CONTENT_CHARS + 50)


def test_ok() -> None:
    assert _classify(200, _BODY, "https://a.com", "https://a.com") == "ok"


def test_redirected() -> None:
    assert _classify(200, _BODY, "https://a.com/final", "https://a.com") == "redirected"


def test_percent_encoded_final_url_is_not_a_redirect() -> None:
    # The browser reports a non-ASCII path percent-encoded; that is still the same page.
    requested = "https://ru.wikipedia.org/wiki/Тест"
    final = "https://ru.wikipedia.org/wiki/%D0%A2%D0%B5%D1%81%D1%82"
    assert _classify(200, _BODY, final, requested) == "ok"


def test_not_found() -> None:
    assert _classify(404, _BODY, "https://a.com", "https://a.com") == "not_found"


def test_access_denied_status() -> None:
    assert _classify(403, _BODY, "https://a.com", "https://a.com") == "access_denied"


def test_access_denied_marker() -> None:
    body = "<html><title>Just a moment...</title></html>"
    assert _classify(200, body, "https://a.com", "https://a.com") == "access_denied"


def test_long_body_mentioning_marker_is_not_flagged() -> None:
    # A real article that merely mentions "captcha"/"access denied" must stay ok.
    body = "Web scraping and captcha and access denied. " + "content " * 400
    assert _classify(200, body, "https://a.com", "https://a.com") == "ok"


# --- _decide_outcome (content-aware salvage after the load cap) ---

def test_decide_uses_status_when_present() -> None:
    # A real HTTP status always wins over content salvage (403 stays a block).
    assert _decide_outcome(403, False, "blocked", "x" * 999, "u", "u") == "access_denied"


def test_decide_salvages_content_on_timeout() -> None:
    # Cap fired (no status) but we extracted solid content → usable `ok`.
    assert _decide_outcome(None, True, _BODY, _GOOD_CONTENT, "u", "u") == "ok"


def test_decide_timeout_when_no_content() -> None:
    assert _decide_outcome(None, True, "", None, "u", "u") == "timeout"
    assert _decide_outcome(None, True, "x", "short", "u", "u") == "timeout"


def test_decide_upstream_down_when_nav_failed_no_content() -> None:
    assert _decide_outcome(None, False, "", None, "u", "u") == "upstream_down"


def test_decide_200_with_no_extractable_content_is_empty() -> None:
    # JS-rendered shell: 200 + large body but nothing extracted → empty, not ok.
    assert _decide_outcome(200, False, _BODY, None, "u", "u") == "empty"
    assert _decide_outcome(200, False, _BODY, "short", "u", "u") == "empty"


def test_decide_200_with_good_content_is_ok() -> None:
    assert _decide_outcome(200, False, _BODY, _GOOD_CONTENT, "u", "u") == "ok"


def test_upstream_error() -> None:
    assert _classify(502, _BODY, "https://a.com", "https://a.com") == "upstream_error"


def test_upstream_down() -> None:
    assert _classify(None, "", "https://a.com", "https://a.com") == "upstream_down"


def test_empty() -> None:
    assert _classify(200, "tiny", "https://a.com", "https://a.com") == "empty"
