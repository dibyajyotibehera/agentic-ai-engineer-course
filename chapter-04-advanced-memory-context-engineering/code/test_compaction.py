"""Tests for code/compaction.py (Chapter 4, Exercise 6).

Standard library only. Run from the chapter directory with:

    python3 code/test_compaction.py

or from inside code/ with:

    python3 test_compaction.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compaction import (  # noqa: E402
    CompactedState,
    TranscriptTurn,
    compact,
    detect_rot,
    estimate_tokens,
    lazy_load,
    prune_tool_result,
    should_compact,
)


def _long_transcript(n_turns: int = 14) -> list[TranscriptTurn]:
    """A long support case: a stale March instruction repeated early,
    then recent turns about the current refund question."""
    turns = [
        TranscriptTurn(
            "Refunds within 30 days need no approval.",
            role="system",
            is_instruction=True,
        )
        for _ in range(n_turns - 2)
    ]
    turns.append(
        TranscriptTurn(
            "Refund policy is 14 days with manager approval (June).",
            role="assistant",
            load_bearing=True,
        )
    )
    turns.append(
        TranscriptTurn("Customer asks about a refund on day 20.", role="user")
    )
    return turns


class TestCompactionTrigger(unittest.TestCase):
    def test_trigger_fires_at_the_70_percent_point(self):
        decision = should_compact(2800, 4000)
        self.assertTrue(decision.should_compact)
        self.assertAlmostEqual(decision.pressure, 0.70)

    def test_trigger_does_not_fire_below_the_point(self):
        decision = should_compact(2000, 4000)
        self.assertFalse(decision.should_compact)

    def test_zero_budget_is_an_error(self):
        with self.assertRaises(ValueError):
            should_compact(100, 0)


class TestCompactionPreservesLoadBearingFacts(unittest.TestCase):
    def test_compaction_preserves_load_bearing_facts(self):
        facts = [
            "Refund policy is 14 days with manager approval (June).",
            "Customer's plan is business, renewal 2027-03-01.",
            "Tried re-issuing the invoice; it did not resolve the error.",
        ]
        state = compact(_long_transcript(), facts)
        self.assertTrue(state.complete)
        self.assertEqual(state.load_bearing_missing, [])
        self.assertEqual(state.load_bearing_preserved, facts)
        for fact in facts:
            self.assertIn(fact, state.summary)

    def test_compaction_reduces_tokens(self):
        state = compact(_long_transcript(), ["Refund policy is 14 days."])
        self.assertLess(state.tokens_after, state.tokens_before)

    def test_compaction_keeps_load_bearing_turns_verbatim(self):
        transcript = _long_transcript()
        state = compact(transcript, ["Refund policy is 14 days."])
        self.assertIn(
            "Refund policy is 14 days with manager approval (June).",
            state.summary,
        )
        self.assertGreater(state.turns_kept_verbatim, 0)

    def test_missing_fact_is_reported_not_hidden(self):
        # A compaction that drops a named fact must say so: build the
        # state, then check the completeness flag catches a fact that
        # was never in the summary. (compact() always appends the facts,
        # so simulate the bad-compaction case through the dataclass.)
        state = CompactedState(
            summary="STATE SUMMARY:\n- nothing relevant",
            tokens_before=100,
            tokens_after=10,
            turns_kept_verbatim=0,
            turns_summarised=5,
            turns_dropped=5,
            load_bearing_preserved=[],
            load_bearing_missing=["Refund policy is 14 days."],
        )
        self.assertFalse(state.complete)


class TestToolResultPruning(unittest.TestCase):
    def test_pruning_reduces_tokens(self):
        big_output = "invoice line: INV-1001 $480, " * 200
        for strategy in ("summarise", "pointer"):
            kwargs = {"pointer_id": "record-1"} if strategy == "pointer" else {}
            pruned = prune_tool_result(big_output, strategy=strategy, **kwargs)
            self.assertLess(pruned.tokens_after, pruned.tokens_before)
            self.assertGreater(pruned.tokens_saved, 0)

    def test_fields_strategy_keeps_only_requested_fields(self):
        record = "plan: business\nrenewal date: 2027-03-01\nseats: 42\nnotes: lots of detail"
        pruned = prune_tool_result(
            record, strategy="fields", keep_fields=["plan", "renewal date"]
        )
        self.assertIn("plan: business", pruned.text)
        self.assertIn("renewal date: 2027-03-01", pruned.text)
        self.assertNotIn("seats: 42", pruned.text)
        self.assertLess(pruned.tokens_after, pruned.tokens_before)

    def test_pointer_strategy_keeps_full_text_out_of_context(self):
        record = "full billing record: " + "detail, " * 100
        pruned = prune_tool_result(record, strategy="pointer", pointer_id="rec-7")
        self.assertEqual(pruned.stored_full_text, record)
        self.assertIn("rec-7", pruned.text)
        self.assertLess(pruned.tokens_after, pruned.tokens_before)

    def test_expiry_drops_a_single_use_result(self):
        pruned = prune_tool_result("candidate tickets: A, B, C", strategy="expiry", expired=True)
        self.assertEqual(pruned.tokens_after, 0)

    def test_expiry_keeps_a_result_still_needed(self):
        text = "candidate tickets: A, B, C"
        pruned = prune_tool_result(text, strategy="expiry", expired=False)
        self.assertEqual(pruned.tokens_after, estimate_tokens(text))

    def test_unknown_strategy_is_an_error(self):
        with self.assertRaises(ValueError):
            prune_tool_result("x", strategy="shred")


class TestRotDetector(unittest.TestCase):
    def test_rot_detector_fires_on_a_stale_heavy_transcript(self):
        report = detect_rot(
            _long_transcript(),
            max_age_turns=10,
            stale_instructions=["Refunds within 30 days need no approval."],
        )
        self.assertTrue(report.rot_suspected)
        self.assertIn("Refunds within 30 days need no approval.", report.stale_instructions)
        self.assertTrue(report.reasons)

    def test_rot_detector_fires_on_dilution_alone(self):
        # Twenty old turns and nothing stale: volume alone is enough.
        turns = [
            TranscriptTurn(f"Routine status update number {i}.", role="assistant")
            for i in range(30)
        ] + [TranscriptTurn("Current question.", role="user")]
        report = detect_rot(turns, max_age_turns=10)
        self.assertTrue(report.rot_suspected)
        self.assertGreater(report.old_token_fraction, 0.5)

    def test_rot_detector_stays_quiet_on_a_fresh_transcript(self):
        turns = [
            TranscriptTurn("Current question about a refund.", role="user"),
            TranscriptTurn("Refund policy is 14 days with manager approval.", role="assistant"),
        ]
        report = detect_rot(turns, max_age_turns=10)
        self.assertFalse(report.rot_suspected)
        self.assertEqual(report.reasons, [])


class TestLazyLoading(unittest.TestCase):
    def test_lazy_loading_defers_what_is_not_needed_now(self):
        loaded, deferred = lazy_load(
            ["current goal: resolve the refund question"],
            {"past episodes": "12 similar tickets"},
        )
        self.assertEqual(loaded, ["current goal: resolve the refund question"])
        self.assertEqual(deferred, ["pointer:past episodes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
