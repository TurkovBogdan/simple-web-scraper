"""Browser fingerprint profile loader.

Profiles live in `src/browser/profiles/<name>.json`. The profile name is the file
name without extension. The active profile is chosen by `SCRAPER_PROFILE`
(default `ru-rtx3060`). Ported from simple-scraper-api — see memory ref
[[ref-simple-scraper-api]].
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
DEFAULT_PROFILE = "ru-rtx3060"


class _Viewport(BaseModel):
    width: int
    height: int


class _Screen(BaseModel):
    width: int
    height: int
    avail_width: int
    avail_height: int
    color_depth: int = 24


class _Brand(BaseModel):
    brand: str
    version: str


class _Navigator(BaseModel):
    platform: str
    vendor: str
    hardware_concurrency: int
    device_memory: int
    max_touch_points: int


class _UserAgentData(BaseModel):
    platform: str
    platform_version: str
    architecture: str
    bitness: str
    mobile: bool
    model: str
    brands: list[_Brand]
    full_version_list: list[_Brand]
    ua_full_version: str


class _WebGL(BaseModel):
    vendor: str
    renderer: str


class BrowserProfile(BaseModel):
    name: str = Field(..., description="Profile name (= file name without .json)")
    chrome_version: str
    user_agent: str
    app_version: str
    locale: str
    timezone: str
    languages: list[str]
    primary_language: str
    viewport: _Viewport
    screen: _Screen
    device_pixel_ratio: float = 1.0
    navigator: _Navigator
    user_agent_data: _UserAgentData
    webgl: _WebGL


def list_profiles() -> list[str]:
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))


def load_profile(name: str | None = None) -> BrowserProfile:
    name = name or os.environ.get("SCRAPER_PROFILE", DEFAULT_PROFILE)
    if not name or name == "default":
        name = DEFAULT_PROFILE
    path = PROFILES_DIR / f"{name}.json"
    if not path.is_file():
        available = ", ".join(list_profiles()) or "(none)"
        raise FileNotFoundError(
            f"browser profile {name!r} not found at {path}. Available: {available}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    data["name"] = name
    return BrowserProfile.model_validate(data)


__all__ = ["BrowserProfile", "PROFILES_DIR", "DEFAULT_PROFILE", "load_profile", "list_profiles"]
