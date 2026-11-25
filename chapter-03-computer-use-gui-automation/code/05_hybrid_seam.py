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
dialog, the vision model selects the dismiss mark from the marked
screenshot (``choose_mark_with_real_model``, via ``llm_client.py`` —
the Chapter 7 client copied into this chapter), and a DOM check routed
through the differ's verdict function (``verify_dismissed`` in ``_seam.py``)
verifies the dialog is gone before the scripted form fill resumes. When
no model backend is configured, the deterministic ``choose_mark`` in
``_seam.py`` stands in (pass ``--mock`` to force it); it matches on the
button's label text, so it is a generous stand-in for a model that has
to read the label off the screenshot.

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
    LLM_API_KEY=<redacted> LLM_BASE_URL=<your provider's API base URL> LLM_MODEL=<your model name> python 05_hybrid_seam.py [--self-test]
    # ... or with AWS Bedrock:
    LLM_BACKEND=bedrock LLM_BEDROCK_MODEL_ID=<your inference profile ID> python 05_hybrid_seam.py [--self-test]

--self-test exercises the failure policy and the mark allow-list without a
browser, so the seam logic is verifiable even where Playwright is not
installed. Add --approve to pause for a human y/n before the final submit
(the Chapter 8 human-in-the-loop pattern in miniature). Add --mock to
force the deterministic label-matching chooser instead of the real model.
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


def annotate_marks(screenshot_path: Path, marks: list[MarkedElement],
                   output_path: Path) -> Path:
    """Draw numbered Set-of-Mark markers at the marks' centres.

    The model chooses from the marked image, not from a bare list of
    labels: the labels are in the prompt for grounding, but the
    selection itself is visual, which is the point of the exercise.
    The marker is a red circle with the number, centred on the
    element (the dialog buttons are large enough that the marker
    does not hide the label).
    """
    from PIL import Image, ImageDraw

    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for mark in marks:
        cx, cy = mark.centre
        draw.ellipse([cx - 11, cy - 11, cx + 11, cy + 11],
                     fill=(220, 38, 38))
        draw.text((cx - 4, cy - 6), str(mark.number), fill=(255, 255, 255))
    image.save(output_path)
    return output_path


def choose_mark_with_real_model(screenshot_path: Path,
                                marks: list[MarkedElement],
                                client) -> int | None:
    """Ask a real vision model which marked button dismisses the dialog.

    The model sees the screenshot with numbered markers and the list
    of mark numbers and labels, and answers with a single integer.
    The allow-list is enforced here, not trusted to the model: a
    number that is not one of the marks is rejected (returns None),
    exactly as an unmarked coordinate is rejected. Returns None
    when the reply carries no usable mark number.
    """
    import re

    annotated_path = OUTPUT_DIR / "seam_annotated.png"
    annotate_marks(screenshot_path, marks, annotated_path)
    labels = ", ".join(f"{m.number}={m.name!r}" for m in marks)
    prompt = (
        "The image shows a pop-up dialog with numbered markers on its "
        "buttons. Which marker is on the button that dismisses the dialog "
        "(the button that closes it, labelled 'Dismiss', 'Close', "
        f"'Not now' or 'Got it')? The marks are: {labels}. Answer with a "
        "single integer and nothing else."
    )
    reply = client.complete_with_image(
        annotated_path, prompt, model=client.judge_model, temperature=0.0,
    )
    match = re.search(r"\d+", reply or "")
    if not match:
        return None
    picked = int(match.group(0))
    return picked if any(m.number == picked for m in marks) else None


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
    parser.add_argument("--mock", action="store_true",
                        help="force the deterministic label-matching chooser "
                             "instead of the real vision model")
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    # The delegated step calls a real vision model by default; the
    # deterministic label-matching chooser is the no-backend fallback.
    client = None
    if not args.mock:
        from llm_client import LLMClient

        if LLMClient.has_backend():
            client = LLMClient()
            print(f"Delegated step uses the real vision model "
                  f"(model {client.judge_model}).")
        else:
            print("No model backend configured; the delegated step uses "
                  "the deterministic label-matching chooser. Connect a "
                  "model (see \"Connect a model\" in the root README) "
                  "for the real-model delegation.")

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

                picked = (choose_mark_with_real_model(
                    handoff.screenshot_path, marks, client)
                    if client is not None
                    else choose_mark("dismiss", marks))
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
            if client is not None:
                print(f"Model usage for the delegated step: "
                      f"{client.total_prompt_tokens:,} prompt + "
                      f"{client.total_completion_tokens:,} completion tokens"
                      + (f", ${client.total_cost_usd:.4f}"
                         if client.total_cost_usd else " (no cost reported)"))
            print("OK — hybrid run complete: scripted steps, one delegated "
                  "step, verified at the seam.")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
