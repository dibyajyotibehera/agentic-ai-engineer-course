# Exercises — Chapter 3: Computer Use and GUI Automation

Work in `code/` in this chapter. Nothing here needs a paid service or an API key for its core acceptance criteria; the exercises that would call a hosted model are written against a mock chooser first, and the real-model call is a clearly marked TODO. Install the chapter dependencies first:

```bash
pip install -r code/requirements.txt
playwright install chromium
```

## Exercise 1 — Drive a form with DOM selectors (Foundation)

**Goal:** build the deterministic actor layer of the hybrid architecture: a Playwright script that fills and submits a form using role and label locators, never coordinates.

**What to do:** run `code/01_dom_form_driver.py`. It serves a small form page locally, drives it with Playwright's Python API (`get_by_label`, `get_by_role`), submits it, and asserts on the confirmation text. Read the script, then extend it: add a second field to the form page, drive it the same way, and add an assertion that the submitted values round-trip exactly.

**Acceptance criteria:**

- The script runs end to end with `python code/01_dom_form_driver.py` and exits 0, printing the submitted values it read back from the page.
- No step in the script uses coordinates, pixel positions or `mouse.click(x, y)`; every interaction addresses an element by role, label or text.
- The script waits for elements to become actionable (Playwright's auto-waiting) rather than sleeping for a fixed duration; there is no bare `time.sleep` before an interaction.
- Your added field is filled and asserted the same way as the originals.

**Stretch:** make the driver recover from a missing field by logging the page's accessibility snapshot (`page.accessibility.snapshot()`) and failing with the snapshot attached, instead of failing with a bare timeout. That snapshot is the artefact you would hand a model in Exercise 2.

## Exercise 2 — Set-of-Mark grounding: label, select, measure (Intermediate)

**Goal:** reproduce the core Set-of-Mark prompting result at small scale — show, with your own measurement, that selecting among numbered labels beats guessing coordinates — and measure the error of the coordinate mapping underneath it.

**What to do:** run `code/02_set_of_mark.py`. It opens a test page with Playwright, collects the bounding boxes of the interactive elements from the Document Object Model, draws numbered markers on a screenshot of the page, and saves the annotated image plus a JSON file mapping each number to its element and centre coordinates. A mock chooser (standing in for the vision model) then picks a number for a given target description, and the script scores the pick against ground truth and reports the click error in pixels. Then do the comparison the paper invites: run the same targets through the raw-coordinate path (the mock chooser guesses an `(x, y)` pair from the unmarked screenshot) and compare accuracy.

**Acceptance criteria:**

- `code/02_set_of_mark.py` runs and prints an accuracy table for both paths, with the labelled path more accurate than the raw-coordinate path on the same targets.
- The annotated screenshot (`code/output/som_annotated.png`) shows a legible number on every interactive element, and the JSON mapping (`code/output/som_mapping.json`) lists each number with the element's role, name and centre coordinates.
- The script reports the coordinate-mapping error separately: for at least five targets, the distance in pixels between the intended centre and the coordinate the mapping produced, and the largest error is under 5 pixels on the test page.
- You can explain, in one paragraph in your own words, why the labelled path wins — discrete selection among visible options versus continuous coordinate regression from a downscaled image — and where it still fails (overlapping elements, elements smaller than the marker, dynamic content that moves between labelling and clicking).

**Stretch:** shrink the targets — halve the button sizes in the test page — and re-run. Record how the raw-coordinate accuracy degrades faster than the labelled accuracy, and note the target size at which the labelled path starts to fail too. That threshold is the number you would carry into a production grounding budget.

**Real-model TODO:** the script has a clearly marked `TODO` where a hosted vision model call belongs. If you have an API key, wire it in there (the prompt is provided in the file) and re-run the comparison with a real model; keep the mock results alongside so the comparison is honest.

## Exercise 3 — Screenshot diffing: detect what changed (Intermediate)

**Goal:** build the verification layer — a differ that compares two screenshots, reports exactly which regions changed, and can tell a real change from rendering noise.

**What to do:** run `code/03_screenshot_diff.py`. It generates two synthetic screenshots with Pillow (a form page before and after a field is filled, plus a blinking cursor and an animated banner as noise), computes the pixel difference, thresholds it, extracts the bounding boxes of the changed regions, and writes a diff image with the changed regions outlined. Then tune it: the script exposes the threshold as a parameter, and your job is to find the threshold that reports the filled field and suppresses the cursor blink and the banner animation.

**Acceptance criteria:**

- The script runs with only Pillow and NumPy (`python code/03_screenshot_diff.py`) and exits 0.
- At your chosen threshold, the reported changed regions include the filled field and exclude the blinking cursor; the diff image (`code/output/diff.png`) shows the field outlined and the cursor region clean.
- The script prints, for each changed region, its bounding box and the percentage of the screen it covers, and prints the total percentage of changed pixels.
- You write down, in a comment or a short note, the failure mode of a threshold that is too low (noise reported as change) and of one that is too high (a subtle change, such as a single character edited in a text field, missed entirely) — and which of the two failures is worse for a verification layer, with a reason.

**Stretch:** run the differ on two real screenshots taken from the Exercise 1 form page before and after submission (Playwright's `page.screenshot()`), and confirm it reports the confirmation banner as the changed region. Then add a "change budget" rule to the differ: if more than 40% of the screen changed, report "page navigated or reloaded" instead of listing regions, because a full-page change is a different event from an in-page edit.

## Exercise 4 — Cost arithmetic: price a screenshot task against a DOM task (Foundation)

**Goal:** turn the lesson's cost figures into a model you can reuse, and find the break-even point where the screenshot path stops being defensible.

**What to do:** run `code/04_cost_arithmetic.py`. It implements the cost model from the lesson with the verified anchor figures — about 4,500 input tokens per request to declare the computer toolset, roughly 1,000–1,800 input tokens per screenshot, about 783,000 input tokens and about $1.61 for a 30-step task on Claude Sonnet 5.5 at list prices — and prints a table of cost per task and cost per 1,000 tasks for step counts of 5, 10, 30, 50 and 100, for the screenshot path and for a DOM path whose per-step observation cost you supply. The DOM path's observation cost is deliberately a parameter, not a hard-coded number: measure it yourself by capturing an accessibility-tree snapshot of a real page with Playwright (Exercise 1's stretch shows how) and counting its characters, converting at the rough heuristic of 4 characters per token, and plug the result in.

**Acceptance criteria:**

- The script runs with the standard library only (`python code/04_cost_arithmetic.py`) and exits 0.
- Its output reproduces the lesson's anchor: a 30-step screenshot task at about 783,000 input tokens and about $1.61 (within rounding — the script prints its own derivation, so you can see where the number comes from).
- You plug in a measured DOM observation size from a real page, and the script prints the cost ratio between the two paths at each step count, plus the step count at which the screenshot path costs ten times the DOM path.
- You answer, in a short note, the question the model exists to answer: for a workflow your team actually runs, how many tasks a month would the screenshot path have to handle before its extra cost exceeds one engineer-day of building the DOM path instead? State your assumptions (tasks per month, engineer-day cost) explicitly; the point is the reasoning, not the number.

**Stretch:** add prompt caching to the model — a cache that makes the repeated toolset declaration and the repeated early screenshots cheap on later steps — as a parameter (fraction of input tokens served from cache, and the cache discount). Re-run the table and note how much of the gap caching closes, and how much remains, because the per-step screenshot is the part caching cannot remove.

## Exercise 5 — The hybrid seam: script the known, delegate the unknown (Advanced)

**Goal:** design the seam between deterministic actors and the vision agent, and prove the seam holds when the page surprises you.

**What to do:** using the Exercise 1 driver as the actor layer, build a two-layer run against the test form page: the Playwright script performs login and navigation to the form (the known steps), and then hands control to the Set-of-Mark labeller from Exercise 2 for one deliberately unknown step — a pop-up dialog that appears on the page at a random position with a randomly labelled dismiss button. The labeller marks the dialog, the mock chooser selects the dismiss mark, and the script verifies with a DOM check routed through the differ's verdict function (`verify_dismissed` in `code/_seam.py`) that the dialog is gone before resuming the scripted form fill. The random pop-up is generated by `code/05_hybrid_seam.py`, which wires the three earlier pieces together; your work is the seam itself — the state handoff (what the actor passes to the agent: URL, screenshot, labelled elements, and what it gets back: a chosen mark and a verification result) and the failure policy.

**Acceptance criteria:**

- `code/05_hybrid_seam.py` runs end to end and exits 0, printing the handoff record for the delegated step: what was passed in, what was chosen, and the verification verdict on the outcome.
- The failure policy is implemented, not described: if the verification reports that the dialog is still present after the delegated click, the run retries the delegation once with a fresh screenshot and fresh labels, and if it still fails, it stops and reports the failure with the screenshot and the label mapping attached — it never proceeds to fill the form underneath an open dialog.
- There is a step cap (10 steps for the delegated portion) and a hard rule that the delegated agent may only click elements the labeller marked; an unmarked coordinate is rejected before it reaches the browser.
- You write down the seam decision for one real workflow you know (a vendor portal, an internal tool, a form you file regularly): which steps you would script, which you would delegate, and what the approval gate before the final submit would check.

**Stretch:** add the approval gate: before the form is submitted, the run pauses, prints the values it is about to submit, and waits for a human `y/n` on the terminal. This is the human-in-the-loop pattern Chapter 8 develops in full; here it is one prompt, and the point is to feel where it belongs — after the agent has done its work and before anything irreversible happens.
