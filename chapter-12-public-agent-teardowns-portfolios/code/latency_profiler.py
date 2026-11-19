"""Latency profiler for the support copilot (Chapter 12).

Wraps an agent run, records the wall-clock time of every step and every
tool call, and prints a flame-graph-style breakdown: each step is a bar whose
length is its share of the total time, so the dominant cost is visible at a
glance. It also reports the distribution across many runs — mean, median,
95th percentile, and maximum — because a teardown must show the slow tail,
not just the average.

The profiler is framework-neutral: wrap any callable step with ``profile()``
or decorate it with ``@span``. The ``run_mock_copilot()`` demo simulates one
support ticket (four Model Context Protocol tools fanning out, a ReAct loop
of model calls, and a judge call) with ``time.sleep`` standing in for real
work, so the file runs with no model API key and no paid service. Point the
profiler at your real agent run for real numbers.

Serving threshold from Chapter 11, used by the report: an alert fires when
95th-percentile latency rises above 2 seconds.

Usage:
    python latency_profiler.py
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")

# Chapter 11 serving threshold: 95th-percentile latency above this trips an
# alert. The profiler reports whether the profiled runs cross it.
P95_ALERT_THRESHOLD_SECONDS = 2.0


@dataclass
class Span:
    """One timed step of an agent run."""

    name: str
    duration_seconds: float
    parallel: bool = False  # True if the step ran concurrently with siblings


@dataclass
class RunProfile:
    """All spans recorded for one agent run."""

    run_id: str
    spans: list[Span] = field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(s.duration_seconds for s in self.spans)

    def flame_graph(self, width: int = 40) -> str:
        """Render a text flame-graph-style breakdown of the run."""
        total = self.total_seconds
        lines = [f"Run {self.run_id}: {total:.2f}s total"]
        for span in sorted(self.spans, key=lambda s: -s.duration_seconds):
            share = span.duration_seconds / total if total else 0.0
            bar = "#" * max(1, round(share * width))
            tag = " (parallel)" if span.parallel else ""
            lines.append(
                f"  {span.name:<28} {bar:<{width}} "
                f"{span.duration_seconds:5.2f}s {share:5.1%}{tag}"
            )
        return "\n".join(lines)


class Profiler:
    """Collects spans for one agent run. Use as a context manager per run."""

    def __init__(self, run_id: str) -> None:
        self.profile = RunProfile(run_id=run_id)

    @contextmanager
    def span(self, name: str, parallel: bool = False):
        """Time one step. Yields nothing; records the elapsed wall time."""
        start = time.perf_counter()
        try:
            yield
        finally:
            self.profile.spans.append(
                Span(name, time.perf_counter() - start, parallel=parallel)
            )

    def time_call(self, name: str, fn: Callable[..., T], *args, **kwargs) -> T:
        """Time a callable and return its result."""
        with self.span(name):
            return fn(*args, **kwargs)


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in 0..100) over a list of durations."""
    if not values:
        raise ValueError("Need at least one value for a percentile.")
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(len(ordered) * pct / 100)))
    return ordered[rank]


def distribution_report(profiles: list[RunProfile]) -> str:
    """Report the latency distribution across many runs."""
    totals = [p.total_seconds for p in profiles]
    mean = statistics.mean(totals)
    median = statistics.median(totals)
    p95 = percentile(totals, 95)
    worst = max(totals)
    verdict = (
        "TRIPS the Chapter 11 alert threshold"
        if p95 > P95_ALERT_THRESHOLD_SECONDS
        else "within the Chapter 11 alert threshold"
    )
    return (
        f"Latency across {len(profiles)} runs:\n"
        f"  mean:   {mean:5.2f}s\n"
        f"  median: {median:5.2f}s\n"
        f"  p95:    {p95:5.2f}s\n"
        f"  max:    {worst:5.2f}s\n"
        f"  p95 {verdict} "
        f"(alert above {P95_ALERT_THRESHOLD_SECONDS:.0f}s)."
    )


def slowest_step(profiles: list[RunProfile]) -> str:
    """Name the step with the largest mean duration across runs."""
    totals: dict[str, list[float]] = {}
    for profile in profiles:
        for span in profile.spans:
            totals.setdefault(span.name, []).append(span.duration_seconds)
    name, durations = max(totals.items(), key=lambda kv: statistics.mean(kv[1]))
    mean = statistics.mean(durations)
    grand = sum(statistics.mean(v) for v in totals.values())
    return (
        f"Slowest step: {name}, mean {mean:.2f}s, "
        f"about {mean / grand:.0%} of a typical run."
    )


# ---------------------------------------------------------------------------
# Mock copilot: a simulated ticket, so the profiler runs with no API key.
# time.sleep stands in for real model calls and tool calls; the shape of the
# run — fan-out of four tools, then a loop of model calls, then a judge —
# is the real copilot's shape.
# ---------------------------------------------------------------------------

def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def run_mock_copilot(profiler: Profiler, ticket: str) -> str:
    """Simulate one support-copilot ticket. Returns a mock resolution."""
    with profiler.span("model: plan ticket", parallel=False):
        _sleep(0.35)
    # Four Model Context Protocol tools fan out per ticket. They run
    # concurrently in the real copilot, so they are marked parallel; the
    # wall-clock cost of the fan-out is the slowest member, not the sum.
    with profiler.span("tool: account_state", parallel=True):
        _sleep(0.30)
    with profiler.span("tool: subscription_status", parallel=True):
        _sleep(0.25)
    with profiler.span("tool: knowledge_base_search", parallel=True):
        _sleep(0.40)
    with profiler.span("tool: refund_eligibility", parallel=True):
        _sleep(0.90)  # slowest member: waits on a synchronous billing service
    with profiler.span("model: draft resolution", parallel=False):
        _sleep(0.45)
    with profiler.span("judge: grade resolution", parallel=False):
        _sleep(0.20)
    return f"resolved: {ticket}"


def profile_many(n_runs: int, tickets: list[str]) -> list[RunProfile]:
    """Profile the mock copilot over n_runs, cycling through the tickets."""
    profiles = []
    for i in range(n_runs):
        profiler = Profiler(run_id=f"run-{i + 1:02d}")
        run_mock_copilot(profiler, tickets[i % len(tickets)])
        profiles.append(profiler.profile)
    return profiles


def main() -> None:
    tickets = [
        "refund for duplicate charge",
        "subscription downgrade after renewal",
        "cannot log in after password reset",
        "invoice shows wrong billing address",
        "feature request: export to spreadsheet",
    ]
    profiles = profile_many(20, tickets)
    print(profiles[0].flame_graph())
    print()
    print(distribution_report(profiles))
    print(slowest_step(profiles))
    print()
    print("Note: sleeps simulate sequential spans here, so the fan-out's four")
    print("tools add up in this mock. In the real copilot they run in parallel")
    print("and the fan-out costs the slowest member (refund_eligibility), not")
    print("the sum — re-profile against the real run to show that difference.")


if __name__ == "__main__":
    main()
