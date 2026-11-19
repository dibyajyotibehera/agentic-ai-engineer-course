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
path (a mock chooser guessing an (x, y) pair from the unmarked screenshot),
and accuracy is reported for both.

Outputs (in code/output/):
  som_annotated.png — the screenshot with a numbered marker on each element.
  som_mapping.json  — number -> {role, name, centre coordinates}.

The mock chooser stands in for a vision model so the exercise runs with no
API key. To use a real model, implement the TODO in choose_with_real_model():
the prompt is provided there; the model must answer with a single mark number.

Run:
    pip install -r requirements.txt
    playwright install chromium
    python 02_set_of_mark.py
"""

from __future__ import annotations

import argparse
import json
import math
import random
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
# The mock choosers (stand-ins for a vision model)
# --------------------------------------------------------------------------

def mock_labelled_choice(target_keyword: str, mapping: dict) -> int | None:
    """Select a mark number whose element name contains the keyword.

    This is what a vision model does well: discrete selection among visible,
    labelled options. The mock is generous — a real model misreads labels —
    so treat its accuracy as an upper bound for the labelled path.
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
    and optimistic for the tiny one.
    """
    error_x = rng.gauss(0, 28)
    error_y = rng.gauss(0, 22)
    return target_centre[0] + error_x, target_centre[1] + error_y


# --------------------------------------------------------------------------
# TODO: real-model call (needs an API key)
# --------------------------------------------------------------------------

def choose_with_real_model(annotated_image_path: Path, target: str) -> int | None:
    """Ask a hosted vision model to pick a mark number. TODO — needs a key.

    Prompt to send with the annotated image attached:

        "The image shows a user interface with numbered markers on each
        interactive element. Which marker is on {target}? Answer with a
        single integer and nothing else."

    Implement this with your provider's vision API (for example Anthropic's
    Messages API with an image block, or OpenAI's chat completions with an
    image URL), parse the integer from the reply, and return it. Return None
    if the reply is not a single integer. Keep the mock results alongside
    the real ones so the comparison stays honest.
    """
    raise NotImplementedError(
        "TODO: wire in a hosted vision model call here (see docstring). "
        "The exercise is complete with the mock chooser; this is the "
        "stretch that measures a real model.")


# --------------------------------------------------------------------------
# Main flow
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7,
                        help="random seed for the raw-coordinate mock")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
        from PIL import Image, ImageDraw
    except ImportError as exc:
        print(f"Missing dependency ({exc}). Run:\n"
              "  pip install -r requirements.txt\n"
              "  playwright install chromium", file=sys.stderr)
        return 2

    OUTPUT_DIR.mkdir(exist_ok=True)
    rng = random.Random(args.seed)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 600})
        page.set_content(TEST_HTML)
        page.wait_for_timeout(300)

        # 1. Label: collect interactive elements and their bounding boxes.
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

    # 2. Draw numbered markers on the screenshot (the Set-of-Mark overlay).
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
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
        # Marker: a filled circle with the number, at the element's top-left
        # corner so it never covers the element's own label.
        mx, my = element["x"] + 2, element["y"] + 2
        draw.ellipse([mx, my, mx + 22, my + 22], fill=(220, 38, 38))
        draw.text((mx + 7, my + 5), str(number), fill=(255, 255, 255))
        draw.rectangle([element["x"], element["y"],
                        element["x"] + element["width"],
                        element["y"] + element["height"]],
                       outline=(220, 38, 38), width=2)
    annotated_path = OUTPUT_DIR / "som_annotated.png"
    image.save(annotated_path)
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
    print("\nGrounding comparison (mock chooser, seed "
          f"{args.seed}):")
    print(f"  {'target':38s} {'labelled':>9s} {'raw-coord':>10s}")
    labelled_hits = raw_hits = 0
    for description, keyword, element_id in TARGETS:
        element = next(e for e in mapping.values() if e["id"] == element_id)
        cx, cy = element["centre"]
        width, height = element["box"][2], element["box"][3]

        picked = mock_labelled_choice(keyword, mapping)
        labelled_ok = (picked is not None
                       and mapping[str(picked)]["id"] == element_id)
        labelled_hits += labelled_ok

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
    print("\nWhy the labelled path wins: it turns grounding from continuous "
          "coordinate regression — which vision-language models do badly — "
          "into discrete selection among visible options, which they do "
          "well. It still fails on overlapping elements, on elements smaller "
          "than their marker, and on content that moves between labelling "
          "and clicking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
