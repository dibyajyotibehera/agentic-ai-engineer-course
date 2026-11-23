"""Degraded agent drill (Chapter 11, Exercise 6).

Wires the chapter's pieces together -- budgets, a circuit breaker
per provider, the model cascade, the cached-answer rung, the offline
queue and the refusal -- and runs the drill that proves the ladder
works before it is needed: take the primary provider offline, watch
the agent step down, restore the primary, and watch it step back up.

Alert thresholds in the report follow the LLM course deployment
section (https://github.com/mlabonne/llm-course): 95th percentile
latency above 2 seconds, error rate above 5%, memory above 90%.

No API key is needed for the deterministic drill: the providers are
scripted rungs that fail on a fixed schedule, which is the failure
injection the drill is studied with -- the drill must take the
primary offline on demand, and no real provider can be taken
offline on demand. The serving rungs in the live drill (``live_drill``)
are instead real model calls, and need a configured model backend
(``LLM_BACKEND=opencheap-connector`` on the author's machine, or
``LLM_API_KEY``); the live drill is skipped with a note when no
backend is configured. There is no second provider on this backend,
so the different-provider rung stays scripted even in the live
drill, and the drill says so -- a second provider would need its
own key in production.
Run:  python degraded_agent.py
"""

from __future__ import annotations

import statistics
import time

from circuit_breaker import CircuitBreaker, FakeClock, ProviderError
from cost_kill_switch import Budgets, CostMeter
from model_cascade import LiveRung, ModelCascade, Rung
from offline_queue import OfflineQueue


class DegradedAgent:
    """One agent with the whole ladder wired in.

    With no client, the rungs are scripted -- the deterministic
    drill, where the primary is taken offline on a fixed schedule.
    With a client (an ``LLMClient``), the frontier and cheaper rungs
    are real model calls (``LiveRung``): the outage is still injected
    on a fixed schedule, because that is the failure being studied,
    but the rung that serves the request is a real model, and the
    cost reported is real provider-reported dollars.
    """

    def __init__(self, client=None) -> None:
        self.client = client
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
        self.spent_usd: float = 0.0  # real money, live rungs only

    def _rungs(self, *, frontier_down: bool):
        """The ladder for one request: live rungs when a client is set."""
        if self.client is None:
            return [
                Rung("frontier", 10, [503] if frontier_down else []),
                Rung("cheaper-same-provider", 2, []),
                Rung("different-provider", 8, []),
            ]
        frontier = (Rung("frontier", 10, [503]) if frontier_down
                    else LiveRung("frontier", self.client, self.client.model))
        return [
            frontier,
            LiveRung("cheaper-same-provider", self.client,
                     self.client.judge_model),
            # No second provider on this backend: this rung stays
            # scripted even in the live drill, and is only reached if
            # the live cheap rung fails.
            Rung("different-provider", 8, []),
        ]

    def answer(self, prompt: str, *, frontier_down: bool) -> dict:
        """Serve one request, logging which rung served it and why.

        The frontier rung goes through its circuit breaker, so after
        three consecutive 503s the breaker opens and later requests
        skip the frontier entirely (failing fast) instead of paying
        for a call that is already known to fail.
        """
        started = time.monotonic()
        cascade = ModelCascade(
            self._rungs(frontier_down=frontier_down),
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
        self.spent_usd += result.total_cost_usd
        self.log.append({"prompt": prompt, "served_by": result.served_by,
                         "cause": "; ".join(result.attempts),
                         "cost": result.total_cost_units,
                         "cost_usd": round(result.total_cost_usd, 4),
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


def live_drill(client) -> None:
    """The same ladder, with real models on the serving rungs: two requests.

    First the primary healthy, served by the real frontier model;
    then the primary injected offline, served by the real cheap
    model after the scripted 503. Two live calls. The different-
    provider rung is not reached and stays scripted: there is no
    second provider on this backend.
    """
    agent = DegradedAgent(client=client)
    healthy = agent.answer(
        "What is the refund policy? Answer in one sentence.",
        frontier_down=False)
    print(f"live: primary healthy -> served by {healthy['served_by']}: "
          f"{healthy['answer'].strip()[:120]!r}")
    down = agent.answer(
        "What is the refund policy? Answer in one sentence.",
        frontier_down=True)
    print(f"live: primary offline -> served by {down['served_by']}: "
          f"{down['answer'].strip()[:120]!r}")
    for entry in agent.log:
        print(f"live:   {entry['served_by']:24} cost ${entry['cost_usd']:.4f}  "
              f"{entry['cause']}")
    print(f"live: real cost for the drill: ${agent.spent_usd:.4f}; "
          f"client totals: {client.total_prompt_tokens} prompt + "
          f"{client.total_completion_tokens} completion tokens")


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
    print("\n--- live check: the ladder with real models on the serving rungs ---")
    try:
        from llm_client import LLMClient

        if LLMClient.has_backend():
            live_drill(LLMClient())
        else:
            print("live check skipped: no model backend configured "
                  "(set LLM_API_KEY, or LLM_BACKEND=opencheap-connector)")
    except Exception as exc:  # noqa: BLE001 - the live check must not hide the drill
        print(f"live check failed: {exc}")
