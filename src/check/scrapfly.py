from typing import Any

from check import CheckReport

URL = "https://scrapfly.io/web-scraping-tools/automation-detector"
WAIT_MODE = "load"
WAIT = 30.0
WAIT_SEC_AFTER_EVAL = 0.0

EVAL_JS = r"""
(async () => {
  const text = (id) => {
    const el = document.getElementById(id);
    return el ? el.textContent.trim() : null;
  };
  const num = (id) => {
    const v = parseInt(text(id) || '', 10);
    return Number.isFinite(v) ? v : null;
  };
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 500));
    const v = text('verdict-text');
    if (v && !/Running|Checking/i.test(v)) break;
  }
  return {
    verdict: text('verdict-text'),
    description: text('verdict-description'),
    detected: num('stat-detected'),
    safe: num('stat-safe'),
    suspicious: num('stat-suspicious'),
    total: num('stat-total'),
  };
})()
"""


def analyze(html: str | None, eval_result: Any) -> CheckReport:
    er = eval_result if isinstance(eval_result, dict) else {}
    verdict = (er.get("verdict") or "").strip()
    detected = er.get("detected")
    passed = detected == 0 and "Not Automated" in verdict
    verdict_text = verdict or f"unexpected: {er}"
    return CheckReport(
        site="scrapfly.io",
        url=URL,
        passed=passed,
        verdict=verdict_text,
        details=er,
    )
