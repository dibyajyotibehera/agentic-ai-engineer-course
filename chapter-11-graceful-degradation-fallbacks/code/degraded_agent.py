"""Degraded agent drill (Chapter 11, Exercise 6).

Wires the chapter's pieces together -- budgets, a circuit breaker
per provider, the model cascade, the cached-answer rung, the offline
queue and the refusal -- and runs the drill that proves the ladder
works before it is needed: take the primary provider offline, watch
the agent step down, restore the primary, and watch it step back up.

Alert thresholds in the report follow the LLM course deployment
section (https://github.com/mlabonne/llm-course): 95th percentile
latency above 2 seconds, error rate above 5%, memory above 90%.

No API key or paid service is needed: every provider is a mock.
Run:  python degraded_agent.py
"""

from __future__ import annotations

import statistics
import time

from circuit_breaker import CircuitBreaker, FakeClock, ProviderError
from cost_kill_switch import Budgets, CostMeter
from model_cascade import ModelCascade, Rung
from offline_queue import OfflineQueue


class DegradedAgent:
    """One agent with the whole ladder wired in, against mock providers."""

    def __init__(self) -> None:
        self.clock = FakeClock()
        self.breakers = {
            "frontier": CircuitBreaker(failure_threshold=3, reset_timeout_s=30.0,
                                       clock=self.clock),
            "other-provider": CircuitBreaker(failure_threshold=3, reset_timeout_s=30.0,
                                             clock=self.clock),
        }
        self.meter = CostMeter()
        self.budgets = Budgets(per_run=10_000.0)  # generous: the drill tests routing, not spend
        self.queue = OfflineQueue()
        self.log: list[dict] = []

    def answer(self, prompt: str, *, frontier_down: bool) -> dict:
        """Serve one request, logging which rung served it and why.

        The frontier rung goes through its circuit breaker, so after
        three consecutive 503s the breaker opens and later requests
        skip the frontier entirely (failing fast) instead of paying
        for a call that is already known to fail.
        """
        started = time.monotonic()
        cascade = ModelCascade(
            [
                Rung("frontier", 10, [503] if frontier_down else []),
                Rung("cheaper-same-provider", 2, []),
                Rung("different-provider", 8, []),
            ],
            cache={"What is the refund policy?": "[cached] Refunds within 30 days."},
            breakers=self.breakers,
        )
        try:
            result = cascade.run(prompt)
        except ProviderError as exc:
            self.log.append({"prompt": prompt, "served_by": "error",
                             "cause": f"non-retryable {exc.status}: caller bug",
                             "cost": 0, "latency_ms": 0})
            raise
        latency_ms = (time.monotonic() - started) * 1000
        self.meter.spent += result.total_cost_units
        self.log.append({"prompt": prompt, "served_by": result.served_by,
                         "cause": "; ".join(result.attempts),
                         "cost": result.total_cost_units,
                         "latency_ms": round(latency_ms, 1)})
        if result.served_by == "refusal":
            # A task that can wait goes to the queue instead of dying.
            self.queue.enqueue(prompt)
            self.log[-1]["cause"] += "; queued for replay"
        return {"served_by": result.served_by, "answer": result.answer}


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, int(0.95 * len(ordered)) - 1)]


def drill() -> None:
    agent = DegradedAgent()
    prompts = (["What is the refund policy?"] * 4
               + ["Summarise the outage report"] * 3
               + ["Draft the incident update"] * 3)

    print("=== drill phase 1: primary provider offline ===")
    for prompt in prompts:
        agent.answer(prompt, frontier_down=True)
    phase1 = agent.log[-len(prompts):]
    for entry in phase1:
        print(f"  {entry['served_by']:24} cost {entry['cost']:>2}  {entry['cause']}")

    print("\n=== drill phase 2: primary restored ===")
    # The reset timeout has elapsed, so the breaker is half-open: the
    # first restored call is the trial that closes it again.
    agent.clock.advance(31)
    for prompt in prompts:
        agent.answer(prompt, frontier_down=False)
    phase2 = agent.log[-len(prompts):]
    for entry in phase2:
        print(f"  {entry['served_by']:24} cost {entry['cost']:>2}  {entry['cause']}")

    def report(name: str, entries: list[dict]) -> None:
        from collections import Counter
        by_rung = Counter(e["served_by"] for e in entries)
        costs = [e["cost"] for e in entries]
        latencies = [e["latency_ms"] for e in entries]
        fallback_rate = 1 - by_rung.get("frontier", 0) / len(entries)
        print(f"\n--- {name} ---")
        print(f"requests: {len(entries)}; served by rung: {dict(by_rung)}")
        print(f"fallback rate: {fallback_rate:.0%}; refusal rate: "
              f"{by_rung.get('refusal', 0) / len(entries):.0%}")
        print(f"cost per task: mean {statistics.mean(costs):.1f}, "
              f"median {statistics.median(costs):.1f}, max {max(costs)}")
        print(f"latency: p50 {statistics.median(latencies):.1f}ms, "
              f"p95 {_p95(latencies):.1f}ms (alert above 2000ms)")

    report("phase 1 (primary offline)", phase1)
    report("phase 2 (primary restored)", phase2)

    print("\n--- alerts to ship ---")
    print("  p95 latency > 2s, error rate > 5%, memory > 90% "
          "(LLM course deployment thresholds)")
    print("  fallback rate > 10% for 15 minutes: page on-call -- the primary "
          "may have been down unnoticed, and degraded mode must not become permanent")
    print("  any refusal: log as an incident, not a success")
    print(f"\nqueued tasks awaiting replay: {agent.queue.size()}")


if __name__ == "__main__":
    drill()
