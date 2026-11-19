from __future__ import annotations

import sys

from loguru import logger

from config import Settings


def setup_logging(settings: Settings, *, component: str) -> None:
    """Configure loguru for a process (resident or browser worker)."""
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <7}</level> | "
        "<cyan>{extra[component]}</cyan> | {extra[stage]} | {message}"
    )
    logger.configure(extra={"component": component, "stage": "-"})
    logger.add(sys.stderr, level=settings.log_level, format=fmt)
    logger.add(
        settings.log_dir / f"{component}.log",
        level=settings.log_level,
        format=fmt,
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        enqueue=True,
    )
