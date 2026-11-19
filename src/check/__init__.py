"""Bot-detection self-check — drives public detector sites and scores the verdict.

Ported from simple-scraper-api. Each module exposes `URL`, `WAIT_MODE`, `WAIT`,
`WAIT_SEC_AFTER_EVAL`, `EVAL_JS`, and `analyze(html, eval_result) -> CheckReport`.
Run via the `live` test (`tests/live/test_botcheck.py`).
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckReport:
    site: str
    url: str
    passed: bool
    verdict: str
    details: dict[str, Any] = field(default_factory=dict)
