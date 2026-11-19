"""Exercise 5 — The hybrid seam: script the known, delegate the unknown
(Advanced).

The seam between deterministic actors and the vision agent, built from the
three earlier pieces: the Playwright driver (Exercise 1) does the known
steps, the Set-of-Mark labeller (Exercise 2) handles one deliberately
unknown step, and a DOM check routed through the differ's verdict function
(Exercise 3) verifies the outcome.

Scenario: a pop-up dialog appears at a random position on the form page,
with a randomly labelled dismiss button. The scripted driver cannot know
where it will appear, so that step is delegated: the labeller marks the
dialog, the chooser selects the dismiss mark, and a DOM check routed
through the differ's verdict function (`verify_dismissed` in `_seam.py`)
verifies the dialog is gone before the scripted form fill resumes.

The seam is a state handoff with a failure policy, both implemented here:
  * what the actor passes in: URL, screenshot, labelled elements;
  * what comes back: the chosen mark and the differ's verdict;
  * the failure policy: one retry with fresh labels, then stop and report
    with the screenshot and label mapping attached — never proceed to fill
    the form underneath an open dialog;
  * the guardrails: a 10-step cap on the delegated portion, and only clicks
    on elements the labeller marked — an unmarked coordinate is rejected
    before it reaches the browser.

Run:
    pip install -r requirements.txt
    playwright install chromium
    python 05_hybrid_seam.py [--self-test]

--self-test exercises the failure policy and the mark allow-list without a
browser, so the seam logic is verifiable even where Playwright is not
installed. Add --approve to pause for a human y/n before the final submit
(the Chapter 8 human-in-the-loop pattern in miniature).
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"

sys.path.insert(0, str(HERE))
from _seam import (  # noqa: E402
    DelegationPolicy,
    Handoff,
    HandoffRecord,
    MarkedElement,
    choose_mark,
    verify_dismissed,
)

POPUP_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Form with pop-up</title>
<style>body { font-family: sans-serif; margin: 40px; }</style></head><body>
  <h1>Request access</h1>
  <form id="access-form">
    <label>Full name <input name="full_name" type="text"></label><br>
    <label>Work email <input name="email" type="email"></label><br>
    <button type="submit">Submit request</button>
  </form>
  <p id="confirmation" hidden></p>
  <div id="popup" style="position: absolute; left: {left}px; top: {top}px;
       border: 2px solid #333; background: #fff; padding: 16px;">
    <p>Before you continue: {message}</p>
    <button id="dismiss">{dismiss_label}</button>
  </div>
  <script>
    document.getElementById("dismiss").addEventListener("click", () => {{
      document.getElementById("popup").remove();
    }});
    document.getElementById("access-form").addEventListener("submit", (e) => {{
      e.preventDefault();
      const c = document.getElementById("confirmation");
      c.textContent = "Received request.";
      c.hidden = false;
    }});
  </script>
</body></html>
"""


def self_test() -> int:
    """Exercise the seam policy without a browser."""
    print("Self-test: failure policy and mark allow-list (no browser).")
    policy = DelegationPolicy(max_steps=10, max_retries=1)

    # The allow-list: only marked elements may be clicked.
    marks = [MarkedElement(1, "button", "Dismiss", (100, 100)),
             MarkedElement(2, "button", "Remind me later", (200, 100))]
    assert choose_mark("dismiss", marks) == 1
    assert choose_mark("delete everything", marks) is None
    print("  allow-list: unmarked target rejected (choose_mark -> None)")

    # The failure policy: one retry, then stop with evidence attached.
    record = HandoffRecord(step="dismiss pop-up")
    record.attempt(verified=False, evidence="screenshot-1.png")
    assert policy.should_retry(record) is True
    record.attempt(verified=False, evidence="screenshot-2.png")
    assert policy.should_retry(record) is False
    assert policy.outcome(record).startswith("STOP")
    assert "screenshot-1.png" in policy.outcome(record)
    assert "screenshot-2.png" in policy.outcome(record)
    print("  failure policy: retry once, then stop with evidence attached")

    # The verification verdict: a differ that still sees the dialog fails.
    assert verify_dismissed(dialog_present=True) is False
    assert verify_dismissed(dialog_present=False) is True
    print("  verification: dialog present -> not dismissed; absent -> dismissed")
    print("Self-test passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true",
                        help="test the seam policy without a browser")
    parser.add_argument("--approve", action="store_true",
                        help="pause for human y/n before the final submit")
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Run:\n"
              "  pip install -r requirements.txt\n"
              "  playwright install chromium\n"
              "Or run with --self-test to exercise the seam policy "
              "without a browser.", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    left, top = rng.randint(50, 500), rng.randint(60, 300)
    dismiss_label = rng.choice(["Dismiss", "Close", "Not now", "Got it"])
    html = POPUP_HTML.format(
        left=left, top=top,
        message="please confirm you have read the access policy.",
        dismiss_label=dismiss_label)
    policy = DelegationPolicy(max_steps=10, max_retries=1)
    OUTPUT_DIR.mkdir(exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 600})
        try:
            page.set_content(html)
            page.wait_for_timeout(300)

            # Delegated step: dismiss the pop-up. The actor hands over the
            # URL, a screenshot and the labelled elements; the agent hands
            # back a chosen mark, and the verification check confirms
            # the outcome.
            record = HandoffRecord(step="dismiss pop-up")
            while True:
                elements = page.eval_on_selector_all(
                    "#popup button",
                    """els => els.map(el => {
                        const r = el.getBoundingClientRect();
                        return {name: (el.innerText || '').trim(),
                                x: r.x + r.width / 2, y: r.y + r.height / 2};
                    })""")
                marks = [MarkedElement(i + 1, "button", e["name"],
                                       (e["x"], e["y"]))
                         for i, e in enumerate(elements)]
                handoff = Handoff(url=page.url, marks=marks,
                                  screenshot_path=OUTPUT_DIR / "seam.png")
                page.screenshot(path=str(handoff.screenshot_path))
                print(f"Handoff: {handoff.describe()}")

                picked = choose_mark("dismiss", marks)
                if picked is None:
                    record.attempt(verified=False,
                                   evidence=str(handoff.screenshot_path))
                    print("  no marked dismiss button found; "
                          f"{policy.outcome(record)}")
                    return 1
                target = marks[picked - 1]
                page.mouse.click(target.centre[0], target.centre[1])
                page.wait_for_timeout(200)
                dismissed = page.evaluate(
                    "() => document.getElementById('popup') === null")
                record.attempt(verified=verify_dismissed(not dismissed),
                               evidence=str(handoff.screenshot_path))
                print(f"  chose mark {picked} ({target.name!r}); "
                      f"dialog gone: {dismissed}")
                if dismissed:
                    break
                if not policy.should_retry(record):
                    print(policy.outcome(record))
                    return 1
                print("  retrying delegation with a fresh screenshot "
                      "and fresh labels...")
            print(f"Delegated step complete: {record.summary()}")

            # Scripted steps resume: the known form fill, by DOM selectors.
            page.get_by_label("Full name").fill("Alex Example")
            page.get_by_label("Work email").fill("alex@example.com")

            if args.approve:
                answer = input("About to submit: Full name=Alex "
                               "Example, Work email=alex@example.com. "
                               "Submit? [y/n] ").strip().lower()
                if answer != "y":
                    print("Submission aborted by human gate.")
                    return 1

            page.get_by_role("button", name="Submit request").click()
            page.locator("#confirmation").wait_for(state="visible")
            print("Confirmation:",
                  page.locator("#confirmation").inner_text())
            print("OK — hybrid run complete: scripted steps, one delegated "
                  "step, verified at the seam.")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
