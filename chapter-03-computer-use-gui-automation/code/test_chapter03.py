"""Tests for Chapter 3 (Computer Use and GUI Automation).

Run with ``python -m unittest test_chapter03 -v`` from this directory.
The deterministic tests need no model, no browser and no network:
the coordinate mapper, the Pillow page renderer, the mock choosers,
the screenshot differ, the cost arithmetic and the seam policy are
all pure local computation, and the chapter scripts are exercised
end to end in their mock / synthetic modes.

The live tests at the end need a configured model backend
(``LLM_API_KEY``, ``LLM_BASE_URL`` and ``LLM_MODEL`` set, or
``LLM_BACKEND=bedrock`` with ``LLM_BEDROCK_MODEL_ID``). When no
backend is configured they skip instead of failing: a skipped live
test means "not run", not "passed". The live tests call the real
vision model against real image files rendered here with Pillow —
the same images ``02_set_of_mark.py`` produces when Playwright is
unavailable — so a passing live test means the model genuinely
read the screenshot, not that a mock agreed with itself.
"""

from __future__ import annotations

import importlib.util
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _seam import (  # noqa: E402
    DelegationPolicy,
    HandoffRecord,
    MarkedElement,
    choose_mark,
    verify_dismissed,
)
from llm_client import LLMClient  # noqa: E402


def _load_numbered_module(filename: str, name: str):
    """Import a module whose filename starts with a digit."""
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


som = _load_numbered_module("02_set_of_mark.py", "som")
differ = _load_numbered_module("03_screenshot_diff.py", "differ")
costs = _load_numbered_module("04_cost_arithmetic.py", "costs")
seam = _load_numbered_module("05_hybrid_seam.py", "seam")


def _llm_available() -> bool:
    return LLMClient.has_backend()


def _live_client() -> LLMClient:
    return LLMClient()


def _render_labelled_page(tmpdir: Path):
    """Render the test page with Pillow and annotate it; return paths."""
    image, elements = som.render_test_page_pillow()
    plain_path = tmpdir / "som_plain.png"
    image.save(plain_path)
    annotated, mapping = som.annotate_screenshot(image, elements)
    annotated_path = tmpdir / "som_annotated.png"
    annotated.save(annotated_path)
    return plain_path, annotated_path, mapping


def _render_popup(tmpdir: Path):
    """Draw a pop-up dialog with two marked buttons; return (path, marks)."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (900, 600), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((40, 30), "Request access", fill=(0, 0, 0))
    draw.rectangle([200, 150, 700, 350], outline=(51, 51, 51), width=2,
                   fill=(255, 255, 255))
    draw.text((230, 180), "Before you continue: please confirm you have",
              fill=(0, 0, 0))
    draw.text((230, 200), "read the access policy.", fill=(0, 0, 0))
    draw.rectangle([250, 280, 370, 320], outline=(51, 51, 51), width=2)
    draw.text((285, 292), "Dismiss", fill=(0, 0, 0))
    draw.rectangle([450, 280, 630, 320], outline=(51, 51, 51), width=2)
    draw.text((490, 292), "Remind me later", fill=(0, 0, 0))
    path = tmpdir / "popup.png"
    image.save(path)
    marks = [
        MarkedElement(1, "button", "Dismiss", (310, 300)),
        MarkedElement(2, "button", "Remind me later", (540, 300)),
    ]
    return path, marks


class TestCoordinateMapper(unittest.TestCase):
    def test_identity_round_trip_is_exact(self) -> None:
        mapper = som.CoordinateMapper(900, 600, 900, 600)
        for x, y in ((115, 122), (450, 300), (880, 590)):
            mx, my = mapper.screen_to_model(x, y)
            back_x, back_y = mapper.model_to_screen(mx, my)
            self.assertAlmostEqual(back_x, x, places=6)
            self.assertAlmostEqual(back_y, y, places=6)

    def test_downscaled_screenshot_maps_back(self) -> None:
        # The model saw a 1280x720 screenshot of a 1920x1080 screen.
        mapper = som.CoordinateMapper(1280, 720, 1920, 1080)
        x, y = mapper.model_to_screen(500, 500)
        self.assertAlmostEqual(x, 960.0)
        self.assertAlmostEqual(y, 540.0)


class TestPillowRenderer(unittest.TestCase):
    def test_renders_the_same_five_targets(self) -> None:
        _image, elements = som.render_test_page_pillow()
        self.assertEqual([e["id"] for e in elements],
                         ["save", "publish", "search", "export", "help"])

    def test_annotation_mapping_matches_the_elements(self) -> None:
        image, elements = som.render_test_page_pillow()
        _annotated, mapping = som.annotate_screenshot(image, elements)
        self.assertEqual(len(mapping), 5)
        for number, element in mapping.items():
            self.assertEqual(element["id"], elements[int(number) - 1]["id"])
            cx, cy = element["centre"]
            x, y, w, h = element["box"]
            self.assertTrue(x <= cx <= x + w)
            self.assertTrue(y <= cy <= y + h)

    def test_mapping_error_is_zero_on_the_rendered_page(self) -> None:
        image, elements = som.render_test_page_pillow()
        _annotated, mapping = som.annotate_screenshot(image, elements)
        mapper = som.CoordinateMapper(image.width, image.height,
                                      image.width, image.height)
        for element in mapping.values():
            cx, cy = element["centre"]
            mx, my = mapper.screen_to_model(cx, cy)
            back_x, back_y = mapper.model_to_screen(mx, my)
            self.assertAlmostEqual(back_x, cx, places=6)
            self.assertAlmostEqual(back_y, cy, places=6)


class TestMockChoosers(unittest.TestCase):
    """The deterministic fallback: generous, and labelled as such."""

    def test_labelled_mock_picks_each_target(self) -> None:
        image, elements = som.render_test_page_pillow()
        _annotated, mapping = som.annotate_screenshot(image, elements)
        for _description, keyword, element_id in som.TARGETS:
            picked = som.mock_labelled_choice(keyword, mapping)
            self.assertIsNotNone(picked)
            self.assertEqual(mapping[str(picked)]["id"], element_id)

    def test_labelled_mock_returns_none_for_unknown_target(self) -> None:
        image, elements = som.render_test_page_pillow()
        _annotated, mapping = som.annotate_screenshot(image, elements)
        self.assertIsNone(som.mock_labelled_choice("delete everything", mapping))

    def test_raw_mock_is_deterministic_for_a_fixed_seed(self) -> None:
        first = som.mock_raw_coordinate_guess((100, 100), random.Random(7))
        second = som.mock_raw_coordinate_guess((100, 100), random.Random(7))
        self.assertEqual(first, second)


class TestReplyParsing(unittest.TestCase):
    def test_mark_number_parses_a_plain_integer(self) -> None:
        self.assertEqual(som._parse_mark_number("3"), 3)
        self.assertEqual(som._parse_mark_number("The marker is 3."), 3)

    def test_mark_number_is_none_without_a_digit(self) -> None:
        self.assertIsNone(som._parse_mark_number("I cannot tell."))
        self.assertIsNone(som._parse_mark_number(""))

    def test_coordinates_accept_a_valid_pair(self) -> None:
        self.assertEqual(som._coerce_coordinates({"x": 123, "y": 456}),
                         (123.0, 456.0))

    def test_coordinates_reject_out_of_range_and_missing_fields(self) -> None:
        self.assertIsNone(som._coerce_coordinates({"x": 1200, "y": 50}))
        self.assertIsNone(som._coerce_coordinates({"x": 50}))
        self.assertIsNone(som._coerce_coordinates("not a dict"))


class TestScreenshotDiff(unittest.TestCase):
    def test_filled_field_reported_and_cursor_suppressed(self) -> None:
        before = differ.make_screenshot(filled=False, cursor_on=False)
        after = differ.make_screenshot(filled=True, cursor_on=True)
        boxes, changed_pct = differ.diff_screenshots(before, after, 30)
        self.assertLess(changed_pct, 40.0)
        field_box = [b for b in boxes if b[2] - b[0] > 50 and 90 < b[1] < 160]
        cursor_box = [b for b in boxes if b[2] - b[0] <= 20 and 100 < b[1] < 145]
        self.assertTrue(field_box, f"field not reported in {boxes}")
        self.assertFalse(cursor_box, f"cursor reported in {boxes}")

    def test_identical_images_report_no_change(self) -> None:
        image = differ.make_screenshot(filled=True, cursor_on=False)
        boxes, changed_pct = differ.diff_screenshots(image, image, 30)
        self.assertEqual(boxes, [])
        self.assertEqual(changed_pct, 0.0)


class TestCostArithmetic(unittest.TestCase):
    def test_anchor_reproduces_the_verified_figure(self) -> None:
        # 30 steps, 1,400 tokens per screenshot: 30*4,500 toolset
        # + 1,400*30*31/2 screenshots = 786,000, against the verified
        # anchor of about 783,000 tokens / $1.61.
        tokens = costs.screenshot_path_tokens(30, 1_400)
        self.assertEqual(tokens, 786_000)
        self.assertAlmostEqual(costs.cost_usd(tokens), 1.61, places=1)

    def test_dom_path_is_cheaper_and_scales_linearly(self) -> None:
        self.assertEqual(costs.dom_path_tokens(30, 300), 9_000)
        self.assertLess(costs.dom_path_tokens(30, 300),
                        costs.screenshot_path_tokens(30, 1_400))


class TestSeamPolicy(unittest.TestCase):
    def test_allow_list_rejects_unmarked_targets(self) -> None:
        marks = [MarkedElement(1, "button", "Dismiss", (100, 100)),
                 MarkedElement(2, "button", "Remind me later", (200, 100))]
        self.assertEqual(choose_mark("dismiss", marks), 1)
        self.assertIsNone(choose_mark("delete everything", marks))

    def test_failure_policy_retries_once_then_stops(self) -> None:
        policy = DelegationPolicy(max_steps=10, max_retries=1)
        record = HandoffRecord(step="dismiss pop-up")
        record.attempt(verified=False, evidence="screenshot-1.png")
        self.assertTrue(policy.should_retry(record))
        record.attempt(verified=False, evidence="screenshot-2.png")
        self.assertFalse(policy.should_retry(record))
        self.assertTrue(policy.outcome(record).startswith("STOP"))
        self.assertIn("screenshot-1.png", policy.outcome(record))
        self.assertIn("screenshot-2.png", policy.outcome(record))

    def test_verification_verdict(self) -> None:
        self.assertFalse(verify_dismissed(dialog_present=True))
        self.assertTrue(verify_dismissed(dialog_present=False))

    def test_annotate_marks_writes_a_marked_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            popup_path, marks = _render_popup(tmpdir)
            out = tmpdir / "marked.png"
            result = seam.annotate_marks(popup_path, marks, out)
            self.assertEqual(result, out)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 0)


class TestScriptsRun(unittest.TestCase):
    """End-to-end runs in the modes that need no browser and no model."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, *args], cwd=str(HERE),
            capture_output=True, text=True, timeout=180,
        )

    def test_set_of_mark_mock_run_exits_zero(self) -> None:
        result = self._run("02_set_of_mark.py", "--mock")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Labelled (Set-of-Mark) path: 5/5", result.stdout)
        self.assertIn("Raw-coordinate path:", result.stdout)

    def test_screenshot_diff_run_exits_zero(self) -> None:
        result = self._run("03_screenshot_diff.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Filled field reported: yes", result.stdout)
        self.assertIn("Cursor blink reported: no", result.stdout)

    def test_screenshot_diff_on_real_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            before_path = tmpdir / "before.png"
            after_path = tmpdir / "after.png"
            differ.make_screenshot(filled=False, cursor_on=False).save(before_path)
            differ.make_screenshot(filled=True, cursor_on=True).save(after_path)
            result = self._run(
                "03_screenshot_diff.py",
                "--before", str(before_path), "--after", str(after_path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Filled field reported: yes", result.stdout)
            self.assertIn("Cursor blink reported: no", result.stdout)

    def test_cost_arithmetic_run_exits_zero(self) -> None:
        result = self._run("04_cost_arithmetic.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("786,000 tokens", result.stdout)

    def test_hybrid_seam_self_test_exits_zero(self) -> None:
        result = self._run("05_hybrid_seam.py", "--self-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Self-test passed.", result.stdout)


@unittest.skipUnless(_llm_available(), "needs a configured LLM backend")
class TestLiveGrounding(unittest.TestCase):
    """Live tests: the real vision model against real image files."""

    def test_labelled_choice_picks_the_save_draft_button(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _plain, annotated, _mapping = _render_labelled_page(Path(tmp))
            picked = som.choose_with_real_model(
                annotated, "the Save draft button", _live_client())
            self.assertEqual(picked, 1)

    def test_labelled_choice_picks_the_tiny_help_button(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _plain, annotated, _mapping = _render_labelled_page(Path(tmp))
            picked = som.choose_with_real_model(
                annotated, "the tiny help button marked ?", _live_client())
            self.assertEqual(picked, 5)

    def test_raw_coordinate_guess_lands_on_the_save_draft_button(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain, _annotated, mapping = _render_labelled_page(Path(tmp))
            client = _live_client()
            image, _elements = som.render_test_page_pillow()
            mapper = som.CoordinateMapper(image.width, image.height,
                                          image.width, image.height)
            guess = som.guess_raw_coordinates_with_real_model(
                plain, "the Save draft button", client, mapper)
            self.assertIsNotNone(guess)
            assert guess is not None
            x, y = guess
            box = mapping["1"]["box"]
            # Generous bounds: the button box expanded by 25 px.
            self.assertGreaterEqual(x, box[0] - 25)
            self.assertLessEqual(x, box[0] + box[2] + 25)
            self.assertGreaterEqual(y, box[1] - 25)
            self.assertLessEqual(y, box[1] + box[3] + 25)


@unittest.skipUnless(_llm_available(), "needs a configured LLM backend")
class TestLiveSeamChoice(unittest.TestCase):
    """Live test: the real vision model picks the dismiss mark."""

    def test_model_picks_the_dismiss_button(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            popup_path, marks = _render_popup(tmpdir)
            picked = seam.choose_mark_with_real_model(
                popup_path, marks, _live_client())
            self.assertEqual(picked, 1)


if __name__ == "__main__":
    unittest.main()
