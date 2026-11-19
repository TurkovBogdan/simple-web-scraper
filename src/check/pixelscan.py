from typing import Any

from check import CheckReport

URL = "https://pixelscan.net/bot-check"
WAIT_MODE = "load"
WAIT = 30.0
WAIT_SEC_AFTER_EVAL = 0.0

EVAL_JS = r"""
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const state = () => document.querySelector('.checker-header')?.getAttribute('state');
  const final = s => s === 'success' || s === 'error';
  // Since 2026-11 the check runs by itself on load, and clicking "Start Check" restarts
  // it into a run that never finishes. Take the page's own verdict; click only if the
  // check has not started on its own (the old flow).
  for (let i = 0; i < 60; i++) {
    const s = state();
    if (final(s)) return {state: s};
    if (i >= 30 && s !== 'loading') break;
    await sleep(500);
  }
  const btn = [...document.querySelectorAll('button')]
    .find(b => /start check/i.test(b.textContent));
  if (!btn) return {state: null, error: 'no_start_button'};
  btn.click();
  for (let i = 0; i < 60; i++) {
    await sleep(500);
    const s = state();
    if (final(s)) return {state: s};
  }
  return {state: 'timeout'};
})()
"""


_VERDICT_BY_STATE = {
    "success": "You're Definitely a Human",
    "error": "Bot Behavior Detected",
    "timeout": "check timed out (no verdict in 30s)",
}


def analyze(html: str | None, eval_result: Any) -> CheckReport:
    er = eval_result if isinstance(eval_result, dict) else {}
    state = er.get("state")
    passed = state == "success"
    verdict = _VERDICT_BY_STATE.get(state) or er.get("error") or f"unexpected: {er}"
    return CheckReport(
        site="pixelscan.net",
        url=URL,
        passed=passed,
        verdict=verdict,
        details=er,
    )
