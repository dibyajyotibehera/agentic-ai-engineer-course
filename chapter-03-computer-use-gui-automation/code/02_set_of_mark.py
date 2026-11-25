"""Exercise 2 — Set-of-Mark grounding: label, select, measure (Intermediate).

Reproduces the core Set-of-Mark prompting result (arXiv:2310.11441) at small scale: instead of asking a vision model to guess
coordinates from a bare screenshot, label every interactive element with a
number, let the model select a number, and translate the number back into
coordinates with deterministic code.

This script uses the page's Document Object Model (DOM) as the labeller — the
bounding boxes come from the browser, which is exact — standing in for a
segmentation model such as the Segment Anything Model (SAM) that a
screenshot-only pipeline would use. The comparison is what matters: the same
targets are attempted through the labelled path and through a raw-coordinate
path, and accuracy is reported for both.

Both paths now call a real vision model by default
(``llm_client.py``, the Chapter 7 client copied into this chapter;
connect a model as described in "Connect a model" in the root
README — key-based with ``LLM_API_KEY``, ``LLM_BASE_URL`` and
``LLM_MODEL``, or ``LLM_BACKEND=bedrock`` with
``LLM_BEDROCK_MODEL_ID``). The labelled path sends the
annotated screenshot and asks for a mark number; the raw-coordinate
path sends the plain screenshot and asks for an (x, y) pair on the
0-1000 grid. Both calls use the configured judge model
(``LLM_JUDGE_MODEL``, defaulting to the main model)
with a small token cap, so a full run costs cents. When no backend
is configured, the script falls back to the deterministic mock
choosers below — labelled as such — so the exercise still runs with
no API key; pass ``--mock`` to force that fallback even when a
backend is configured, and ``--real-model`` to fail loudly instead
of falling back.

The browser is the one part that cannot be faked: when Playwright
or its Chromium download is unavailable, the script renders the
same test page with Pillow instead (``render_test_page_pillow``)
and says so plainly. The rendered page is a real image file with
exact bounding boxes taken from the drawing code, so the model
calls against it are genuine; only the page source changes, from
a browser render to a Pillow render.

Outputs (in code/output/):
  som_plain.png     — the screenshot of the test page.
  som_annotated.png — the screenshot with a numbered marker on each element.
  som_mapping.json  — number -> {role, name, centre coordinates}.

Run:
    pip install -r requirements.txt
    playwright install chromium
    LLM_API_KEY=<redacted> LLM_BASE_URL=<your provider's API base URL> LLM_MODEL=<your model name> python 02_set_of_mark.py [--mock]
    # ... or with AWS Bedrock:
    LLM_BACKEND=bedrock LLM_BEDROCK_MODEL_ID=<your inference profile ID> python 02_set_of_mark.py [--mock]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"

# The test page: five targets of decreasing size, so the comparison shows
# where raw-coordinate guessing breaks first.
TEST_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Grounding targets</title>
<style>
  body { font-family: sans-serif; margin: 40px; }
  .row { margin: 24px 0; }
  button { font-size: 16px; padding: 10px 18px; }
  .small { font-size: 11px; padding: 3px 6px; }
  .tiny { font-size: 9px; padding: 1px 3px; }
  input { font-size: 16px; padding: 8px; }
</style></head><body>
  <h1>Grounding targets</h1>
  <div class="row"><button id="save">Save draft</button></div>
  <div class="row"><button id="publish">Publish report</button></div>
  <div class="row"><label>Search <input id="search" type="text"></label></div>
  <div class="row"><button id="export" class="small">Export CSV</button></div>
  <div class="row"><button id="help" class="tiny">?</button></div>
</body></html>
"""

# (target description, keyword that identifies it, element id)
TARGETS = [
    ("the Save draft button", "save draft", "save"),
    ("the Publish report button", "publish report", "publish"),
    ("the Search text field", "search", "search"),
    ("the small Export CSV button", "export csv", "export"),
    ("the tiny help button marked ?", "?", "help"),
]

# Geometry of the same test page for the Pillow renderer (900x600, the
# same viewport the browser path uses). The boxes are the ground truth
# for the rendered page: the drawing code below draws exactly these
# rectangles, so the mapping is exact by construction, not measured.
PILLOW_ELEMENTS = [
    {"tag": "button", "name": "Save draft", "id": "save",
     "x": 40, "y": 100, "width": 150, "height": 44},
    {"tag": "button", "name": "Publish report", "id": "publish",
     "x": 40, "y": 180, "width": 175, "height": 44},
    {"tag": "input", "name": "Search", "id": "search",
     "x": 115, "y": 258, "width": 220, "height": 40},
    {"tag": "button", "name": "Export CSV", "id": "export",
     "x": 40, "y": 340, "width": 115, "height": 28},
    {"tag": "button", "name": "?", "id": "help",
     "x": 40, "y": 410, "width": 28, "height": 22},
]
PILLOW_VIEWPORT = (900, 600)


# --------------------------------------------------------------------------
# Coordinate mapping
# --------------------------------------------------------------------------

class CoordinateMapper:
    """Map between the model's normalised 0-1000 grid and screen pixels.

    Every frame of reference is named: the model grid, the screenshot pixels
    (which may be downscaled from the screen), and the screen pixels. The
    transform is a scale on each axis plus an offset, applied in order, and
    every mapping is logged so a systematic offset is diagnosable.
    """

    def __init__(self, screenshot_width: int, screenshot_height: int,
                 screen_width: int, screen_height: int,
                 viewport_offset_x: int = 0, viewport_offset_y: int = 0):
        self.screenshot_width = screenshot_width
        self.screenshot_height = screenshot_height
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.viewport_offset_x = viewport_offset_x
        self.viewport_offset_y = viewport_offset_y

    def model_to_screen(self, x_1000: float, y_1000: float) -> tuple[float, float]:
        """Normalised model coordinates -> screen pixels."""
        shot_x = x_1000 / 1000.0 * self.screenshot_width
        shot_y = y_1000 / 1000.0 * self.screenshot_height
        screen_x = (shot_x / self.screenshot_width * self.screen_width
                    + self.viewport_offset_x)
        screen_y = (shot_y / self.screenshot_height * self.screen_height
                    + self.viewport_offset_y)
        return screen_x, screen_y

    def screen_to_model(self, x: float, y: float) -> tuple[float, float]:
        """Screen pixels -> normalised model coordinates (for logging)."""
        shot_x = (x - self.viewport_offset_x) / self.screen_width * self.screenshot_width
        shot_y = (y - self.viewport_offset_y) / self.screen_height * self.screenshot_height
        return shot_x / self.screenshot_width * 1000.0, shot_y / self.screenshot_height * 1000.0


# --------------------------------------------------------------------------
# Page rendering: browser first, Pillow fallback
# --------------------------------------------------------------------------

def render_test_page_pillow():
    """Draw the test page with Pillow and return (image, elements).

    Used when Playwright or its Chromium download is unavailable.
    The image is a real PNG file, and the element boxes are the exact
    rectangles drawn, so the mapping and the model calls against the
    image are genuine — only the renderer differs from the browser
    path. The layout mirrors TEST_HTML: the same five targets, in
    the same order, at the same 900x600 viewport.
    """
    from PIL import Image, ImageDraw

    width, height = PILLOW_VIEWPORT
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((40, 30), "Grounding targets", fill=(0, 0, 0))
    # The Search label sits to the left of its text field, as in the HTML.
    draw.text((40, 270), "Search", fill=(0, 0, 0))
    for element in PILLOW_ELEMENTS:
        x, y = element["x"], element["y"]
        w, h = element["width"], element["height"]
        draw.rectangle([x, y, x + w, y + h], outline=(90, 90, 90), width=2)
        # Centre the label inside its box.
        label = element["name"]
        text_width = draw.textlength(label)
        text_height = 11
        draw.text((x + (w - text_width) / 2, y + (h - text_height) / 2),
                  label, fill=(0, 0, 0))
    return image, [dict(element) for element in PILLOW_ELEMENTS]


def capture_test_page_playwright():
    """Render the test page in Chromium and return (image, elements).

    Raises ImportError when Playwright is not installed, and raises
    the browser error when Chromium cannot launch (for example its
    download failed). The caller falls back to the Pillow renderer
    in both cases and says so.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.set_content(TEST_HTML)
        page.wait_for_timeout(300)
        elements = page.eval_on_selector_all(
            "button, input, select, a[href]",
            """els => els.map(el => {
                const r = el.getBoundingClientRect();
                return {tag: el.tagName.toLowerCase(),
                        name: (el.innerText || el.getAttribute('aria-label')
                               || el.id || '').trim(),
                        id: el.id,
                        x: r.x, y: r.y, width: r.width, height: r.height};
            })""")
        screenshot_path = OUTPUT_DIR / "som_plain.png"
        page.screenshot(path=str(screenshot_path))
        browser.close()

    from PIL import Image

    return Image.open(screenshot_path).convert("RGB"), elements


def annotate_screenshot(image, elements: list[dict]):
    """Draw numbered Set-of-Mark markers and return (image, mapping).

    Shared by both renderers: a filled red circle with the number at
    the element's top-left corner (so it never covers the element's
    own label) and a red outline around the element. The mapping
    records each number's role, name, id, centre and box.
    """
    from PIL import ImageDraw

    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    mapping: dict[str, dict] = {}
    for number, element in enumerate(elements, start=1):
        cx = element["x"] + element["width"] / 2
        cy = element["y"] + element["height"] / 2
        mapping[str(number)] = {
            "role": element["tag"],
            "name": element["name"],
            "id": element["id"],
            "centre": [round(cx, 1), round(cy, 1)],
            "box": [round(element["x"], 1), round(element["y"], 1),
                    round(element["width"], 1), round(element["height"], 1)],
        }
        mx, my = element["x"] + 2, element["y"] + 2
        draw.ellipse([mx, my, mx + 22, my + 22], fill=(220, 38, 38))
        draw.text((mx + 7, my + 5), str(number), fill=(255, 255, 255))
        draw.rectangle([element["x"], element["y"],
                        element["x"] + element["width"],
                        element["y"] + element["height"]],
                       outline=(220, 38, 38), width=2)
    return annotated, mapping


# --------------------------------------------------------------------------
# The mock choosers (deterministic fallback when no model is configured)
# --------------------------------------------------------------------------

def mock_labelled_choice(target_keyword: str, mapping: dict) -> int | None:
    """Select a mark number whose element name contains the keyword.

    This is what a vision model does well: discrete selection among visible,
    labelled options. The mock is generous — a real model misreads labels —
    so treat its accuracy as an upper bound for the labelled path.
    It is kept only as the no-backend fallback: when a model backend
    is configured, ``choose_with_real_model`` runs instead.
    """
    for number, element in mapping.items():
        if target_keyword.lower() in element["name"].lower():
            return int(number)
    return None


def mock_raw_coordinate_guess(target_centre: tuple[float, float],
                              rng: random.Random) -> tuple[float, float]:
    """Guess an (x, y) pair for a target from an unmarked screenshot.

    A vision model estimating coordinates from a downscaled image makes
    errors that grow as the target shrinks, so the mock error is drawn from a
    distribution whose spread scales with 1 / target size — here simulated
    with a fixed spread, which is deliberately pessimistic for large targets
    and optimistic for the tiny one. Kept only as the no-backend
    fallback: when a model backend is configured,
    ``guess_raw_coordinates_with_real_model`` runs instead.
    """
    error_x = rng.gauss(0, 28)
    error_y = rng.gauss(0, 22)
    return target_centre[0] + error_x, target_centre[1] + error_y


# --------------------------------------------------------------------------
# Real-model calls (the default path when a backend is configured)
# --------------------------------------------------------------------------

def _parse_mark_number(text: str) -> int | None:
    """Return the first integer in the model's reply, or None."""
    match = re.search(r"\d+", text or "")
    return int(match.group(0)) if match else None


def _coerce_coordinates(value: object) -> tuple[float, float] | None:
    """Validate a model reply of {"x": ..., "y": ...} on the 0-1000 grid."""
    if not isinstance(value, dict):
        return None
    try:
        x = float(value["x"])
        y = float(value["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= x <= 1000.0 and 0.0 <= y <= 1000.0):
        return None
    return x, y


def choose_with_real_model(annotated_image_path: Path, target: str,
                           client) -> int | None:
    """Ask a real vision model to pick a mark number from the annotated image.

    The prompt is the one the original TODO carried: the model sees the
    numbered markers and answers with a single integer. The
    configured judge model (``client.judge_model``) is used because
    selection among visible labels is a short classification, not a
    reasoning task.
    Returns None when the reply carries no integer.
    """
    prompt = (
        "The image shows a user interface with numbered markers on each "
        f"interactive element. Which marker is on {target}? Answer with a "
        "single integer and nothing else."
    )
    reply = client.complete_with_image(
        annotated_image_path, prompt, model=client.judge_model,
        temperature=0.0,
    )
    return _parse_mark_number(reply)


def guess_raw_coordinates_with_real_model(plain_image_path: Path, target: str,
                                          client, mapper: CoordinateMapper,
                                          ) -> tuple[float, float] | None:
    """Ask a real vision model to guess an (x, y) pair from the plain image.

    The model answers in the normalised 0-1000 grid and the mapper
    translates the guess into screen pixels, exactly as a
    screenshot-only agent would. Returns None when the reply is not
    a usable coordinate pair.
    """
    prompt = (
        "The image shows a user interface. "
        f"Where is {target}? Answer with a JSON object "
        '{"x": <number>, "y": <number>} where x and y are coordinates '
        "on a 0-1000 grid: (0, 0) is the top-left corner of the image "
        "and (1000, 1000) is the bottom-right corner. Answer with the "
        "JSON object and nothing else."
    )
    reply = client.structured(
        [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {
                "url": _image_data_uri(plain_image_path)}},
        ]}],
        model=client.judge_model, temperature=0.0, max_tokens=256,
        required_fields=("x", "y"),
    )
    coordinates = _coerce_coordinates(reply)
    if coordinates is None:
        return None
    return mapper.model_to_screen(*coordinates)


def _image_data_uri(image_path: Path) -> str:
    """Read an image file as a base64 data URI (PNG fallback)."""
    import base64
    import mimetypes

    mime, _ = mimetypes.guess_type(str(image_path))
    if mime is None or not mime.startswith("image/"):
        mime = "image/png"
    encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


# --------------------------------------------------------------------------
# Main flow
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7,
                        help="random seed for the raw-coordinate mock")
    parser.add_argument("--mock", action="store_true",
                        help="force the deterministic mock choosers even "
                             "when a model backend is configured")
    parser.add_argument("--real-model", action="store_true",
                        help="require the real model; fail instead of "
                             "falling back to the mocks")
    args = parser.parse_args()

    try:
        from PIL import Image
    except ImportError as exc:
        print(f"Missing dependency ({exc}). Run:\n"
              "  pip install -r requirements.txt", file=sys.stderr)
        return 2

    OUTPUT_DIR.mkdir(exist_ok=True)

    # 1. Render the test page: a real browser when one is available,
    #    the Pillow renderer otherwise — never a faked browser run.
    try:
        image, elements = capture_test_page_playwright()
        print("Rendered with Playwright (Chromium).")
    except Exception as exc:  # ImportError, or Chromium cannot launch
        print(f"Playwright unavailable ({exc}); rendering the test page "
              "with Pillow instead. The image and the mapping are real; "
              "only the renderer differs.")
        image, elements = render_test_page_pillow()
    screenshot_path = OUTPUT_DIR / "som_plain.png"
    image.save(screenshot_path)

    # 2. Draw numbered markers on the screenshot (the Set-of-Mark overlay).
    annotated, mapping = annotate_screenshot(image, elements)
    annotated_path = OUTPUT_DIR / "som_annotated.png"
    annotated.save(annotated_path)
    with open(OUTPUT_DIR / "som_mapping.json", "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, indent=2)
    print(f"Annotated screenshot: {annotated_path}")
    print(f"Mapping: {OUTPUT_DIR / 'som_mapping.json'} "
          f"({len(mapping)} elements labelled)")

    # 3. Coordinate mapping: verify the transform is exact on this page.
    # The screenshot is the same size as the viewport here, so the mapper is
    # the identity transform — the point is to measure its rounding error.
    mapper = CoordinateMapper(screenshot_width=image.width,
                              screenshot_height=image.height,
                              screen_width=image.width,
                              screen_height=image.height)
    print("\nCoordinate-mapping error (intended centre vs mapped centre):")
    max_error = 0.0
    for number, element in mapping.items():
        cx, cy = element["centre"]
        mx, my = mapper.screen_to_model(cx, cy)
        back_x, back_y = mapper.model_to_screen(mx, my)
        error = math.hypot(back_x - cx, back_y - cy)
        max_error = max(max_error, error)
        print(f"  mark {number} ({element['name'] or element['id']}): "
              f"{error:.2f} px")
    print(f"  largest mapping error: {max_error:.2f} px "
          f"(acceptance: under 5 px)")

    # 4. Compare the two grounding paths on the same targets.
    #    Real model by default; deterministic mocks only when no
    #    backend is configured (or --mock forces them).
    client = None
    use_real = False
    if not args.mock:
        from llm_client import LLMClient

        if args.real_model and not LLMClient.has_backend():
            print("No model backend configured. Connect a model "
                  "(see \"Connect a model\" in the root README) and "
                  "re-run.", file=sys.stderr)
            return 2
        if args.real_model or LLMClient.has_backend():
            client = LLMClient()
            use_real = True
    if use_real:
        print("\nGrounding comparison (real vision model, "
              f"model {client.judge_model}):")
    else:
        print("\nGrounding comparison (mock chooser, seed "
              f"{args.seed} — no model backend configured; connect a "
              "model for the real-model comparison):")
    print(f"  {'target':38s} {'labelled':>9s} {'raw-coord':>10s}")
    rng = random.Random(args.seed)
    labelled_hits = raw_hits = 0
    for description, keyword, element_id in TARGETS:
        element = next(e for e in mapping.values() if e["id"] == element_id)
        cx, cy = element["centre"]
        width, height = element["box"][2], element["box"][3]

        if use_real:
            picked = choose_with_real_model(annotated_path, description, client)
        else:
            picked = mock_labelled_choice(keyword, mapping)
        labelled_ok = (picked is not None
                       and mapping.get(str(picked), {}).get("id") == element_id)
        labelled_hits += labelled_ok

        if use_real:
            guess = guess_raw_coordinates_with_real_model(
                screenshot_path, description, client, mapper)
            if guess is None:
                print(f"  WARNING: real model returned no usable "
                      f"coordinates for {description!r}")
                guess_x, guess_y = float("nan"), float("nan")
            else:
                guess_x, guess_y = guess
        else:
            guess_x, guess_y = mock_raw_coordinate_guess((cx, cy), rng)
        raw_ok = (abs(guess_x - cx) <= width / 2
                  and abs(guess_y - cy) <= height / 2)
        raw_hits += raw_ok
        print(f"  {description:38s} {'HIT' if labelled_ok else 'miss':>9s} "
              f"{'HIT' if raw_ok else 'miss':>10s}   "
              f"(target {width:.0f}x{height:.0f} px, "
              f"raw guess off by {math.hypot(guess_x - cx, guess_y - cy):.0f} px)")
    print(f"\n  Labelled (Set-of-Mark) path: {labelled_hits}/{len(TARGETS)}")
    print(f"  Raw-coordinate path:        {raw_hits}/{len(TARGETS)}")
    if use_real:
        print(f"\n  Model usage: {client.total_prompt_tokens:,} prompt + "
              f"{client.total_completion_tokens:,} completion tokens"
              + (f", ${client.total_cost_usd:.4f}"
                 if client.total_cost_usd else " (no cost reported)"))
    print("\nWhy the labelled path wins: it turns grounding from continuous "
          "coordinate regression — which vision-language models do badly — "
          "into discrete selection among visible options, which they do "
          "well. It still fails on overlapping elements, on elements smaller "
          "than their marker, and on content that moves between labelling "
          "and clicking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
