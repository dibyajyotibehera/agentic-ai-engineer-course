"""Model cascade (Chapter 11, Exercise 2).

The full fallback ladder, in order:

    frontier model
      -> cheaper model, same provider
      -> model from a different provider
      -> cached answer
      -> refusal

Each rung is tried only when the rung above it has provably failed,
each attempt is tallied, and a rung must pass a small evaluation gate
before it is allowed to serve.  A 400 from the frontier model never
triggers the cascade at all: a 400 is your bug, not a retry (see the
LLMOps.si deployment article,
https://www.llmops.si/articles/llm-deployment-ci-cd-staging-rollback/).

Costs are abstract cost units, not real money: 10 for the frontier
model, 2 for the cheaper model, 8 for the different provider, 0 for
the cache and 0 for a refusal.  Replace the price table with your own
measured cost per task before using this in production.

No API key or paid service is needed: every model is a mock.
Run:  python model_cascade.py
"""

from __future__ import annotations

from dataclasses import dataclass, field

from circuit_breaker import CircuitBreaker, CircuitOpenError, ProviderError, RETRYABLE_STATUSES


@dataclass
class Rung:
    """One step of the cascade: a mock model with a price and a gate."""

    name: str
    cost_units: int
    # Statuses this mock raises, in order; once exhausted it succeeds.
    fail_with: list[int] = field(default_factory=list)
    # Evaluation gate: (questions passed, questions total).  A rung
    # serves only if it passes at least 4 of 5.
    eval_passed: int = 5
    eval_total: int = 5
    calls: int = 0

    def passes_gate(self) -> bool:
        return self.eval_passed >= 4 and self.eval_total > 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        if self.fail_with:
            raise ProviderError(self.fail_with.pop(0), f"{self.name} failed")
        return f"[{self.name}] answer to: {prompt}"


@dataclass
class CascadeResult:
    served_by: str
    answer: str
    total_cost_units: int
    calls_made: int
    attempts: list[str]  # human-readable trace of every attempt


class ModelCascade:
    """Tries rungs in order, then the cache, then refuses.

    ``breakers`` optionally maps a rung name to a CircuitBreaker: a
    rung whose breaker is open is skipped without being called, and a
    retryable failure is recorded on the breaker, so a known-dead
    provider is never paid a timeout for again.
    """

    def __init__(self, rungs: list[Rung], cache: dict[str, str] | None = None,
                 breakers: dict[str, CircuitBreaker] | None = None) -> None:
        self.rungs = rungs
        self.cache = cache or {}
        self.breakers = breakers or {}

    def run(self, prompt: str) -> CascadeResult:
        attempts: list[str] = []
        total_cost = 0
        calls = 0
        for rung in self.rungs:
            if not rung.passes_gate():
                attempts.append(f"{rung.name}: skipped (failed evaluation gate "
                                f"{rung.eval_passed}/{rung.eval_total})")
                continue
            breaker = self.breakers.get(rung.name)
            try:
                if breaker is not None:
                    answer = breaker.call(rung.complete, prompt)
                else:
                    answer = rung.complete(prompt)
            except CircuitOpenError:
                attempts.append(f"{rung.name}: skipped (circuit breaker open)")
                continue
            except ProviderError as exc:
                calls += 1
                total_cost += rung.cost_units
                attempts.append(f"{rung.name}: failed with {exc.status}")
                if exc.status not in RETRYABLE_STATUSES:
                    # A 400 is your bug: surface it, do not cascade.
                    raise
                continue
            calls += 1
            total_cost += rung.cost_units
            attempts.append(f"{rung.name}: served")
            return CascadeResult(rung.name, answer, total_cost, calls, attempts)
        if prompt in self.cache:
            attempts.append("cache: served (stale answer, check freshness)")
            return CascadeResult("cache", self.cache[prompt], total_cost, calls, attempts)
        attempts.append("refusal: no rung could serve this request")
        return CascadeResult(
            "refusal",
            "I could not answer that right now: every model in the fallback "
            "chain failed. Please retry later, or ask a narrower question.",
            total_cost, calls, attempts,
        )


def _ladder(frontier_fails, cheap_fails, other_fails, cache=None, cheap_gate=5):
    return ModelCascade(
        [
            Rung("frontier", 10, list(frontier_fails)),
            Rung("cheaper-same-provider", 2, list(cheap_fails), eval_passed=cheap_gate),
            Rung("different-provider", 8, list(other_fails)),
        ],
        cache=cache or {"What is the refund policy?": "[cached] Refunds within 30 days."},
    )


def demo() -> None:
    scenarios = [
        ("frontier healthy", _ladder([], [], [])),
        ("frontier 503, cheaper serves", _ladder([503], [], [])),
        ("frontier + cheaper fail, other provider serves", _ladder([503], [503], [])),
        ("all models fail, cache serves", _ladder([503], [503], [503])),
        ("all models fail, no cache hit -> refusal", _ladder([503], [503], [503])),
    ]
    prompts = [
        "What is the refund policy?",
        "What is the refund policy?",
        "What is the refund policy?",
        "What is the refund policy?",
        "What is the capital of France?",
    ]
    print(f"{'scenario':45} {'served by':24} {'cost':>4} {'calls':>5}")
    for (label, cascade), prompt in zip(scenarios, prompts):
        result = cascade.run(prompt)
        print(f"{label:45} {result.served_by:24} {result.total_cost_units:>4} {result.calls_made:>5}")
        for attempt in result.attempts:
            print(f"    - {attempt}")

    # A rung that fails its evaluation gate is skipped, not served.
    gated = _ladder([503], [], [], cheap_gate=2)
    result = gated.run("What is the refund policy?")
    print(f"\ncheaper model fails its eval gate -> served by {result.served_by}, "
          f"cost {result.total_cost_units}")

    # A 400 surfaces immediately, with no fallback call made.
    buggy = _ladder([400], [], [])
    try:
        buggy.run("What is the refund policy?")
    except ProviderError as exc:
        print(f"frontier 400 -> raised immediately ({exc.status}); "
              f"cheaper calls made: {buggy.rungs[1].calls} (no cascade)")


if __name__ == "__main__":
    demo()
