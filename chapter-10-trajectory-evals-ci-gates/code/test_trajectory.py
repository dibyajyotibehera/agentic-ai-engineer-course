"""Tests for the chapter's trajectory evals. Run with:

    python -m unittest test_trajectory -v

from inside the code/ directory. The deterministic tests need no
network, no model, and no API key. The live tests at the end need a
configured model backend (key-based: ``LLM_API_KEY``,
``LLM_BASE_URL`` and ``LLM_MODEL``; or AWS Bedrock:
``LLM_BACKEND=bedrock`` and ``LLM_BEDROCK_MODEL_ID`` — see
"Connect a model" in the root README) and skip when there is none: a
skipped live test means "not run", not "passed".
"""

import json
import unittest
from pathlib import Path

from ci_gate import canary_verdict, gate, run_suite
from faithfulness import LLMClaimChecker, context_recall_score, faithfulness_score
from grader import StepLevelGrader
from judge import LLMJudge, MockJudge, measure_agreement, trajectory_to_text
from llm_client import LLMClient
from trajectory import (
    GoldenDAG,
    Trajectory,
    edit_distance,
    jaccard_similarity,
    lcs_similarity,
    normalized_edit_distance,
    trajectory_distance,
)

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def load_fixtures():
    dag = GoldenDAG.from_json_file(DATA / "golden_refund_dag.json")
    with open(DATA / "trajectories.json", encoding="utf-8") as fh:
        dataset = json.load(fh)
    expected = {c["case_id"]: c["expected_outcome"] for c in dataset["cases"]}
    trajectories = {c["case_id"]: Trajectory.from_dict(c) for c in dataset["cases"]}
    return dag, expected, trajectories


class TestEditDistance(unittest.TestCase):
    def test_identical_sequences_are_zero(self):
        seq = ["fetch_order", "check_eligibility", "issue_refund"]
        self.assertEqual(edit_distance(seq, seq), 0)
        self.assertEqual(normalized_edit_distance(seq, seq), 0.0)

    def test_one_swap_costs_two_edits(self):
        # Swapping two adjacent steps is a delete plus an insert.
        a = ["fetch_order", "check_eligibility", "issue_refund"]
        b = ["fetch_order", "issue_refund", "check_eligibility"]
        self.assertEqual(edit_distance(a, b), 2)

    def test_empty_sequences(self):
        self.assertEqual(edit_distance([], []), 0)
        self.assertEqual(edit_distance(["a"], []), 1)
        self.assertEqual(normalized_edit_distance([], []), 0.0)

    def test_lcs_and_jaccard_ignore_order(self):
        a = ["fetch_order", "check_eligibility", "issue_refund"]
        b = ["issue_refund", "check_eligibility", "fetch_order"]
        self.assertEqual(lcs_similarity(a, b), 1 / 3)  # one step in order
        self.assertEqual(jaccard_similarity(a, b), 1.0)  # same set


class TestGoldenDAG(unittest.TestCase):
    def test_golden_run_is_valid(self):
        dag, _, trajectories = load_fixtures()
        self.assertTrue(dag.validate(trajectories["refund-golden"])["valid"])

    def test_denial_takes_the_other_branch(self):
        dag, _, trajectories = load_fixtures()
        report = dag.validate(trajectories["refund-denied-ineligible"])
        self.assertTrue(report["valid"], report)

    def test_ordering_error_is_a_precedence_violation(self):
        dag, _, trajectories = load_fixtures()
        report = dag.validate(trajectories["refund-ordering-error"])
        self.assertEqual(
            report["precedence_violations"],
            [("issue_refund", "check_eligibility")],
        )

    def test_missing_confirmation_is_reported(self):
        dag, _, trajectories = load_fixtures()
        report = dag.validate(trajectories["refund-missing-confirmation"])
        self.assertEqual(report["missing_required"], ["send_confirmation"])

    def test_refund_and_denial_together_is_a_branch_violation(self):
        from trajectory import Step

        dag, _, trajectories = load_fixtures()
        both = trajectories["refund-golden"]
        both.steps.append(
            Step(
                kind="tool_call",
                tool="send_denial",
                args={"ticket": "T-881", "reason": "outside 30-day window"},
                result={"sent": True},
            )
        )
        report = dag.validate(both)
        self.assertEqual(len(report["branch_violations"]), 1)
        self.assertFalse(report["valid"])


class TestGrader(unittest.TestCase):
    def test_golden_run_passes_every_check(self):
        dag, expected, trajectories = load_fixtures()
        grade = StepLevelGrader(dag, expected).grade(trajectories["refund-golden"])
        self.assertTrue(grade.passed, grade.summary())

    def test_ordering_error_fails_on_ordering_but_not_on_outcome(self):
        # The point of the chapter: the money is right and the run is
        # still a failure, and the grader must say so precisely.
        dag, expected, trajectories = load_fixtures()
        grade = StepLevelGrader(dag, expected).grade(
            trajectories["refund-ordering-error"]
        )
        self.assertFalse(grade.passed)
        by_name = {c.name: c for c in grade.checks}
        self.assertFalse(by_name["ordering"].passed)
        self.assertTrue(by_name["outcome"].passed)
        self.assertTrue(by_name["refund_arguments"].passed)
        self.assertEqual(grade.first_failure.name, "ordering")

    def test_wrong_refund_amount_fails_argument_check(self):
        dag, expected, trajectories = load_fixtures()
        run = trajectories["refund-golden"]
        run.steps[3].args["amount"] = 1.0
        grade = StepLevelGrader(dag, expected).grade(run)
        self.assertFalse(grade.passed)
        by_name = {c.name: c for c in grade.checks}
        self.assertFalse(by_name["refund_arguments"].passed)


class TestTrajectoryDistance(unittest.TestCase):
    def test_ordering_error_is_close_but_not_identical(self):
        _, _, trajectories = load_fixtures()
        golden = trajectories["refund-golden"].tool_sequence()
        buggy = trajectories["refund-ordering-error"].tool_sequence()
        dist = trajectory_distance(buggy, golden)
        self.assertEqual(dist["edit_distance"], 2)
        self.assertEqual(dist["missing_steps"], [])
        self.assertEqual(dist["extra_steps"], [])
        self.assertGreater(dist["lcs_similarity"], 0.5)

    def test_missing_step_shows_up_as_missing(self):
        _, _, trajectories = load_fixtures()
        golden = trajectories["refund-golden"].tool_sequence()
        short = trajectories["refund-missing-confirmation"].tool_sequence()
        dist = trajectory_distance(short, golden)
        self.assertEqual(dist["missing_steps"], ["send_confirmation"])


class TestFaithfulness(unittest.TestCase):
    def test_ragas_worked_example_scores_one_half(self):
        # The same arithmetic as the Ragas documentation's own example:
        # two claims, one supported, faithfulness 1 / 2 = 0.5. The
        # response here is written as two sentences because the mock
        # claim splitter works sentence by sentence, where Ragas uses
        # a model to split one sentence into two claims.
        result = faithfulness_score(
            response=(
                "Einstein was born in Germany. "
                "Einstein won the Nobel Prize in 1955."
            ),
            retrieved_contexts=[
                "Albert Einstein (born 14 March 1879) was a German-born "
                "theoretical physicist."
            ],
        )
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["supported"], 1)
        self.assertEqual(result["faithfulness"], 0.5)

    def test_context_recall_blames_retrieval(self):
        result = context_recall_score(
            reference="Einstein was born in Germany on 14th March 1879.",
            retrieved_contexts=["Einstein was a theoretical physicist."],
        )
        self.assertLess(result["context_recall"], 1.0)


class TestJudgeAgreement(unittest.TestCase):
    def test_mock_judge_and_agreement_arithmetic(self):
        judge = MockJudge()
        self.assertEqual(
            judge.grade("Your refund of $79.00 is on its way.", "")["label"],
            "pass",
        )
        self.assertEqual(
            judge.grade("Your refund is on its way.", "")["label"], "fail"
        )
        agreement = measure_agreement(
            ["pass", "pass", "fail", "pass"],
            ["pass", "fail", "fail", "pass"],
        )
        self.assertEqual(agreement["agreement"], 0.75)
        self.assertEqual(agreement["false_passes"], 1)
        self.assertEqual(agreement["false_fails"], 0)


class TestGate(unittest.TestCase):
    def test_gate_fails_on_the_critical_ordering_case(self):
        dag, expected, trajectories = load_fixtures()
        grader = StepLevelGrader(dag, expected)
        results = run_suite(grader, list(trajectories.values()))
        with open(DATA / "baseline.json", encoding="utf-8") as fh:
            baseline = json.load(fh)
        verdict = gate(results, baseline)
        self.assertFalse(verdict["passed"])
        self.assertTrue(
            any("refund-ordering-error" in b for b in verdict["breaches"])
        )

    def test_gate_passes_when_the_bug_is_fixed(self):
        dag, expected, trajectories = load_fixtures()
        good = [trajectories["refund-golden"], trajectories["refund-denied-ineligible"]]
        grader = StepLevelGrader(dag, expected)
        results = run_suite(grader, good)
        baseline = {"pass_rate": 1.0, "criteria": {k: 1.0 for k in results["criteria"]}}
        self.assertTrue(gate(results, baseline)["passed"])

    def test_gate_catches_an_aggregate_regression(self):
        # The chapter's worked example: a criterion falling from 0.92
        # to 0.71 is far outside a two-to-three point tolerance.
        results = {"pass_rate": 0.71, "criteria": {"ordering": 0.71}, "cases": []}
        baseline = {"pass_rate": 0.92, "criteria": {"ordering": 0.92}}
        verdict = gate(results, baseline, tolerance=0.03)
        self.assertFalse(verdict["passed"])


class TestCanaryVerdict(unittest.TestCase):
    STABLE = {
        "requests": 90_000,
        "error_rate": 0.004,
        "p95_ms": 900,
        "eval_pass": 0.93,
        "cost_per_request": 0.012,
    }

    def test_wait_below_two_thousand_requests(self):
        canary = dict(self.STABLE, requests=1_500)
        self.assertTrue(canary_verdict(self.STABLE, canary).startswith("wait"))

    def test_promote_when_everything_holds(self):
        canary = dict(
            self.STABLE, requests=4_800, error_rate=0.004, p95_ms=880,
            eval_pass=0.93, cost_per_request=0.012,
        )
        self.assertEqual(canary_verdict(self.STABLE, canary), "promote")

    def test_rollback_on_eval_pass_rate_alone(self):
        # Latency and errors are fine, so a classic service canary
        # would promote this. The eval gate must not.
        canary = dict(
            self.STABLE, requests=4_800, error_rate=0.004, p95_ms=880,
            eval_pass=0.85, cost_per_request=0.012,
        )
        self.assertEqual(
            canary_verdict(self.STABLE, canary), "roll back: eval pass rate"
        )


@unittest.skipUnless(LLMClient.has_backend(), "needs a configured LLM backend")
class TestLLMJudgeLive(unittest.TestCase):
    """Live tests: the real judge against the real model.

    The rubric grades the run itself — order, arguments and
    confirmation — because the fixture trajectories are tool-call
    records with no customer-facing message text. These tests assert
    the verdicts the demo shows, and they will fail if a judge model
    stops seeing the ordering error; that failure is the signal, not
    noise, because a judge that cannot see the chapter's central
    failure cannot be trusted to grade it.
    """

    RUBRIC = (
        "The run must check eligibility before issuing any refund, "
        "the refund amount and order must match what eligibility "
        "authorised, and the run must send a confirmation after the "
        "refund. A run that refunds before the eligibility check "
        "fails, even when the amount is correct."
    )

    def test_judge_passes_the_golden_run(self):
        _, _, trajectories = load_fixtures()
        verdict = LLMJudge().grade(
            trajectory_to_text(trajectories["refund-golden"]), self.RUBRIC
        )
        self.assertEqual(verdict["label"], "pass")

    def test_judge_fails_the_ordering_error_run(self):
        _, _, trajectories = load_fixtures()
        verdict = LLMJudge().grade(
            trajectory_to_text(trajectories["refund-ordering-error"]),
            self.RUBRIC,
        )
        self.assertEqual(verdict["label"], "fail")
        self.assertIn("eligibility", verdict["rationale"].lower())


@unittest.skipUnless(LLMClient.has_backend(), "needs a configured LLM backend")
class TestLLMClaimCheckerLive(unittest.TestCase):
    """Live test: the real claim checker on the Ragas example."""

    def test_claim_checker_scores_the_ragas_example_at_one_half(self):
        checker = LLMClaimChecker()
        result = faithfulness_score(
            response=(
                "Einstein was born in Germany. "
                "Einstein won the Nobel Prize in 1955."
            ),
            retrieved_contexts=[
                "Albert Einstein (born 14 March 1879) was a German-born "
                "theoretical physicist."
            ],
            entails=checker.entails,
        )
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["supported"], 1)
        self.assertEqual(result["faithfulness"], 0.5)


if __name__ == "__main__":
    unittest.main()
