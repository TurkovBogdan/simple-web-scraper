"""Heavy browser backend — one persistent Chromium context, in the worker only.

`load()` is lazy (launches Chromium on the first scrape), `unload()` closes it so
the OS reclaims memory. Page concurrency is bounded by a semaphore; a launch lock
prevents concurrent first-requests from double-launching.

The context is launched with a fingerprint profile (UA/locale/timezone/viewport/
screen) and the stealth init-script from [[ref-simple-scraper-api]] is injected
before any page script runs.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from loguru import logger

from browser.profile import load_profile
from browser.scroll import scroll_to_bottom
from browser.stealth import build_stealth_js
from extract import to_markdown

# Marker substrings that signal a bot-wall / challenge even on a 200.
_BLOCK_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "cf-challenge",
    "captcha",
    "access denied",
    "attention required",
)


def _classify(status: int | None, body: str, final_url: str, requested_url: str) -> str:
    """Map HTTP status + HTML to a coarse outcome; `_decide_outcome` refines it with the
    extracted content. Challenge URLs and a richer marker set are a follow-up modelled
    on simple-scraper-api's `classify.py`."""
    if status is None:
        return "upstream_down"
    stripped = (body or "").strip()
    text = stripped.lower()
    if status == 404:
        return "not_found"
    # Bot-wall / challenge pages are short; only trust the block markers on a small
    # body, else long articles that merely mention "captcha"/"access denied" (e.g. an
    # article about scraping) get false-flagged.
    challenge = len(stripped) < 1500 and any(m in text for m in _BLOCK_MARKERS)
    if status in (401, 403, 429) or challenge:
        return "access_denied"
    if status >= 500:
        return "upstream_error"
    if status >= 400:
        return "upstream_error"
    if len((body or "").strip()) < 200:
        return "empty"
    # The browser reports the URL percent-encoded (Cyrillic paths, spaces); compare
    # decoded, or such a page comes back `redirected` without having moved.
    if unquote(final_url).rstrip("/") != unquote(requested_url).rstrip("/"):
        return "redirected"
    return "ok"


# A scrape is "usable" if we extracted at least this much main content (markdown
# chars). Below it the page is treated as empty rather than a real result.
MIN_CONTENT_CHARS = 200

# A scrape must hand its page slot back even when the driver stops answering. A page
# stuck in content()/title()/close() otherwise holds its semaphore slot for good, and
# once every slot is held the worker serves nothing while still passing /healthz
# (seen 2026-11-14: three hangs in a day under sustained batch load).
SCRAPE_GRACE_SECONDS = 15.0
PAGE_CLEANUP_SECONDS = 5.0

# Closing the browser and stopping its driver are bounded too: a wedged driver must not
# pin the idle watcher (which holds the worker's lock meanwhile) or the shutdown.
BROWSER_CLOSE_SECONDS = 15.0


def _decide_outcome(
    status: int | None,
    nav_timeout: bool,
    body: str,
    content: str | None,
    final_url: str,
    requested_url: str,
) -> str:
    """Final outcome, content-aware.

    When we have an HTTP status, trust the status/body classifier. When we don't
    (navigation error, or the 5 s cap fired mid-load), salvage whatever loaded:
    if the extracted main content is long enough it's a real result (`ok`),
    otherwise it's a `timeout` (cap fired) or `upstream_down` (nav failed).
    """
    good = bool(content) and len(content.strip()) >= MIN_CONTENT_CHARS
    if status is not None:
        base = _classify(status, body, final_url, requested_url)
        # A 200 whose main content we couldn't extract is a failed extraction, not a
        # real result — typically a JS-rendered shell (reddit/x/medium). Report
        # `empty` so the caller can retry with use_idle. Real error statuses
        # (404/403/5xx) keep their verdict.
        if base in ("ok", "redirected") and not good:
            return "empty"
        return base
    if good:
        return "ok"
    return "timeout" if nav_timeout else "upstream_down"


def _abandoned_result(url: str) -> dict:
    """What a scrape returns when its page outlived the deadline and was dropped."""
    return {
        "outcome": "timeout",
        "url": url,
        "final_url": None,
        "target_status": None,
        "title": None,
        "headers": None,
        "content": None,
        "body": None,
    }


class ScraperBackend:
    """Owns one patchright persistent Chromium context."""

    def __init__(self, profile_dir: Path, profile_name: str, page_concurrency: int) -> None:
        self.profile_dir = Path(profile_dir)
        self.profile_name = profile_name
        self._sem = asyncio.Semaphore(page_concurrency)
        self._launch_lock = asyncio.Lock()
        self._pw: Any = None
        self._context: Any = None
        self._stealth_js: str = ""

    @property
    def loaded(self) -> bool:
        return self._context is not None

    async def load(self) -> None:
        if self._context is not None:
            return
        async with self._launch_lock:
            if self._context is not None:
                return
            from patchright.async_api import async_playwright

            profile = load_profile(self.profile_name)
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            logger.bind(stage="load").info(
                "launching chromium profile={} dir={}", profile.name, self.profile_dir,
            )
            pw = await async_playwright().start()
            try:
                context = await pw.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=True,
                    user_agent=profile.user_agent,
                    locale=profile.locale,
                    timezone_id=profile.timezone,
                    viewport={"width": profile.viewport.width, "height": profile.viewport.height},
                    screen={"width": profile.screen.width, "height": profile.screen.height},
                    device_scale_factor=profile.device_pixel_ratio,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception:
                # A failed launch must not leak the driver: each one is a node process
                # holding pipes of this worker, and a few hundred exhaust its file
                # descriptors (2026-11-06: "Executable doesn't exist" on every URL for
                # two minutes, then "Too many open files" on everything after that).
                await self._stop_driver(pw)
                raise
            # NOTE: patchright runs `context.add_init_script` in an ISOLATED world
            # (its anti-detection design), so the profile-specific navigator/webgl
            # spoofs never reach the page's main world. We inject the stealth script
            # into the MAIN world per page via raw CDP
            # `Page.addScriptToEvaluateOnNewDocument` (see `_scrap_page`). The script
            # self-masks (toString→native, getOwnPropertyDescriptor hook), so a
            # main-world injection stays hidden. See docs/architecture.md, "Anti-bot layer".
            self._stealth_js = build_stealth_js(profile)
            self._pw, self._context = pw, context
            logger.bind(stage="load").info("chromium ready (stealth prepared)")

    async def unload(self) -> None:
        """Close the browser and stop its driver. Never raises: an error here would end
        the idle watcher (no more eviction, no self-exit) or abort the shutdown cleanup —
        seen as "Application shutdown failed" whenever the browser was already gone."""
        async with self._launch_lock:
            context, pw = self._context, self._pw
            if context is None and pw is None:
                return
            # Forget both first: whatever happens below, the next scrape launches
            # afresh instead of reusing a half-closed browser.
            self._context = self._pw = None
            logger.bind(stage="evict").info("closing chromium")
            if context is not None:
                try:
                    await asyncio.wait_for(context.close(), timeout=BROWSER_CLOSE_SECONDS)
                except Exception as exc:  # noqa: BLE001 — already gone, or wedged
                    logger.bind(stage="evict").warning("context close failed err={!r}", exc)
            if pw is not None:
                await self._stop_driver(pw)

    @staticmethod
    async def _stop_driver(pw: Any) -> None:
        """Stop a playwright driver (a node subprocess) without raising or hanging."""
        try:
            await asyncio.wait_for(pw.stop(), timeout=BROWSER_CLOSE_SECONDS)
        except Exception as exc:  # noqa: BLE001
            logger.bind(stage="evict").warning("driver stop failed err={!r}", exc)

    async def delete_profile(self) -> None:
        """Close the context and wipe the persistent profile dir."""
        await self.unload()
        shutil.rmtree(self.profile_dir, ignore_errors=True)

    # Which lifecycle state `goto` waits for. Default is `domcontentloaded` (main
    # content is ready then) — NOT `load`, which also waits for every
    # image/font/analytics beacon and so falsely times out on asset-heavy pages that
    # already have their content. `use_idle` opts into `networkidle` for JS-rendered
    # pages that fill in after DOM ready. `timeout` (default 5 s) bounds this wait
    # inside patchright's driver; `scrap` adds an outer asyncio.wait_for over the whole
    # page as the backstop for calls the driver never answers (it does interrupt the
    # async API — checked 2026-11-14).
    @staticmethod
    def _wait_until(use_idle: bool) -> str:
        return "networkidle" if use_idle else "domcontentloaded"

    async def scrap(
        self,
        url: str,
        timeout: float,
        use_idle: bool = False,
        use_scroll: bool = False,
    ) -> dict:
        # Not exported by `patchright.async_api`; the exact pin (==1.64.4) keeps it put.
        from patchright._impl._errors import TargetClosedError

        await self.load()
        deadline = max(1.0, timeout) + SCRAPE_GRACE_SECONDS
        # The page and its CDP session, and how far the scrape got. Cleanup runs
        # outside the deadline so a scrape cut off late still closes its page.
        held: dict = {"page": None, "cdp": None, "stage": "new_page"}
        async with self._sem:
            try:
                return await asyncio.wait_for(
                    self._scrap_page(url, timeout, use_idle, use_scroll, held),
                    timeout=deadline,
                )
            except asyncio.TimeoutError:
                logger.bind(stage="scrap").warning(
                    "page abandoned after {:.0f}s at stage={} url={}",
                    deadline, held["stage"], url,
                )
                return _abandoned_result(url)
            except TargetClosedError as exc:
                if held["page"] is None:
                    # No page could be opened: the browser died (crashed, killed) since
                    # the last scrape and `_context` still points at it. Drop it so the
                    # next scrape launches a new one — otherwise every scrape fails the
                    # same way for as long as requests keep the idle watcher away.
                    logger.bind(stage="scrap").warning(
                        "browser gone ({}); relaunching on the next scrape", exc,
                    )
                    await self.unload()
                raise
            finally:
                await self._close_page(held, url)

    async def _scrap_page(
        self,
        url: str,
        timeout: float,
        use_idle: bool,
        use_scroll: bool,
        held: dict,
    ) -> dict:
        """Open, load and read one page; the caller closes it from `held`."""
        from patchright.async_api import Error as PWError

        wait_until = self._wait_until(use_idle)
        nav_ms = int(max(1.0, timeout) * 1000)
        page = held["page"] = await self._context.new_page()
        held["stage"] = "stealth"
        cdp = held["cdp"] = await self._inject_stealth(page)
        resp = None
        nav_timeout = False
        started = time.monotonic()
        held["stage"] = "goto"
        try:
            resp = await page.goto(url, wait_until=wait_until, timeout=nav_ms)
        except PWError as exc:
            # Timeout → keep partial DOM as `timeout`; other nav errors →
            # no response → `upstream_down` via _classify(status=None).
            if "imeout" in str(exc):
                nav_timeout = True
                # The navigation is still pending; without stopping it,
                # page.content() blocks until the server finally responds
                # (defeating the cap). Halt it via the page's existing CDP
                # session so we can read the partial DOM immediately.
                if cdp is not None:
                    try:
                        await cdp.send("Page.stopLoading")
                    except Exception:  # noqa: BLE001
                        pass

        if use_scroll and resp is not None:
            held["stage"] = "scroll"
            # The scroll takes what the load left of the page budget, at least 0.5 s.
            budget = max(0.5, timeout - (time.monotonic() - started))
            try:
                await asyncio.wait_for(scroll_to_bottom(page), timeout=budget)
            except asyncio.TimeoutError:
                pass

        held["stage"] = "read"
        try:
            final_url = page.url
            body = await page.content()
            title = await page.title()
        except Exception:  # noqa: BLE001 — page torn mid-navigation
            final_url, body, title = url, "", None

        status = resp.status if resp is not None else None
        headers = dict(resp.headers) if resp is not None else None
        # Extract first, then decide the outcome from what we actually got —
        # after the cap we return whatever content is there rather than a
        # bare "timeout".
        held["stage"] = "extract"
        content = await asyncio.to_thread(to_markdown, body, final_url) if body else None
        outcome = _decide_outcome(status, nav_timeout, body, content, final_url, url)
        return {
            "outcome": outcome,
            "url": url,
            "final_url": final_url,
            "target_status": status,
            "title": title,
            "headers": headers,
            "content": content,
            "body": body,
        }

    @staticmethod
    async def _close_page(held: dict, url: str) -> None:
        """Detach and close, each bounded: a dead page must not pin the scrape."""
        if held["cdp"] is not None:
            try:
                await asyncio.wait_for(held["cdp"].detach(), timeout=PAGE_CLEANUP_SECONDS)
            except Exception as exc:  # noqa: BLE001
                logger.bind(stage="scrap").warning(
                    "cdp detach failed url={} err={!r}", url, exc,
                )
        if held["page"] is None:
            return
        try:
            await asyncio.wait_for(held["page"].close(), timeout=PAGE_CLEANUP_SECONDS)
        except Exception as exc:  # noqa: BLE001
            logger.bind(stage="scrap").warning(
                "page close failed, page left open url={} err={!r}", url, exc,
            )

    async def check_page(
        self,
        url: str,
        eval_js: str,
        wait_mode: str = "load",
        wait: float = 30.0,
        wait_sec_after_eval: float = 0.0,
    ) -> dict:
        """Navigate with stealth, run `eval_js` in the page, return its result.

        Powers the bot-detection self-check (browserscan/pixelscan/scrapfly).
        """
        await self.load()
        async with self._sem:
            page = await self._context.new_page()
            try:
                await self._inject_stealth(page)
                resp = await page.goto(url, wait_until="commit", timeout=int(wait * 1000))
                await self._safe_wait(page, wait_mode, wait)
                eval_result = await page.evaluate(eval_js)
                if wait_sec_after_eval > 0:
                    await asyncio.sleep(wait_sec_after_eval)
                return {
                    "final_url": page.url,
                    "target_status": resp.status if resp is not None else None,
                    "body": await page.content(),
                    "eval_result": eval_result,
                }
            finally:
                await page.close()

    async def _inject_stealth(self, page: Any) -> Any:
        """Register the stealth script in the page's MAIN world before navigation.

        patchright's `add_init_script` runs in an isolated world, so it never reaches
        the page's `navigator`. We register via raw CDP instead. `Page.enable` is
        REQUIRED for `addScriptToEvaluateOnNewDocument` to take effect through
        patchright's CDP wrapper; `runImmediately` also patches the current document.
        Verified: without `Page.enable` the profile values do NOT apply.

        Returns the CDP session so the caller can reuse it (e.g. `Page.stopLoading`)
        and detach it when done — one session per page, explicitly cleaned up, rather
        than leaking a session per scrape (which destabilises the driver under load).
        """
        if not self._stealth_js:
            return None
        cdp = await self._context.new_cdp_session(page)
        await cdp.send("Page.enable")
        await cdp.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": self._stealth_js, "runImmediately": True},
        )
        return cdp

    @staticmethod
    async def _safe_wait(page: Any, state: str, timeout: float) -> None:
        from patchright.async_api import Error as PWError

        try:
            await page.wait_for_load_state(state, timeout=int(timeout * 1000))
        except PWError:
            pass  # non-fatal: return whatever loaded
