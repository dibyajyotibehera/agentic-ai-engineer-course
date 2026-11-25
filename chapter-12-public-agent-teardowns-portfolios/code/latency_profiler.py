"""Latency profiler for the support copilot (Chapter 12).

Wraps a real agent run, records the wall-clock time of every step and
every tool call, and prints a flame-graph-style breakdown: each step
is a bar whose length is its share of the total time, so the dominant
cost is visible at a glance. It also reports the distribution across
many runs — mean, median, 95th percentile, and maximum — because a
teardown must show the slow tail, not just the average.

The profiler is framework-neutral: wrap any callable step with
``profile()`` or decorate it with ``@span``. The demo profiles the
real copilot in ``copilot.py`` — a plan call and a draft call to a
real model, four local tools fanning out in parallel, and a judge
call on the configured judge model — so it needs a model backend
configured (see "Connect a model" in the root README) and it
reports real numbers: the run total is wall-clock time, and the
parallel fan-out costs the slowest member, not the sum, because the
tools really run concurrently under a thread pool.

Serving threshold from Chapter 11, used by the report: an alert fires
when 95th-percentile latency rises above 2 seconds.

Usage (with a model backend configured):
    LLM_API_KEY=<key> LLM_BASE_URL=<base URL> LLM_MODEL=<model> python latency_profiler.py [--runs N] [--sequential]
    LLM_BACKEND=bedrock LLM_BEDROCK_MODEL_ID=<inference profile ID> python latency_profiler.py [--runs N] [--sequential]
"""

from __future__ import annotations

import argparse
import statistics
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TypeVar

from copilot import TICKETS, run_copilot
from llm_client import LLMClient

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
    # Wall-clock time of the whole run, when the profiler measured it.
    # The total is the wall time, not the sum of the spans: parallel
    # spans overlap, so their sum would overstate a fanned-out run.
    wall_seconds: float | None = None

    @property
    def total_seconds(self) -> float:
        if self.wall_seconds is not None:
            return self.wall_seconds
        return sum(s.duration_seconds for s in self.spans)

    def flame_graph(self, width: int = 40) -> str:
        """Render a text flame-graph-style breakdown of the run."""
        total = self.total_seconds
        lines = [f"Run {self.run_id}: {total:.2f}s total (wall clock)"]
        for span in sorted(self.spans, key=lambda s: -s.duration_seconds):
            share = span.duration_seconds / total if total else 0.0
            bar = "#" * max(1, min(width, round(share * width)))
            tag = " (parallel)" if span.parallel else ""
            lines.append(
                f"  {span.name:<28} {bar:<{width}} "
                f"{span.duration_seconds:5.2f}s {share:5.1%}{tag}"
            )
        if any(s.parallel for s in self.spans):
            lines.append(
                "  (parallel spans overlap, so their shares can sum past 100%;"
                " the total above is wall-clock time)"
            )
        return "\n".join(lines)


class Profiler:
    """Collects spans for one agent run. Use as a context manager per run.

    Thread-safe: the copilot's tool fan-out records spans from worker
    threads, so appends are guarded by a lock.
    """

    def __init__(self, run_id: str) -> None:
        self.profile = RunProfile(run_id=run_id)
        self._lock = threading.Lock()
        self._start: float | None = None

    def __enter__(self) -> "Profiler":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._start is not None:
            self.profile.wall_seconds = time.perf_counter() - self._start

    @contextmanager
    def span(self, name: str, parallel: bool = False):
        """Time one step. Yields nothing; records the elapsed wall time."""
        start = time.perf_counter()
        try:
            yield
        finally:
            with self._lock:
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


def profile_many(
    client: LLMClient,
    n_runs: int,
    tickets: list[dict],
    *,
    parallel: bool = True,
) -> tuple[list[RunProfile], list]:
    """Profile the real copilot over n_runs, cycling through the tickets.

    Returns the profiles and the copilot runs (which carry the measured
    token counts and the judge verdicts). Each run is three model calls
    — plan, draft, judge — so keep n_runs small; the default of three
    runs is nine model calls.
    """
    profiles = []
    runs = []
    for i in range(n_runs):
        with Profiler(run_id=f"run-{i + 1:02d}") as profiler:
            run = run_copilot(
                client, tickets[i % len(tickets)],
                profiler=profiler, parallel=parallel,
            )
        profiles.append(profiler.profile)
        runs.append(run)
    return profiles, runs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile the real support copilot's latency."
    )
    parser.add_argument("--runs", type=int, default=3,
                        help="runs to profile (default 3; each run is "
                        "three model calls, so keep this small)")
    parser.add_argument("--sequential", action="store_true",
                        help="run the four tools sequentially instead of "
                        "in parallel, to measure the fan-out saving")
    args = parser.parse_args()

    client = LLMClient()  # raises loudly without a configured backend
    mode = "sequential" if args.sequential else "parallel fan-out"
    print(f"Profiling the real copilot: {args.runs} runs, {mode}")
    profiles, runs = profile_many(
        client, args.runs, TICKETS, parallel=not args.sequential
    )
    print()
    print(profiles[0].flame_graph())
    print()
    print(distribution_report(profiles))
    print(slowest_step(profiles))
    print()
    passes = sum(1 for run in runs if run.judge_pass)
    print(f"Judge: {passes}/{len(runs)} drafts passed.")
    for run in runs:
        if not run.judge_pass:
            print(f"  {run.ticket_id} failed the judge: {run.judge_reason}")
    print(
        f"Model usage for this run: {client.total_prompt_tokens} prompt "
        f"tokens, {client.total_completion_tokens} completion tokens"
        + (f", ${client.total_cost_usd:.4f}"
           if client.total_cost_usd else "")
    )


if __name__ == "__main__":
    main()
