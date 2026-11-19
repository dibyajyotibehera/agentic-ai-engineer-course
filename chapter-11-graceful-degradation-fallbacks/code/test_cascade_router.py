"""Tests for cascade_router.py (Chapter 11, cost-aware routing).

Standard library only.  Run from the code/ directory:

    python test_cascade_router.py
    python -m unittest test_cascade_router -v

Every number here is a worked example from the simulated pool in
cascade_router.py -- illustrative prices and scripted quality scores,
not real vendor prices or measured results.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cascade_router import (  # noqa: E402
    CascadeRouter,
    GoldenTask,
    Task,
    complexity_score,
    default_golden_set,
    default_pool,
    evaluate_router,
    expected_cost,
    replay_matrix,
)


class TestExpectedCost(unittest.TestCase):
    def test_expected_cost_arithmetic(self):
        pool = {m.name: m for m in default_pool()}
        # Worked example from the lesson: 60% small, 30% mid, 10%
        # frontier, 2,000 in / 500 out tokens a call.
        # small: (2000*0.50 + 500*2.00)/1e6 = 0.0020
        # mid:   (2000*3.00 + 500*12.00)/1e6 = 0.0120
        # front: (2000*15.00 + 500*60.00)/1e6 = 0.0600
        # E = 0.6*0.0020 + 0.3*0.0120 + 0.1*0.0600 = 0.0108
        self.assertAlmostEqual(
            expected_cost({"small": 0.6, "mid": 0.3, "frontier": 0.1},
                          pool, 2000, 500),
            0.0108, places=6)
        self.assertAlmostEqual(
            expected_cost({"frontier": 1.0}, pool, 2000, 500),
            0.0600, places=6)

    def test_expected_cost_shares_must_sum_to_one(self):
        pool = {m.name: m for m in default_pool()}
        with self.assertRaises(ValueError):
            expected_cost({"small": 0.6, "mid": 0.3}, pool, 2000, 500)


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.router = CascadeRouter(mode="predictive")

    def test_routing_down_on_easy_task(self):
        decision = self.router.route(
            Task("Summarise this refund policy in two sentences.",
                 "summarise"))
        self.assertEqual(decision.model, "small")
        self.assertGreaterEqual(decision.predicted_quality, 0.85)

    def test_routing_up_on_hard_task(self):
        hard = Task("Design a multi-step migration plan with trade-off "
                    "analysis and edge cases for the billing service.",
                    "plan")
        self.assertGreaterEqual(complexity_score(hard), 0.70)
        decision = self.router.route(hard)
        self.assertEqual(decision.model, "frontier")
        self.assertGreaterEqual(decision.predicted_quality, 0.85)

    def test_cheapest_only_counts_if_it_clears_quality_bar(self):
        # The small model scores 0.55 on "plan" -- below the 0.85 bar --
        # so even a short planning prompt must not be routed to it.
        decision = self.router.route(Task("Plan the move.", "plan"))
        self.assertNotEqual(decision.model, "small")

    def test_per_step_routing_uses_different_models_in_one_run(self):
        run = self.router.run_agent([
            Task("Design a multi-step migration plan with trade-off "
                 "analysis.", "plan"),
            Task("Format this address list as a table.", "format"),
        ])
        self.assertEqual(run["models_used"], ["frontier", "small"])
        self.assertLess(run["total_cost"],
                        2 * 0.0600)  # cheaper than two frontier calls

    def test_fixed_mode_ignores_prompt_complexity(self):
        fixed = CascadeRouter(mode="fixed", version="v1-fixed")
        # A long, hard-marked summarise prompt still goes small under
        # fixed rules, because fixed routing looks only at task type.
        decision = fixed.route(Task(
            "Summarise this contract and compare the trade-off clauses "
            "with a multi-step analysis of the ambiguous edge cases. " * 3,
            "summarise"))
        self.assertEqual(decision.model, "small")


class TestShadowMode(unittest.TestCase):
    def test_shadow_mode_does_not_enforce(self):
        router = CascadeRouter(mode="predictive")
        served = router.shadow_route(
            Task("Summarise this refund policy in two sentences.",
                 "summarise"),
            enforced_model="frontier")
        # The user gets the enforced model, not the router's choice.
        self.assertEqual(served.model, "frontier")
        self.assertEqual(len(router.shadow_log), 1)
        self.assertEqual(router.shadow_log[0]["would_choose"], "small")
        self.assertEqual(router.shadow_log[0]["enforced"], "frontier")
        self.assertLess(router.shadow_log[0]["would_cost"],
                        router.shadow_log[0]["enforced_cost"])


class TestEvalHarness(unittest.TestCase):
    def test_golden_set_gate_passes_good_router(self):
        router = CascadeRouter(mode="predictive")
        report = evaluate_router(router, default_golden_set())
        self.assertEqual(report.passed, report.tasks)
        self.assertEqual(report.pass_rate, 1.0)
        self.assertEqual(report.failures, [])

    def test_golden_set_gate_fails_bad_router_version(self):
        # always_small routes the hard plan/reason tasks to a model
        # scoring 0.55/0.60 -- below the 0.85 bar -- so it must fail.
        bad = CascadeRouter(mode="always_small", version="bad-always-small")
        report = evaluate_router(bad, default_golden_set())
        self.assertLess(report.pass_rate, 1.0)
        self.assertTrue(report.failures)
        self.assertLess(report.avg_cost_per_task,
                        evaluate_router(CascadeRouter(mode="predictive"),
                                        default_golden_set()).avg_cost_per_task)

    def test_replay_matrix_compares_versions_on_same_tasks(self):
        golden = default_golden_set()
        reports = replay_matrix(
            [CascadeRouter(mode="fixed", version="v1-fixed"),
             CascadeRouter(mode="predictive", version="v2-predictive")],
            golden)
        self.assertEqual([r.router_version for r in reports],
                         ["v1-fixed", "v2-predictive"])
        self.assertTrue(all(r.tasks == len(golden) for r in reports))
        # Predictive routing must not be dearer than fixed routing on
        # this golden set: it steps the hard tasks up, but steps the
        # easy extract task down to small where fixed pays for mid.
        self.assertLessEqual(reports[1].avg_cost_per_task,
                             reports[0].avg_cost_per_task)


if __name__ == "__main__":
    unittest.main(verbosity=2)
