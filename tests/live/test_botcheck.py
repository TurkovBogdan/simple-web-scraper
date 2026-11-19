"""LIVE bot-detection self-check — launches a real browser and drives public
detector sites through `ScraperBackend.check_page`, scoring the stealth profile.

Run: `uv run pytest -m live -s`  (excluded from the default suite).
Network-dependent and slow; a site being down does not mean the fingerprint failed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from browser.context import ScraperBackend
from check import CheckReport, browserscan, pixelscan, scrapfly

pytestmark = pytest.mark.live

_SITES = [browserscan, pixelscan, scrapfly]


@pytest.fixture
async def backend(tmp_path: Path):
    be = ScraperBackend(tmp_path / "profile", "ru-rtx3060", page_concurrency=2)
    await be.load()
    yield be
    await be.unload()


@pytest.mark.parametrize("site", _SITES, ids=lambda m: m.__name__.split(".")[-1])
async def test_bot_detection(backend: ScraperBackend, site) -> None:
    res = await backend.check_page(
        site.URL,
        site.EVAL_JS,
        wait_mode=site.WAIT_MODE,
        wait=site.WAIT,
        wait_sec_after_eval=site.WAIT_SEC_AFTER_EVAL,
    )
    report: CheckReport = site.analyze(res.get("body"), res.get("eval_result"))
    print(f"\n[{report.site}] passed={report.passed} :: {report.verdict}")
    assert report.passed, f"{report.site} flagged the browser: {report.verdict}"
