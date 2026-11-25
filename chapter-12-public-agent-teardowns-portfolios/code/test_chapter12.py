"""Tests for Chapter 12.

Run with ``python -m unittest test_chapter12 -v`` from this directory.

The deterministic tests — the profiler maths, the cost arithmetic,
the attribution rollup, the rescue bundle, and the copilot's local
tools — need no model and no network. Hand-built traces in these
tests are test fixtures with small round numbers, labelled as such
in the test names; they are not teardown numbers.

The live tests at the end need a configured model backend
(a key-based backend or Bedrock — see "Connect a model" in the root
README) and run the real copilot: plan and draft calls to the model,
four local tools, and a judge call on the configured judge model.
When no backend is configured they skip instead of failing: a
skipped live test means "not run", not "passed".
"""

from __future__ import annotations

import unittest

from copilot import (
    TICKETS,
    CopilotRun,
    account_state,
    knowledge_base_search,
    refund_eligibility,
    run_copilot,
    run_tools,
)
from cost_per_task import (
    ModelPricing,
    TaskTrace,
    ToolCall,
    attribute_steps,
    attribute_task,
    cost_of_trace,
    rollup_conversation,
    summarise,
    trace_from_run,
)
from latency_profiler import (
    Profiler,
    RunProfile,
    Span,
    distribution_report,
    percentile,
    profile_many,
    slowest_step,
)
from llm_client import LLMClient
from rescue_fixture import build_bundle


def _llm_available() -> bool:
    return LLMClient.has_backend()


def _live_client() -> LLMClient:
    return LLMClient()


def _fixture_trace() -> TaskTrace:
    """A small hand-built trace for arithmetic tests only."""
    return TaskTrace(
        task_id="fixture-001",
        model_calls=[(1_000, 100), (2_000, 200)],
        tools=[ToolCall("knowledge_base_search", calls=2)],
        memory_tokens=500,
        eval_input_tokens=400,
        eval_output_tokens=50,
    )


_PRICING = ModelPricing(name="fixture", input_per_million=2.00, output_per_million=10.00)


class TestProfilerMaths(unittest.TestCase):
    def test_percentile_nearest_rank(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 95), 4.0)
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 50), 3.0)

    def test_percentile_needs_a_value(self) -> None:
        with self.assertRaises(ValueError):
            percentile([], 95)

    def test_span_records_wall_time(self) -> None:
        profiler = Profiler(run_id="run-01")
        with profiler.span("model: plan ticket"):
            pass
        self.assertEqual(len(profiler.profile.spans), 1)
        self.assertGreaterEqual(profiler.profile.spans[0].duration_seconds, 0.0)

    def test_wall_time_is_the_total_when_measured(self) -> None:
        with Profiler(run_id="run-01") as profiler:
            with profiler.span("step"):
                pass
        profile = profiler.profile
        self.assertIsNotNone(profile.wall_seconds)
        self.assertAlmostEqual(profile.total_seconds, profile.wall_seconds, places=2)

    def test_distribution_report_names_the_threshold(self) -> None:
        profiles = [
            RunProfile(run_id=f"run-{i}", wall_seconds=0.5 + 0.5 * i)
            for i in range(3)
        ]
        report = distribution_report(profiles)
        self.assertIn("mean:", report)
        self.assertIn("p95:", report)
        self.assertIn("within the Chapter 11 alert threshold", report)

    def test_slowest_step_names_the_slow_span(self) -> None:
        profiles = [
            RunProfile(run_id="run-01", spans=[
                Span("model: draft resolution", 1.5),
                Span("tool: account_state", 0.01),
            ]),
            RunProfile(run_id="run-02", spans=[
                Span("model: draft resolution", 2.5),
                Span("tool: account_state", 0.02),
            ]),
        ]
        self.assertIn("model: draft resolution", slowest_step(profiles))

    def test_flame_graph_shows_each_span(self) -> None:
        profile = RunProfile(run_id="run-01", spans=[
            Span("model: plan ticket", 1.0),
            Span("tool: refund_eligibility", 0.5, parallel=True),
        ])
        graph = profile.flame_graph()
        self.assertIn("model: plan ticket", graph)
        self.assertIn("(parallel)", graph)


class TestCostArithmetic(unittest.TestCase):
    def test_pricing_applies_input_and_output_rates(self) -> None:
        pricing = ModelPricing(name="x", input_per_million=2.00, output_per_million=10.00)
        self.assertAlmostEqual(pricing.cost(1_000_000, 1_000_000), 12.00)

    def test_four_lines_sum_to_the_total(self) -> None:
        breakdown = cost_of_trace(_fixture_trace(), _PRICING)
        self.assertAlmostEqual(
            breakdown["total"],
            breakdown["model"] + breakdown["tools"]
            + breakdown["memory"] + breakdown["evaluation"],
        )

    def test_memory_is_priced_at_the_input_rate(self) -> None:
        breakdown = cost_of_trace(_fixture_trace(), _PRICING)
        self.assertAlmostEqual(breakdown["memory"], 500 * 2.00 / 1_000_000)

    def test_summarise_reports_mean_and_worst(self) -> None:
        summary = summarise([_fixture_trace(), _fixture_trace()], _PRICING)
        self.assertEqual(summary["n"], 2.0)
        self.assertAlmostEqual(
            summary["mean_total"], cost_of_trace(_fixture_trace(), _PRICING)["total"]
        )
        self.assertAlmostEqual(summary["worst_total"], summary["mean_total"])

    def test_attributed_steps_sum_to_the_trace_total(self) -> None:
        trace = _fixture_trace()
        total = cost_of_trace(trace, _PRICING)["total"]
        steps = attribute_steps(trace, _PRICING)
        self.assertAlmostEqual(sum(s.cost_usd for s in steps), total)

    def test_rollup_sums_tasks_and_rejects_mixed_conversations(self) -> None:
        trace = _fixture_trace()
        tasks = [
            attribute_task(trace, _PRICING, conversation_id="conv-1",
                           owner="team", product="copilot"),
            attribute_task(trace, _PRICING, conversation_id="conv-1",
                           owner="team", product="copilot"),
        ]
        rollup = rollup_conversation(tasks)
        self.assertEqual(rollup["n_tasks"], 2)
        self.assertAlmostEqual(
            rollup["total"], 2 * cost_of_trace(trace, _PRICING)["total"]
        )
        other = attribute_task(trace, _PRICING, conversation_id="conv-2",
                               owner="team", product="copilot")
        with self.assertRaises(ValueError):
            rollup_conversation([tasks[0], other])


class TestCopilotToolsDeterministic(unittest.TestCase):
    def test_account_state_reads_the_local_record(self) -> None:
        self.assertIn("Pro", account_state("cust-101"))
        self.assertIn("duplicate", account_state("cust-101"))

    def test_knowledge_base_search_matches_the_refund_policy(self) -> None:
        result = knowledge_base_search("refund for duplicate charge")
        self.assertIn("duplicate charge", result)

    def test_refund_eligibility_flags_the_duplicate_charge(self) -> None:
        self.assertIn("ELIGIBLE", refund_eligibility("cust-101"))
        self.assertNotIn("ELIGIBLE", refund_eligibility("cust-103"))

    def test_run_tools_returns_all_four_tools(self) -> None:
        results = run_tools(TICKETS[0])
        self.assertEqual(
            set(results),
            {"account_state", "subscription_status",
             "knowledge_base_search", "refund_eligibility"},
        )

    def test_trace_from_run_carries_measured_tokens(self) -> None:
        run = CopilotRun(
            ticket_id="ticket-001", resolution="r", plan="p",
            judge_pass=True, judge_reason="ok",
            model_calls=[(1000, 100), (2000, 200)],
            eval_calls=[(400, 50)],
            tool_calls=["account_state"],
            memory_tokens=300,
        )
        trace = trace_from_run(run)
        self.assertEqual(trace.total_input_tokens, 3000)
        self.assertEqual(trace.eval_input_tokens, 400)
        self.assertEqual(trace.memory_tokens, 300)


class TestRescueFixture(unittest.TestCase):
    def test_bundle_is_deterministic(self) -> None:
        self.assertEqual(build_bundle(), build_bundle())

    def test_failing_week_costs_about_three_times_baseline(self) -> None:
        bundle = build_bundle()
        ratio = bundle["headline"]["cost_ratio_failing_over_baseline"]
        self.assertGreater(ratio, 2.5)
        self.assertLess(ratio, 3.5)

    def test_eval_pass_rate_barely_moved(self) -> None:
        bundle = build_bundle()
        self.assertEqual(bundle["headline"]["eval_pass_rate_baseline"], 0.95)
        self.assertEqual(bundle["headline"]["eval_pass_rate_failing"], 0.90)

    def test_bundle_labels_its_figures_illustrative(self) -> None:
        bundle = build_bundle()
        self.assertIn("ILLUSTRATIVE", bundle["note"])
        self.assertIn("not measured", bundle["note"])


@unittest.skipUnless(_llm_available(), "needs a configured LLM backend")
class TestCopilotLive(unittest.TestCase):
    """Live tests: the real copilot against the real model."""

    def test_copilot_resolves_the_duplicate_charge_ticket(self) -> None:
        client = _live_client()
        run = run_copilot(client, TICKETS[0])
        self.assertTrue(run.resolution.strip())
        self.assertTrue(run.judge_pass, run.judge_reason)
        self.assertGreater(run.total_input_tokens, 0)
        self.assertGreater(run.total_output_tokens, 0)
        self.assertGreater(sum(o for _, o in run.eval_calls), 0)

    def test_profile_many_reports_real_wall_time(self) -> None:
        client = _live_client()
        profiles, runs = profile_many(client, 2, TICKETS[:2])
        self.assertEqual(len(profiles), 2)
        for profile in profiles:
            self.assertGreater(profile.total_seconds, 0)
            names = {span.name for span in profile.spans}
            self.assertIn("model: plan ticket", names)
            self.assertIn("model: draft resolution", names)
            self.assertIn("judge: grade resolution", names)
        self.assertIn("model:", slowest_step(profiles))

    def test_live_trace_prices_above_zero(self) -> None:
        client = _live_client()
        run = run_copilot(client, TICKETS[1])
        trace = trace_from_run(run)
        breakdown = cost_of_trace(trace, _PRICING)
        self.assertGreater(breakdown["total"], 0)
        self.assertGreater(breakdown["model"], 0)
        self.assertGreater(breakdown["evaluation"], 0)


if __name__ == "__main__":
    unittest.main()
