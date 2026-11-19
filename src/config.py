"""Settings for the lightweight resident process.

Paths are derived from the project root and never read from `.env` — only
tunables and secrets live in the environment.
"""
from __future__ import annotations

import getpass
import os
import re
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_FILE = _PROJECT_ROOT / ".env"
_ENV_EXAMPLE_FILE = _PROJECT_ROOT / ".env.example"


def _user_tag() -> str:
    try:
        name = getpass.getuser()
    except Exception:  # noqa: BLE001
        name = "default"
    return re.sub(r"[^A-Za-z0-9_-]", "_", name) or "default"


_USER_TAG = _user_tag()

# Minimal fallback if .env.example is absent (e.g. a stripped deployment).
_ENV_FALLBACK = "SCRAPER_API_KEY=\nSCRAPER_HOST=127.0.0.1\nSCRAPER_PORT=19020\n"


def _ensure_env_file() -> None:
    """Create `.env` from `.env.example` on first run; never overwrite it."""
    if _ENV_FILE.exists():
        return
    if _ENV_EXAMPLE_FILE.exists():
        _ENV_FILE.write_text(
            _ENV_EXAMPLE_FILE.read_text(encoding="utf-8"), encoding="utf-8",
        )
    else:
        _ENV_FILE.write_text(_ENV_FALLBACK, encoding="utf-8")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Public API
    scraper_api_key: str = ""
    scraper_host: str = "127.0.0.1"
    scraper_port: int = 19020

    # Browser worker
    scraper_profile: str = "ru-rtx3060"
    page_concurrency: int = 10
    # Max URLs from a single /scrap/batch call loaded in parallel.
    scrape_batch_concurrency: int = 10

    # Browser worker lifecycle
    browser_idle_evict_minutes: float = 5.0
    browser_daemon_shutdown_minutes: float = 30.0
    daemon_request_timeout_seconds: float = 180.0
    daemon_spawn_health_timeout_seconds: float = 60.0
    daemon_spawn_retries: int = 2
    daemon_host: str = "127.0.0.1"

    # Where port/lock/pid files, logs and the browser profile live.
    runtime_dir: Path = _PROJECT_ROOT / "runtime"
    log_dir: Path = _PROJECT_ROOT / "runtime" / "logs"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_rotation: str = "10 MB"
    log_retention: str = "7 days"

    @property
    def user_tag(self) -> str:
        return _USER_TAG

    @property
    def profile_dir(self) -> Path:
        """Persistent Chromium user-data dir for the worker."""
        return self.runtime_dir / "profile"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _ensure_env_file()
        _settings = Settings()
    return _settings
