# Chapter 3 code — Computer Use and GUI Automation

Run everything from this directory:

```bash
pip install -r requirements.txt
playwright install chromium
python 01_dom_form_driver.py
python 02_set_of_mark.py
python 03_screenshot_diff.py
python 04_cost_arithmetic.py
python 05_hybrid_seam.py --self-test
python -m unittest test_chapter03 -v
```

## The model calls are real

`llm_client.py` is the Chapter 7 client copied verbatim into this
chapter (plus one method, `complete_with_image`, which sends a
screenshot alongside a text prompt as a base64 data URI — the only
addition, and still standard library only), so each chapter stays
self-contained. Exercises 2 and 5 call a real vision model by
default when a backend is configured:

```bash
LLM_API_KEY=<redacted> LLM_BASE_URL=<your provider's API base URL> LLM_MODEL=<your model name> python 02_set_of_mark.py
# ... or with AWS Bedrock:
LLM_BACKEND=bedrock LLM_BEDROCK_MODEL_ID=<your inference profile ID> python 02_set_of_mark.py
```

Connect a model first — see "Connect a model" in the root
README for the two paths (key-based, or AWS Bedrock). Both model
calls use the configured judge model (`LLM_JUDGE_MODEL`,
defaulting to the main model) with a small token cap: a full
Exercise 2 run measured 9,009 prompt +
917 completion tokens, $0.0030. Never print or store a key; the
client raises loudly when none is configured.

## What deliberately stays a stand-in

One paragraph, so a reader can tell a deliberate stand-in from an
unfinished one. Two mocks remain, both as the no-backend fallback,
and both are labelled as such where they run. In Exercise 2 the
deterministic mock choosers (`mock_labelled_choice`,
`mock_raw_coordinate_guess`) stand in for the vision model only
when no backend is configured, so the exercise still runs with no
API key; pass `--mock` to force them, `--real-model` to fail
loudly instead of falling back. In Exercise 5 the deterministic
`choose_mark` in `_seam.py` matches the dismiss button by label
text and stands in for the model only when no backend is
configured. The DOM labeller standing in for a segmentation model
(Segment Anything Model) is a different kind of stand-in: the
labelling step is deterministic code in both cases, and the browser
gives exact bounding boxes, so the substitution changes the source
of the boxes, not the thing being measured. The synthetic
screenshots in Exercise 3 are deliberate test fixtures — a form
page with a blinking cursor and an animated banner as noise — not
stand-ins for a model or for real input; the differ is real pixel
arithmetic either way, and `--before/--after` runs it on two real
screenshot files. The 300-token DOM observation default in
Exercise 4 is a labelled placeholder, not a mock: measure a real
accessibility snapshot (Exercise 1's `--a11y` flag shows how) and
pass it with `--dom-tokens`.

## Where a real browser cannot run

Exercises 1, 2 and 5 need Playwright with its Chromium download.
Where that download is unavailable (this sandbox, for one),
Exercise 2 renders the same test page with Pillow instead and says
so plainly: the image is a real PNG file and the bounding boxes are
the exact rectangles drawn, so the model calls against it are
genuine and only the renderer differs. Exercises 1 and 5 have no
such fallback — a browser run cannot be faked — so Exercise 1
exits with a clear "Playwright is not installed" message and
Exercise 5's full run does the same, while its `--self-test`
exercises the seam policy without a browser.

## Files

- `01_dom_form_driver.py` — Exercise 1: drive a form with DOM
  selectors (Playwright; no model involved).
- `02_set_of_mark.py` — Exercise 2: Set-of-Mark grounding; real
  vision-model calls for both the labelled and the raw-coordinate
  path, mock fallback without a backend, Pillow renderer fallback
  without a browser. Writes `output/som_plain.png`,
  `output/som_annotated.png`, `output/som_mapping.json`.
- `03_screenshot_diff.py` — Exercise 3: screenshot differ with
  threshold tuning; `--before/--after` diffs two real files.
- `04_cost_arithmetic.py` — Exercise 4: screenshot-versus-DOM
  cost model (standard library only).
- `05_hybrid_seam.py` — Exercise 5: the hybrid seam; the delegated
  dismiss step calls the real vision model, with the deterministic
  chooser as the no-backend fallback. `--self-test` runs the seam
  policy without a browser.
- `_seam.py` — shared seam policy for Exercise 5: handoff record,
  mark allow-list, retry policy, verification verdict. Pure policy;
  no browser, no model.
- `llm_client.py` — the shared model client (see above).
- `test_chapter03.py` — deterministic tests (no model, no
  browser) plus live tests that skip without a backend and call
  the real vision model against real rendered images.
