"""Tests for loop_watchdog.py — standard library only.

Run from the chapter directory or the code/ directory:

    python3 code/test_loop_watchdog.py
    python3 test_loop_watchdog.py
"""

import unittest

from loop_watchdog import (
    AttemptObservation,
    LoopWatchdog,
    WatchdogConfig,
    WatchdogVerdictKind,
    state_key,
)


class TestRepeatedSignatureStopsTheLoop(unittest.TestCase):
    def test_same_signature_twice_stops(self):
        watchdog = LoopWatchdog()
        signature = ("KeyError", "app.py", 6)
        first = watchdog.observe(AttemptObservation(
            attempt=1, output="first attempt text here",
            signature=signature))
        self.assertEqual(first.kind, WatchdogVerdictKind.CONTINUE)
        second = watchdog.observe(AttemptObservation(
            attempt=2, output="second attempt, different words entirely",
            signature=signature))
        self.assertEqual(second.kind, WatchdogVerdictKind.STOP)
        self.assertTrue(any("signature" in r.lower() for r in second.reasons))

    def test_same_output_state_twice_stops(self):
        watchdog = LoopWatchdog()
        watchdog.observe(AttemptObservation(
            attempt=1, output="Return the sorted list, then take the middle."))
        second = watchdog.observe(AttemptObservation(
            attempt=2, output="  return the sorted list, then take the middle. "))
        # Whitespace and capitalisation differ; the state hash is the same.
        self.assertEqual(second.kind, WatchdogVerdictKind.STOP)
        self.assertEqual(state_key("Return the sorted list, then take the middle."),
                         state_key("  return the sorted list, then take the middle. "))

    def test_different_signatures_do_not_stop_on_their_own(self):
        watchdog = LoopWatchdog()
        first = watchdog.observe(AttemptObservation(
            attempt=1, output="alpha attempt one",
            signature=("KeyError", "app.py", 6)))
        second = watchdog.observe(AttemptObservation(
            attempt=2, output="beta attempt two, wholly different",
            signature=("IndexError", "app.py", 9)))
        self.assertNotEqual(second.kind, WatchdogVerdictKind.STOP)
        self.assertEqual(first.kind, WatchdogVerdictKind.CONTINUE)


class TestOscillationIsDetected(unittest.TestCase):
    def test_alternating_states_stop_the_loop(self):
        watchdog = LoopWatchdog()
        a = "Handle empty lists by returning zero for the median."
        b = "Handle empty lists by raising ValueError, but sort in place."
        watchdog.observe(AttemptObservation(attempt=1, output=a, score=0.50))
        watchdog.observe(AttemptObservation(attempt=2, output=b, score=0.60))
        third = watchdog.observe(AttemptObservation(attempt=3, output=a, score=0.50))
        self.assertEqual(third.kind, WatchdogVerdictKind.STOP)
        self.assertTrue(any("oscillation" in r.lower() for r in third.reasons))

    def test_a_non_alternating_sequence_does_not_trigger_oscillation(self):
        watchdog = LoopWatchdog()
        verdicts = [
            watchdog.observe(AttemptObservation(
                attempt=i + 1, output=text, score=score))
            for i, (text, score) in enumerate([
                ("First draft: sort and take the middle.", 0.40),
                ("Second draft: average two middles for even lengths.", 0.65),
                ("Third draft: copy the list before sorting it.", 0.85),
            ])
        ]
        self.assertTrue(all(
            v.kind is WatchdogVerdictKind.CONTINUE for v in verdicts))
        self.assertFalse(any(
            "oscillation" in r.lower()
            for v in verdicts for r in v.reasons))


class TestConvergenceIsRecognised(unittest.TestCase):
    def test_rising_scores_with_new_states_continue(self):
        watchdog = LoopWatchdog()
        verdicts = [
            watchdog.observe(AttemptObservation(
                attempt=i + 1, output=text, score=score))
            for i, (text, score) in enumerate([
                ("Sort the list, then take the middle element.", 0.40),
                ("Sort the list; for even lengths average the two middle values.", 0.65),
                ("Sort a copy so the input is untouched; average the two middle "
                 "values for even lengths, take the middle for odd.", 0.85),
                ("Sort a copy; raise ValueError on an empty list; average the two "
                 "middle values for even lengths, take the middle for odd.", 0.95),
            ])
        ]
        self.assertTrue(all(
            v.kind is WatchdogVerdictKind.CONTINUE for v in verdicts))
        self.assertAlmostEqual(watchdog._best_score, 0.95)

    def test_flat_scores_stagnate_and_stop(self):
        watchdog = LoopWatchdog(config=WatchdogConfig(stagnation_window=3))
        verdicts = [
            watchdog.observe(AttemptObservation(
                attempt=i + 1, output=f"Attempt number {i} with fresh wording "
                f"about a different approach {i}", score=0.50))
            for i in range(4)
        ]
        self.assertEqual(verdicts[-1].kind, WatchdogVerdictKind.STOP)
        self.assertTrue(any("stagnat" in r.lower()
                            for r in verdicts[-1].reasons))

    def test_small_wobble_is_stagnation_not_convergence(self):
        # Scores that rise by less than the gain floor never set a new
        # best, so a 0.50 -> 0.51 -> 0.50 -> 0.51 wobble stagnates.
        watchdog = LoopWatchdog(config=WatchdogConfig(
            stagnation_window=3, min_score_gain=0.02))
        verdicts = [
            watchdog.observe(AttemptObservation(
                attempt=i + 1, output=f"Draft {i} reworded quite differently "
                f"each time, new phrasing {i}", score=score))
            for i, score in enumerate([0.50, 0.51, 0.50, 0.51])
        ]
        self.assertEqual(verdicts[-1].kind, WatchdogVerdictKind.STOP)


class TestPingPongAndVerdictReasons(unittest.TestCase):
    def test_ping_pong_delegation_stops(self):
        watchdog = LoopWatchdog()
        actors = ["planner", "researcher", "planner", "researcher", "planner"]
        texts = [
            "Researcher: find the refund policy for this order, please.",
            "Planner: I need the order's region before I can search.",
            "Researcher: please look up the refund policy for this order.",
            "Planner: I still need the order's region to search.",
            "Researcher: could you find the refund policy for this order?",
        ]
        verdicts = [
            watchdog.observe(AttemptObservation(
                attempt=i + 1, output=text, actor=actor))
            for i, (actor, text) in enumerate(zip(actors, texts))
        ]
        self.assertEqual(verdicts[-1].kind, WatchdogVerdictKind.STOP)
        self.assertTrue(any("ping-pong" in r.lower()
                            for r in verdicts[-1].reasons))

    def test_single_actor_loop_cannot_ping_pong(self):
        watchdog = LoopWatchdog()
        verdict = watchdog.observe(AttemptObservation(
            attempt=1, output="A single agent's first draft.", actor="maker"))
        self.assertEqual(verdict.kind, WatchdogVerdictKind.CONTINUE)

    def test_verdict_reasons_are_recorded(self):
        watchdog = LoopWatchdog()
        watchdog.observe(AttemptObservation(
            attempt=1, output="first attempt text here",
            signature=("KeyError", "app.py", 6)))
        second = watchdog.observe(AttemptObservation(
            attempt=2, output="second attempt, different words entirely",
            signature=("KeyError", "app.py", 6)))
        self.assertEqual(second.kind, WatchdogVerdictKind.STOP)
        self.assertTrue(second.reasons)
        # The watchdog keeps every verdict, so the report is auditable.
        self.assertEqual(len(watchdog.verdicts), 2)
        self.assertIn("signature", " ".join(second.reasons).lower())

    def test_semantic_repetition_warns_without_stopping(self):
        watchdog = LoopWatchdog()
        watchdog.observe(AttemptObservation(
            attempt=1, output="The refund is denied because the order is past thirty days."))
        second = watchdog.observe(AttemptObservation(
            attempt=2, output="The refund is denied because the order is past 30 days!"))
        self.assertEqual(second.kind, WatchdogVerdictKind.WARN)
        self.assertTrue(any("similar" in r.lower() for r in second.reasons))


if __name__ == "__main__":
    unittest.main(verbosity=2)
