"""Exercise 3 — Screenshot diffing: detect what changed (Intermediate).

The verification layer: compare two screenshots, report exactly which
regions changed, and tell a real change from rendering noise.

Two synthetic screenshots are generated with Pillow — a form page before and
after a field is filled — plus deliberate noise: a blinking cursor and an
animated banner. The differ computes the pixel difference, thresholds it,
extracts the bounding boxes of the changed regions, and writes a diff image
with the changed regions outlined.

The threshold is the whole exercise: too low and the cursor blink is reported
as a change; too high and a subtle edit (one character in a text field) is
missed. Tune --threshold until the filled field is reported and the cursor
is not.

Run:
    pip install -r requirements.txt
    python 03_screenshot_diff.py [--threshold 30]

Outputs (in code/output/):
  diff_before.png, diff_after.png — the two synthetic screenshots.
  diff.png — the after screenshot with changed regions outlined in red.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"

WIDTH, HEIGHT = 800, 600


def make_screenshot(filled: bool, cursor_on: bool) -> Image.Image:
    """Draw a synthetic form page. `filled` fills the name field; the cursor
    blinks and the banner animates, so both are noise the differ must ignore
    (the banner) or the threshold must suppress (the cursor)."""
    image = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, WIDTH, 50], fill=(30, 64, 175))
    draw.text((20, 15), "Request access", fill=(255, 255, 255))
    # Animated banner: two frames of a spinner, drawn in slightly different
    # shades so the pixel difference is small but non-zero.
    shade = (200, 200, 200) if cursor_on else (208, 208, 208)
    draw.ellipse([WIDTH - 60, 12, WIDTH - 28, 44], fill=shade)
    draw.text((20, 80), "Full name", fill=(0, 0, 0))
    draw.rectangle([20, 105, 420, 140], outline=(120, 120, 120), width=2)
    if filled:
        draw.text((28, 113), "Alex Example", fill=(0, 0, 0))
    if cursor_on:
        # Blinking text cursor: a thin black bar at the end of the text.
        draw.rectangle([150, 110, 152, 135], fill=(0, 0, 0))
    draw.text((20, 170), "Work email", fill=(0, 0, 0))
    draw.rectangle([20, 195, 420, 230], outline=(120, 120, 120), width=2)
    draw.rectangle([20, 280, 180, 320], outline=(30, 64, 175), width=2)
    draw.text((55, 290), "Submit request", fill=(30, 64, 175))
    return image


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Binary dilation: merge nearby changed pixels into one region.

    Letterforms in a filled text field are disconnected blobs a few pixels
    apart; without dilation the differ reports each letter as its own
    region. Dilation merges them into the one region that actually changed
    (the field), while the cursor blink stays a separate small region.
    """
    out = mask.copy()
    for _ in range(radius):
        out = (out
               | np.roll(out, 1, axis=0) | np.roll(out, -1, axis=0)
               | np.roll(out, 1, axis=1) | np.roll(out, -1, axis=1))
    return out


def label_regions(mask: np.ndarray, raw_changed: np.ndarray,
                    min_pixels: int) -> list[tuple[int, int, int, int]]:
    """Bounding boxes of connected changed regions, via flood fill on a
    4x-downsampled mask (fast enough in pure Python at this size).

    `mask` is the dilated mask, used for the boxes; `raw_changed` is the
    undilated pixel mask, used to count how many pixels really changed
    inside each box. Regions with fewer than `min_pixels` genuinely changed
    pixels are dropped: a blinking text cursor is a real pixel change, but
    it is a speck, not a state change, and the differ's job is to report
    state changes. The threshold handles low-contrast noise (the animated
    banner); the minimum pixel count handles small high-contrast noise
    (the cursor). Neither knob alone is enough — that is the tuning lesson
    of this exercise.
    """
    small = mask[::4, ::4]
    height, width = small.shape
    seen = np.zeros_like(small, dtype=bool)
    boxes: list[tuple[int, int, int, int]] = []
    for y in range(height):
        for x in range(width):
            if not small[y, x] or seen[y, x]:
                continue
            # Flood fill one region.
            stack = [(x, y)]
            seen[y, x] = True
            min_x = max_x = x
            min_y = max_y = y
            while stack:
                cx, cy = stack.pop()
                min_x, max_x = min(min_x, cx), max(max_x, cx)
                min_y, max_y = min(min_y, cy), max(max_y, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy),
                               (cx, cy + 1), (cx, cy - 1)):
                    if (0 <= nx < width and 0 <= ny < height
                            and small[ny, nx] and not seen[ny, nx]):
                        seen[ny, nx] = True
                        stack.append((nx, ny))
            # Scale back up and drop regions with too few really-changed
            # pixels inside them.
            box = (min_x * 4, min_y * 4, (max_x + 1) * 4, (max_y + 1) * 4)
            changed_inside = int(raw_changed[box[1]:box[3],
                                             box[0]:box[2]].sum())
            if changed_inside >= min_pixels:
                boxes.append(box)
    return boxes


def diff_screenshots(before: Image.Image, after: Image.Image,
                     threshold: int, min_pixels: int = 100,
                     dilate_radius: int = 6) -> tuple[list[tuple[int, int, int, int]], float]:
    """Return (changed-region bounding boxes, percentage of pixels changed).

    A pixel counts as changed when any colour channel differs by more than
    `threshold` (0-255). Changed pixels are then dilated so letterforms
    merge into one region, and regions with fewer than `min_pixels`
    genuinely changed pixels are dropped as specks.
    """
    a = np.asarray(before).astype(int)
    b = np.asarray(after).astype(int)
    difference = np.abs(a - b).max(axis=2)
    changed = difference > threshold
    total = changed.size
    changed_pct = 100.0 * int(changed.sum()) / total
    return (label_regions(dilate(changed, dilate_radius), changed, min_pixels),
            changed_pct)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=int, default=30,
                        help="per-channel pixel difference that counts as a "
                             "change (0-255; default 30)")
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(exist_ok=True)

    before = make_screenshot(filled=False, cursor_on=False)
    after = make_screenshot(filled=True, cursor_on=True)
    before.save(OUTPUT_DIR / "diff_before.png")
    after.save(OUTPUT_DIR / "diff_after.png")

    boxes, changed_pct = diff_screenshots(before, after, args.threshold)
    print(f"Threshold: {args.threshold}")
    print(f"Total changed pixels: {changed_pct:.2f}% of the screen")
    if changed_pct > 40:
        # A full-page change is a different event from an in-page edit.
        print("Verdict: page navigated or reloaded "
              "(over 40% of the screen changed).")
    else:
        print(f"Changed regions ({len(boxes)}):")
        for (x0, y0, x1, y1) in boxes:
            area_pct = 100.0 * (x1 - x0) * (y1 - y0) / (WIDTH * HEIGHT)
            print(f"  box ({x0}, {y0})-({x1}, {y1}): {area_pct:.2f}% of screen")

    annotated = after.copy()
    draw = ImageDraw.Draw(annotated)
    for (x0, y0, x1, y1) in boxes:
        draw.rectangle([x0, y0, x1, y1], outline=(220, 38, 38), width=3)
    annotated.save(OUTPUT_DIR / "diff.png")
    print(f"Diff image: {OUTPUT_DIR / 'diff.png'}")

    field_box = [box for box in boxes
                 if box[2] - box[0] > 50 and 90 < box[1] < 160]
    cursor_box = [box for box in boxes
                  if box[2] - box[0] <= 20 and 100 < box[1] < 145]
    field_msg = ("yes" if field_box else
                 "NO — threshold too high or labelling missed it")
    cursor_msg = ("YES — threshold too low" if cursor_box else "no")
    print(f"Filled field reported: {field_msg}")
    print(f"Cursor blink reported: {cursor_msg}")
    print("\nThreshold trade-off: too low and noise (the blinking cursor, "
          "the animated banner) is reported as change, so the agent chases "
          "phantoms; too high and a subtle change — one character edited in "
          "a text field — is missed entirely. For a verification layer the "
          "second failure is worse: a missed change lets the agent proceed "
          "from a false belief that its action worked, while a false "
          "positive only costs one extra look.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
