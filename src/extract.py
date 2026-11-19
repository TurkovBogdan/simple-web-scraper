"""Main-content extraction — HTML → markdown.

trafilatura is the accuracy leader on independent article benchmarks and emits
markdown natively; we feed it the already-rendered HTML (`page.content()`), never
its own fetcher. Synchronous/CPU-bound — the worker calls it via `asyncio.to_thread`.
See research `AGENTS/research/main-content-extraction/`.
"""
from __future__ import annotations

from loguru import logger


def to_markdown(html: str | None, url: str | None = None) -> str | None:
    """Extract the page's main content as markdown, or None if nothing usable."""
    if not html:
        return None
    try:
        import trafilatura

        md = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            include_images=False,
            favor_precision=False,
        )
        return md or None
    except Exception:  # noqa: BLE001
        logger.bind(stage="extract").opt(exception=True).warning("markdown extraction failed")
        return None
