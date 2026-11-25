"""Tests for critic_agent.py and self_healing_loop.py.

Run with ``python -m unittest test_critic_and_healing -v`` from this
directory.

The deterministic tests need no model and no network. They drive the
loops with small scripted makers and generators defined in this file:
those are *test doubles* for the loop logic — the shipped modules
contain no scripted stand-in, and their demos call a real model. Each
deterministic test names the behaviour it covers, so a failing test
reads as a loop regression, not as a generic assertion failure.

The live tests at the end need a configured model backend
(``LLM_API_KEY``, ``LLM_BASE_URL`` and ``LLM_MODEL``, or
``LLM_BACKEND=bedrock`` with ``LLM_BEDROCK_MODEL_ID``). When no
backend is configured they skip instead of failing: a skipped live
test means "not run", not "passed".
"""

from __future__ import annotations

import unittest

from critic_agent import (
    DRAFT_BUGGY,
    MEDIAN_SPEC,
    MEDIAN_TESTS,
    Critic,
    Finding,
    LLMCritic,
    LLMMaker,
    run_maker_checker,
)
from llm_client import LLMClient, LLMError
from self_healing_loop import (
    BUGGY_MEDIAN,
    AttemptRecord,
    LLMGenerator,
    RetryBudget,
    classify_failure,
    FailureKind,
    run_self_healing,
)


def _llm_available() -> bool:
    return LLMClient.has_backend()


def _live_client() -> LLMClient:
    return LLMClient()


# ---------------------------------------------------------------------------
# Test doubles: scripted makers/generators that live in the tests, not in
# the shipped modules. They exist so the loop logic — rounds, stopping
# rules, fail-closed reports — can be tested deterministically with no
# model and no key.
# ---------------------------------------------------------------------------

DRAFT_FIXED = '''
def median(xs):
    if not xs:
        raise ValueError("median of an empty list is undefined")
    ordered = sorted(xs)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
'''

FIXED_MEDIAN_PROGRAM = '''
def median(xs):
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    if n % 2 == 1:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2

print(median([3, 1, 2, 4]))
'''

STILL_BUGGY = '''
def lookup(users, user_id):
    return users[user_id]["name"]

print(lookup({"u1": {"name": "Asha"}}, "u2"))
'''


class ScriptedMaker:
    """Returns a fixed sequence of drafts, one per revision."""

    def __init__(self, drafts: list[str]):
        self.drafts = drafts
        self._next = 1

    def first_draft(self, specification: str) -> str:
        return self.drafts[0]

    def revise(self, specification: str, draft: str,
               findings: list[Finding]) -> str:
        revised = self.drafts[min(self._next, len(self.drafts) - 1)]
        self._next += 1
        return revised


class ScriptedGenerator:
    """Returns a fixed sequence of candidate programs, one per attempt."""

    def __init__(self, candidates: list[str]):
        self.candidates = candidates

    def generate(self, task: str, history: list[AttemptRecord]) -> str:
        index = min(len(history), len(self.candidates) - 1)
        return self.candidates[index]


def _median_verifier(output: str) -> tuple[bool, str]:
    if output.strip() == "2.5":
        return True, ""
    return False, f"expected '2.5', got {output.strip()!r}"


def _never_passes(_output: str) -> tuple[bool, str]:
    return False, "no output can satisfy this verifier"


class TestTestSuiteCritic(unittest.TestCase):
    def test_buggy_draft_fails_five_of_seven(self) -> None:
        critique = Critic(MEDIAN_SPEC, MEDIAN_TESTS).critique(DRAFT_BUGGY)
        self.assertEqual(critique.verdict, "FAIL")
        self.assertEqual(critique.tests_run, 7)
        self.assertEqual(critique.tests_passed, 2)
        self.assertEqual(len(critique.findings), 5)

    def test_fixed_draft_passes_all_seven(self) -> None:
        critique = Critic(MEDIAN_SPEC, MEDIAN_TESTS).critique(DRAFT_FIXED)
        self.assertEqual(critique.verdict, "PASS")
        self.assertEqual(critique.tests_passed, 7)
        self.assertEqual(critique.findings, [])

    def test_findings_name_the_failing_test_and_the_requirement(self) -> None:
        critique = Critic(MEDIAN_SPEC, MEDIAN_TESTS).critique(DRAFT_BUGGY)
        names = {finding.test_name for finding in critique.findings}
        self.assertIn("empty list raises ValueError", names)
        self.assertIn("even length", names)
        for finding in critique.findings:
            self.assertTrue(finding.what_happened)
            self.assertTrue(finding.what_is_required)


class TestMakerCheckerLoop(unittest.TestCase):
    def test_loop_converges_on_the_second_round(self) -> None:
        report = run_maker_checker(
            MEDIAN_SPEC, ScriptedMaker([DRAFT_BUGGY, DRAFT_FIXED]),
            Critic(MEDIAN_SPEC, MEDIAN_TESTS), max_rounds=3)
        self.assertTrue(report.succeeded)
        self.assertEqual(report.rounds_used, 2)
        self.assertEqual(len(report.critiques), 2)
        self.assertEqual(report.critiques[0].verdict, "FAIL")
        self.assertEqual(report.critiques[1].verdict, "PASS")

    def test_loop_that_never_passes_fails_closed_at_the_cap(self) -> None:
        report = run_maker_checker(
            MEDIAN_SPEC, ScriptedMaker([DRAFT_BUGGY]),
            Critic(MEDIAN_SPEC, MEDIAN_TESTS), max_rounds=3)
        self.assertFalse(report.succeeded)
        self.assertEqual(report.rounds_used, 3)
        self.assertEqual(len(report.critiques), 3)

    def test_first_draft_starts_from_an_existing_draft(self) -> None:
        report = run_maker_checker(
            MEDIAN_SPEC, ScriptedMaker([DRAFT_BUGGY, DRAFT_FIXED]),
            Critic(MEDIAN_SPEC, MEDIAN_TESTS), max_rounds=3,
            first_draft=DRAFT_BUGGY)
        self.assertTrue(report.succeeded)
        self.assertEqual(report.rounds_used, 2)

    def test_on_round_hook_sees_each_round(self) -> None:
        seen: list[tuple[int, str]] = []
        run_maker_checker(
            MEDIAN_SPEC, ScriptedMaker([DRAFT_BUGGY, DRAFT_FIXED]),
            Critic(MEDIAN_SPEC, MEDIAN_TESTS), max_rounds=3,
            on_round=lambda n, _d, c: seen.append((n, c.verdict)))
        self.assertEqual(seen, [(1, "FAIL"), (2, "PASS")])


class TestFailureClassification(unittest.TestCase):
    def test_syntax_is_syntax_missing_module_is_unrepairable(self) -> None:
        from traceback_parser import parse_traceback

        self.assertEqual(
            classify_failure(parse_traceback("SyntaxError: expected ':'")),
            FailureKind.SYNTAX)
        self.assertEqual(
            classify_failure(parse_traceback(
                "ModuleNotFoundError: No module named 'yaml'")),
            FailureKind.UNREPAIRABLE)
        self.assertEqual(
            classify_failure(parse_traceback("TimeoutError: timed out")),
            FailureKind.TRANSIENT)
        self.assertEqual(
            classify_failure(parse_traceback("KeyError: 'user_id'")),
            FailureKind.REPAIRABLE)


class TestSelfHealingLoop(unittest.TestCase):
    def test_loop_heals_in_two_attempts(self) -> None:
        report = run_self_healing(
            task="Write a median function and print median([3, 1, 2, 4]).",
            generator=ScriptedGenerator([BUGGY_MEDIAN, FIXED_MEDIAN_PROGRAM]),
            verifier=_median_verifier,
            budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
            sleep=lambda _s: None,
        )
        self.assertTrue(report.succeeded)
        self.assertEqual(len(report.attempts), 2)
        self.assertEqual(report.final_output.strip(), "2.5")

    def test_same_failure_twice_stops_early(self) -> None:
        report = run_self_healing(
            task="Look up a user that does not exist.",
            generator=ScriptedGenerator([STILL_BUGGY, STILL_BUGGY, STILL_BUGGY]),
            verifier=_never_passes,
            budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
            sleep=lambda _s: None,
        )
        self.assertFalse(report.succeeded)
        self.assertEqual(len(report.attempts), 2)
        self.assertIn("Same failure signature twice", report.stop_reason)

    def test_unrepairable_failure_stops_on_the_first_attempt(self) -> None:
        report = run_self_healing(
            task="Use a module that does not exist.",
            generator=ScriptedGenerator(
                ["import does_not_exist_xyz\nprint('never reached')"]),
            verifier=_never_passes,
            budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
            sleep=lambda _s: None,
        )
        self.assertFalse(report.succeeded)
        self.assertEqual(len(report.attempts), 1)
        self.assertIn("Unrepairable failure", report.stop_reason)

    def test_distinct_failures_burn_the_whole_budget(self) -> None:
        candidates = [
            "raise KeyError('a')",
            "raise IndexError('b')",
            "raise NameError('c')",
        ]
        report = run_self_healing(
            task="Fail three different ways.",
            generator=ScriptedGenerator(candidates),
            verifier=_never_passes,
            budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
            sleep=lambda _s: None,
        )
        self.assertFalse(report.succeeded)
        self.assertEqual(len(report.attempts), 3)
        self.assertIn("Retry budget exhausted", report.stop_reason)

    def test_syntax_error_is_diagnosed_without_executing(self) -> None:
        report = run_self_healing(
            task="Parse this broken program.",
            generator=ScriptedGenerator(
                ["def broken(\n    return 1\n", "print('fixed')"]),
            verifier=lambda output: (output.strip() == "fixed", "not fixed"),
            budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
            sleep=lambda _s: None,
        )
        self.assertTrue(report.succeeded)
        self.assertEqual(report.attempts[0].outcome, "syntax_error")
        self.assertEqual(report.attempts[0].failure_kind, FailureKind.SYNTAX)


class TestCodeExtraction(unittest.TestCase):
    def test_extracts_a_fenced_block(self) -> None:
        from critic_agent import _extract_code

        code = _extract_code(
            "Here it is:\n```python\ndef median(xs):\n    return 1\n```\n")
        self.assertIn("def median(xs):", code)

    def test_plain_code_without_fences_is_accepted(self) -> None:
        from critic_agent import _extract_code

        code = _extract_code("def median(xs):\n    return 1\n")
        self.assertIn("def median(xs):", code)

    def test_prose_without_code_raises(self) -> None:
        from critic_agent import _extract_code

        with self.assertRaises(LLMError):
            _extract_code("I am not sure how to write this function.")


@unittest.skipUnless(_llm_available(), "needs a configured LLM backend")
class TestMakerCheckerLive(unittest.TestCase):
    """Live tests: the real maker and the real model critic."""

    def test_llm_maker_repairs_the_buggy_median(self) -> None:
        client = _live_client()
        report = run_maker_checker(
            MEDIAN_SPEC, LLMMaker(client), Critic(MEDIAN_SPEC, MEDIAN_TESTS),
            max_rounds=3, first_draft=DRAFT_BUGGY)
        self.assertTrue(report.succeeded)
        self.assertLessEqual(report.rounds_used, 3)
        self.assertEqual(report.critiques[-1].verdict, "PASS")

    def test_llm_critic_agrees_the_buggy_draft_fails(self) -> None:
        client = _live_client()
        critique = Critic(MEDIAN_SPEC, MEDIAN_TESTS).critique(DRAFT_BUGGY)
        judgement = LLMCritic(client).judge(DRAFT_BUGGY, critique)
        self.assertEqual(judgement.verdict, "FAIL")
        self.assertGreaterEqual(judgement.confidence, 0.5)
        self.assertTrue(judgement.findings)
        self.assertTrue(judgement.reason)

    def test_llm_critic_agrees_the_fixed_draft_passes(self) -> None:
        client = _live_client()
        critique = Critic(MEDIAN_SPEC, MEDIAN_TESTS).critique(DRAFT_FIXED)
        judgement = LLMCritic(client).judge(DRAFT_FIXED, critique)
        self.assertEqual(judgement.verdict, "PASS")


@unittest.skipUnless(_llm_available(), "needs a configured LLM backend")
class TestSelfHealingLive(unittest.TestCase):
    """Live tests: the real generator inside the healing loop."""

    def test_llm_generator_heals_the_median(self) -> None:
        client = _live_client()
        report = run_self_healing(
            task="Write a median function and print median([3, 1, 2, 4]).",
            generator=LLMGenerator(client, first_code=BUGGY_MEDIAN),
            verifier=_median_verifier,
            budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
            sleep=lambda _s: None,
        )
        self.assertTrue(report.succeeded)
        self.assertLessEqual(len(report.attempts), 3)
        self.assertEqual(report.final_output.strip(), "2.5")

    def test_llm_generator_stops_on_an_unrepairable_failure(self) -> None:
        client = _live_client()
        report = run_self_healing(
            task="Import the qzx_cryptolib module and use its encrypt() "
                 "function on the string 'hello', then print the ciphertext.",
            generator=LLMGenerator(client),
            verifier=lambda output: (True, ""),
            budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
            sleep=lambda _s: None,
        )
        self.assertFalse(report.succeeded)
        self.assertEqual(len(report.attempts), 1)
        self.assertIn("Unrepairable failure", report.stop_reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
