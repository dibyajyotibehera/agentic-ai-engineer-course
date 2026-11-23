"""Failing-agent trace bundle generator for the rescue exercise (Chapter 12).

Generates a deterministic trace bundle for Exercise 4 ("rescue a failing
agent"): the support copilot whose cost per task has roughly tripled while
its evaluation pass rate has barely moved. The bundle contains exactly what
a student is handed — a cost table, a latency profile, and evaluation
scores, for a baseline week and a failing week — and nothing else. No
diagnosis is included in the bundle; the student must produce it, using the
chapter's instruments (the cost table, the latency profile, the benchmark,
and architecture decision records).

The numbers are ILLUSTRATIVE worked-example figures, labelled as such in
the bundle — deliberately constructed, not measured, because the
brokenness IS the exercise: the student is handed a failing agent and
must diagnose it from the shape of the change, not from a live run.
Token counts follow the live copilot's shape in ``copilot.py`` (three
model calls, four Model Context Protocol tool calls, memory context,
one judge call), and the prices are illustrative per-token prices, not
any provider's published prices. What matters is the shape of the
change between the two weeks, not the absolute dollars. Nothing in the
bundle is presented as a measured teardown number.

The generator is deterministic: no randomness, no clock, no network. Run
it twice and the bundle is byte-identical. Standard library only.

Usage:
    python rescue_fixture.py [--out rescue_bundle.json]

    Prints a short summary and writes the bundle as JSON. The default
    output path is ``rescue_bundle.json`` next to this file.
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path


# Illustrative per-token prices (worked-example assumptions, not published
# prices). Baseline: the copilot's original model. Failing week: the model
# the copilot was swapped to, at higher per-token prices.
BASELINE_INPUT_PER_MILLION = 3.00
BASELINE_OUTPUT_PER_MILLION = 15.00
FAILING_INPUT_PER_MILLION = 6.00
FAILING_OUTPUT_PER_MILLION = 30.00

# Baseline trace shape: the live copilot's shape (copilot.py) — three
# model calls, four tool calls, 1,500 memory tokens, one judge call.
# The counts themselves are illustrative worked-example figures.
BASELINE_MODEL_CALLS = [(3_200, 420), (2_800, 380), (2_100, 510)]
BASELINE_MEMORY_TOKENS = 1_500
BASELINE_EVAL_TOKENS = (1_800, 120)

# Failing-week trace shape: more model calls per ticket (the loop runs
# longer and retries), more memory tokens carried per call, and extra
# knowledge-base tool calls (4 per ticket, was 2). The per-call token
# counts are larger too. Tool calls stay internal, so tool cost stays $0
# in both weeks — the cost growth is in the model, memory, and judge lines.
FAILING_MODEL_CALLS = [(3_600, 500), (3_200, 450), (2_900, 420), (2_600, 400)]
FAILING_MEMORY_TOKENS = 3_200
FAILING_EVAL_TOKENS = (1_800, 120)
FAILING_TOOL_CALLS_PER_TASK = 7  # was 5: knowledge-base searches rose 2 -> 4
BASELINE_TOOL_CALLS_PER_TASK = 5  # 1 account + 1 subscription + 2 KB + 1 refund

# Latency, seconds per run, over 20 runs each week. Deterministic arithmetic
# progressions, not measurements: the baseline week follows the live
# copilot's shape, the failing week runs longer because the loop makes
# more calls.
BASELINE_LATENCIES = [round(2.40 + 0.05 * i, 2) for i in range(20)]
FAILING_LATENCIES = [round(3.10 + 0.06 * i, 2) for i in range(20)]

# Evaluation: pass/fail per ticket over a fixed set of 20 tickets.
BASELINE_EVAL_PASSES = 19  # 19 of 20 tickets pass
FAILING_EVAL_PASSES = 18  # 18 of 20 tickets pass
EVAL_TICKETS = 20


@dataclass
class CostLine:
    """One task's four-line cost breakdown, in US dollars.

    Token counts are included alongside the dollars, because the diagnosis
    needs them: a cost rise driven by more model calls looks different from
    one driven by a higher price per token, and only the counts tell them
    apart.
    """

    task_id: str
    model: float
    tools: float
    memory: float
    evaluation: float
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    memory_tokens: int = 0
    tool_calls: int = 0

    @property
    def total(self) -> float:
        return self.model + self.tools + self.memory + self.evaluation

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "model": round(self.model, 4),
            "tools": round(self.tools, 4),
            "memory": round(self.memory, 4),
            "evaluation": round(self.evaluation, 4),
            "total": round(self.total, 4),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model_calls": self.model_calls,
            "memory_tokens": self.memory_tokens,
            "tool_calls": self.tool_calls,
        }


@dataclass
class WeekBundle:
    """One week's cost table, latency profile, and evaluation scores."""

    week: str
    model_name: str
    cost_per_task: list[CostLine] = field(default_factory=list)
    latencies_seconds: list[float] = field(default_factory=list)
    eval_passes: int = 0
    eval_tickets: int = EVAL_TICKETS

    def mean_cost_per_task(self) -> float:
        return statistics.mean(c.total for c in self.cost_per_task)

    def latency_summary(self) -> dict[str, float]:
        ordered = sorted(self.latencies_seconds)
        n = len(ordered)
        return {
            "mean": round(statistics.mean(ordered), 2),
            "median": round(statistics.median(ordered), 2),
            # Nearest-rank 95th percentile, same definition as
            # latency_profiler.percentile.
            "p95": ordered[max(0, min(n - 1, int(n * 95 / 100)))],
            "max": ordered[-1],
        }

    def as_dict(self) -> dict:
        return {
            "week": self.week,
            "model_name": self.model_name,
            "cost_per_task": [c.as_dict() for c in self.cost_per_task],
            "mean_cost_per_task_usd": round(self.mean_cost_per_task(), 4),
            "latencies_seconds": self.latencies_seconds,
            "latency_summary_seconds": self.latency_summary(),
            "eval_pass_rate": round(self.eval_passes / self.eval_tickets, 2),
            "eval_passes": self.eval_passes,
            "eval_tickets": self.eval_tickets,
        }


def _price_calls(
    calls: list[tuple[int, int]], input_per_million: float, output_per_million: float
) -> float:
    return sum(
        i * input_per_million / 1_000_000 + o * output_per_million / 1_000_000
        for i, o in calls
    )


def _baseline_week() -> WeekBundle:
    costs = []
    for i in range(5):
        model = _price_calls(
            BASELINE_MODEL_CALLS,
            BASELINE_INPUT_PER_MILLION,
            BASELINE_OUTPUT_PER_MILLION,
        )
        memory = BASELINE_MEMORY_TOKENS * BASELINE_INPUT_PER_MILLION / 1_000_000
        evaluation = _price_calls(
            [BASELINE_EVAL_TOKENS],
            BASELINE_INPUT_PER_MILLION,
            BASELINE_OUTPUT_PER_MILLION,
        )
        costs.append(CostLine(
            task_id=f"ticket-{i + 1:03d}",
            model=model,
            tools=0.0,
            memory=memory,
            evaluation=evaluation,
            input_tokens=sum(i for i, _ in BASELINE_MODEL_CALLS),
            output_tokens=sum(o for _, o in BASELINE_MODEL_CALLS),
            model_calls=len(BASELINE_MODEL_CALLS),
            memory_tokens=BASELINE_MEMORY_TOKENS,
            tool_calls=BASELINE_TOOL_CALLS_PER_TASK,
        ))
    return WeekBundle(
        week="baseline",
        model_name="illustrative-main-model",
        cost_per_task=costs,
        latencies_seconds=BASELINE_LATENCIES,
        eval_passes=BASELINE_EVAL_PASSES,
    )


def _failing_week() -> WeekBundle:
    costs = []
    for i in range(5):
        model = _price_calls(
            FAILING_MODEL_CALLS,
            FAILING_INPUT_PER_MILLION,
            FAILING_OUTPUT_PER_MILLION,
        )
        memory = FAILING_MEMORY_TOKENS * FAILING_INPUT_PER_MILLION / 1_000_000
        evaluation = _price_calls(
            [FAILING_EVAL_TOKENS],
            FAILING_INPUT_PER_MILLION,
            FAILING_OUTPUT_PER_MILLION,
        )
        costs.append(CostLine(
            task_id=f"ticket-{i + 1:03d}",
            model=model,
            tools=0.0,
            memory=memory,
            evaluation=evaluation,
            input_tokens=sum(i for i, _ in FAILING_MODEL_CALLS),
            output_tokens=sum(o for _, o in FAILING_MODEL_CALLS),
            model_calls=len(FAILING_MODEL_CALLS),
            memory_tokens=FAILING_MEMORY_TOKENS,
            tool_calls=FAILING_TOOL_CALLS_PER_TASK,
        ))
    return WeekBundle(
        week="failing",
        model_name="illustrative-large-model",
        cost_per_task=costs,
        latencies_seconds=FAILING_LATENCIES,
        eval_passes=FAILING_EVAL_PASSES,
    )


def build_bundle() -> dict:
    """Build the deterministic failing-agent trace bundle."""
    baseline = _baseline_week()
    failing = _failing_week()
    ratio = failing.mean_cost_per_task() / baseline.mean_cost_per_task()
    return {
        "bundle": "support-copilot rescue exercise (Chapter 12, Exercise 4)",
        "note": (
            "ILLUSTRATIVE worked-example figures, deliberately constructed "
            "for the rescue exercise (not measured): token counts follow "
            "the live copilot's shape in copilot.py and prices are "
            "illustrative per-token prices, not any provider's published "
            "prices. Diagnose from the shape of the change between the two "
            "weeks, not from the absolute dollars."
        ),
        "baseline": baseline.as_dict(),
        "failing": failing.as_dict(),
        "headline": {
            "mean_cost_per_task_baseline_usd": round(
                baseline.mean_cost_per_task(), 4
            ),
            "mean_cost_per_task_failing_usd": round(
                failing.mean_cost_per_task(), 4
            ),
            "cost_ratio_failing_over_baseline": round(ratio, 2),
            "p95_latency_baseline_s": baseline.latency_summary()["p95"],
            "p95_latency_failing_s": failing.latency_summary()["p95"],
            "eval_pass_rate_baseline": round(
                baseline.eval_passes / baseline.eval_tickets, 2
            ),
            "eval_pass_rate_failing": round(
                failing.eval_passes / failing.eval_tickets, 2
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the deterministic failing-agent trace bundle."
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).with_name("rescue_bundle.json")),
        help="Where to write the bundle JSON (default: rescue_bundle.json "
        "next to this file).",
    )
    args = parser.parse_args()
    bundle = build_bundle()
    out = Path(args.out)
    out.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
    headline = bundle["headline"]
    print("Support copilot — rescue exercise trace bundle")
    print("=" * 52)
    print(f"  baseline cost per task: "
          f"${headline['mean_cost_per_task_baseline_usd']:.4f}")
    print(f"  failing cost per task:  "
          f"${headline['mean_cost_per_task_failing_usd']:.4f} "
          f"({headline['cost_ratio_failing_over_baseline']:.2f}x baseline)")
    print(f"  baseline p95 latency:   "
          f"{headline['p95_latency_baseline_s']:.2f}s")
    print(f"  failing p95 latency:    "
          f"{headline['p95_latency_failing_s']:.2f}s")
    print(f"  baseline eval pass rate: "
          f"{headline['eval_pass_rate_baseline']:.0%}")
    print(f"  failing eval pass rate:  "
          f"{headline['eval_pass_rate_failing']:.0%}")
    print(f"\nBundle written to {out}")
    print("Diagnose it with the chapter's instruments — cost table, latency")
    print("profile, benchmark, and architecture decision records — and write")
    print("the rescue plan as ADRs (see exercises.md, Exercise 4).")


if __name__ == "__main__":
    main()
