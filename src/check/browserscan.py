from typing import Any

from check import CheckReport

URL = "https://www.browserscan.net/bot-detection"
WAIT_MODE = "load"
WAIT = 30.0
WAIT_SEC_AFTER_EVAL = 0.0

EVAL_JS = r"""
(async () => {
  const labels = ['WebDriver','WebDriver Advance','Selenium','NightmareJS','PhantomJS',
                  'Awesomium','Cef','CefSharp','Coaches','FMiner','Born','Phantomas',
                  'Rhino','Webdriverio','Headless Chrome','CDP','Dev Tool'];
  // Each sub-test row renders as <h3>Label</h3> + status sibling text;
  // climb h3's ancestors until textContent is "<label><status>" concatenated.
  const findStatus = (label) => {
    const h3 = [...document.querySelectorAll('h3')]
      .find(el => el.textContent.trim() === label);
    if (!h3) return null;
    let p = h3;
    for (let i = 0; i < 6; i++) {
      p = p.parentElement;
      if (!p) return null;
      const t = p.textContent.trim();
      if (t.length > label.length && t.startsWith(label)) {
        return t.slice(label.length).trim();
      }
    }
    return null;
  };
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 500));
    const got = findStatus('WebDriver');
    if (got && got !== '...') break;
  }
  const results = {};
  for (const label of labels) results[label] = findStatus(label);
  return results;
})()
"""


def analyze(html: str | None, eval_result: Any) -> CheckReport:
    results = eval_result if isinstance(eval_result, dict) else {}
    failures = {k: v for k, v in results.items() if v and v != "Normal"}
    missing = [k for k, v in results.items() if v in (None, "")]
    passed = not failures and not missing
    if passed:
        verdict = f"all {len(results)} checks Normal"
    elif failures:
        verdict = f"{len(failures)} non-Normal: {failures}"
    else:
        verdict = f"{len(missing)} test(s) not extracted: {missing}"
    return CheckReport(
        site="browserscan.net",
        url=URL,
        passed=passed,
        verdict=verdict,
        details={"results": results, "failures": failures, "missing": missing},
    )
