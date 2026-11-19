"""Scroll to bottom via CDP Input.synthesizeScrollGesture.

Ported from simple-scraper-api (logging swapped to loguru) — see memory ref
[[ref-simple-scraper-api]]. Unlike mouse.wheel, this synthesizes a physically
plausible wheel gesture routed through Chromium's kinetic-scroll controller, with
a background poll that catches lazy-load growth mid-gesture. Best-effort: never
raises.
"""
from __future__ import annotations

import asyncio
import random

from loguru import logger

log = logger.bind(component="worker", stage="scroll")

_SCROLL_MAX_ITERS = 25
_SCROLL_MAX_HEIGHT = 40_000
_SCROLL_TOLERANCE = 60
_SCROLL_SEGMENT_MIN = 1200
_SCROLL_SEGMENT_MAX = 10_000   # one long gesture with a background poll
_SCROLL_POLL_INTERVAL = 0.1    # how often we poll scrollHeight in the background
_SCROLL_SPEED_SLOW = 800       # px/s near the very bottom
_SCROLL_SPEED_FAST = 5500      # px/s at cruise
_SCROLL_FAR_DISTANCE = 6000    # distance at which speed = cruise
_SCROLL_BOTTOM_VERIFY = 1.2    # pause after "reached bottom" to catch lazy-load
_SCROLL_MOUSE_X_FRAC = (0.05, 0.15)  # left zone: 5–15% of width (avoid sliders)
_SCROLL_MOUSE_Y_FRAC = (0.30, 0.70)  # middle vertically


async def scroll_to_bottom(page) -> bool:
    vp = page.viewport_size or {"width": 1280, "height": 800}
    w, h_vp = vp["width"], vp["height"]
    px = w * random.uniform(*_SCROLL_MOUSE_X_FRAC)
    py = h_vp * random.uniform(*_SCROLL_MOUSE_Y_FRAC)

    try:
        cdp = await page.context.new_cdp_session(page)
    except Exception:
        log.opt(exception=True).warning("cdp session failed")
        return False

    try:
        try:
            await page.mouse.move(px, py)
        except Exception:
            log.opt(exception=True).warning("mouse.move failed")
            return False

        for i in range(_SCROLL_MAX_ITERS):
            try:
                m = await page.evaluate(
                    "({y: window.scrollY, vh: window.innerHeight, "
                    "h: document.documentElement.scrollHeight})"
                )
            except Exception:
                log.opt(exception=True).warning("metrics failed at iter={}", i)
                return False

            y, vh, h = m["y"], m["vh"], m["h"]
            distance = h - vh - y

            if distance <= _SCROLL_TOLERANCE:
                await asyncio.sleep(_SCROLL_BOTTOM_VERIFY)
                try:
                    h_after = await page.evaluate("document.documentElement.scrollHeight")
                except Exception:
                    log.opt(exception=True).warning("verify metrics failed at iter={}", i)
                    return True
                if h_after > h:
                    log.info("bottom at iter={} but feed grew ({} -> {}), continue", i, h, h_after)
                    continue
                log.info("bottom confirmed at iter={} (h={})", i, h)
                return True
            if h >= _SCROLL_MAX_HEIGHT:
                log.info("height cap (h={}) at iter={}", h, i)
                return False

            segment = min(_SCROLL_SEGMENT_MAX, max(_SCROLL_SEGMENT_MIN, distance))
            ratio = max(0.0, min(1.0, distance / _SCROLL_FAR_DISTANCE))
            speed = int(_SCROLL_SPEED_SLOW
                        + (_SCROLL_SPEED_FAST - _SCROLL_SPEED_SLOW) * ratio)

            # Run the gesture in the background and poll scrollHeight in parallel
            # so we catch lazy-load growth while scrolling.
            gesture_task = asyncio.create_task(cdp.send(
                "Input.synthesizeScrollGesture", {
                    "x": px,
                    "y": py,
                    "yDistance": -segment,
                    "speed": speed,
                    "gestureSourceType": "mouse",
                },
            ))
            grew_during_gesture = False
            last_seen_h = h
            try:
                while not gesture_task.done():
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(gesture_task),
                            timeout=_SCROLL_POLL_INTERVAL,
                        )
                        break
                    except asyncio.TimeoutError:
                        pass
                    try:
                        cur_h = await page.evaluate(
                            "document.documentElement.scrollHeight"
                        )
                    except Exception:
                        continue
                    if cur_h > last_seen_h:
                        grew_during_gesture = True
                        last_seen_h = cur_h
                await gesture_task
            except Exception:
                log.opt(exception=True).warning("gesture failed at iter={}", i)
                if not gesture_task.done():
                    gesture_task.cancel()
                return False

            try:
                new_y = await page.evaluate("window.scrollY")
            except Exception:
                log.opt(exception=True).warning("scrollY read failed at iter={}", i)
                return False

            moved = abs(new_y - y) >= 1
            if not moved and not grew_during_gesture:
                log.info("stuck at iter={} (y={} h={})", i, y, h)
                return distance <= _SCROLL_TOLERANCE

            if grew_during_gesture:
                log.info("iter={} gesture moved {}->{}, page grew {}->{}",
                         i, y, new_y, h, last_seen_h)

        log.info("max_iters={} exhausted", _SCROLL_MAX_ITERS)
        return False
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass
