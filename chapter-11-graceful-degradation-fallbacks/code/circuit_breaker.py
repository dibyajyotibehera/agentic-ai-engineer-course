"""Circuit breaker (Chapter 11, Exercise 1).

A small, standard-library-only circuit breaker for a model provider or
any other remote dependency.  It follows the pattern described in the circuit-breaker write-up
(https://martinfowler.com/bliki/CircuitBreaker.html): three states -- closed,
open, half-open -- with a failure threshold, a reset timeout, and a
trial call that decides whether the dependency has recovered.

Only *retryable* failures trip the breaker.  The retryable set follows
the rule in the LLMOps.si deployment article
(https://www.llmops.si/articles/llm-deployment-ci-cd-staging-rollback/):
429, 500, 502, 503 and 504.  A 400 is your bug, not a retry, so it
never counts towards the threshold; neither do authentication
failures or validation errors from your own code.

Thresholds here are *chosen defaults for the exercise* (5 failures,
30 second reset timeout), not measured results.  In production,
derive them from your own traffic and latency budget.

No API key or paid service is needed: the demo drives the breaker
with a mock provider and a fake clock, so no real waiting happens.
Run:  python circuit_breaker.py
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

# Statuses a fallback chain may retry, per the LLMOps.si rule.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class ProviderError(Exception):
    """An error from a provider, carrying its HTTP status code."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"provider error {status}")
        self.status = status


class CircuitOpenError(Exception):
    """Raised when the breaker is open and the call is failed fast."""


@dataclass
class Transition:
    """One logged state transition, with its cause."""

    from_state: CircuitState
    to_state: CircuitState
    cause: str
    at: float


class FakeClock:
    """A clock you can advance by hand, so tests never wait in real time."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CircuitBreaker:
    """Wraps one protected dependency.

    Args:
        failure_threshold: retryable failures in the closed state that
            trip the breaker.
        reset_timeout_s: seconds the breaker stays open before a trial
            call is allowed (the half-open state).
        successes_to_close: consecutive successful trial calls needed
            to close the breaker again.  Defaults to 1.
        clock: a callable returning the current time in seconds; pass a
            FakeClock in tests, defaults to time.monotonic.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout_s: float = 30.0,
        successes_to_close: int = 1,
        clock=None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self.successes_to_close = successes_to_close
        self._clock = clock or time.monotonic
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_successes = 0
        self.transitions: list[Transition] = []

    @property
    def state(self) -> CircuitState:
        if self._opened_at is None:
            return CircuitState.CLOSED
        if self._clock() - self._opened_at >= self.reset_timeout_s:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def _record(self, from_state: CircuitState, to_state: CircuitState, cause: str) -> None:
        self.transitions.append(Transition(from_state, to_state, cause, self._clock()))

    def call(self, func, *args, **kwargs):
        """Call ``func`` through the breaker, failing fast while open."""
        state = self.state
        if state is CircuitState.OPEN:
            raise CircuitOpenError(
                f"circuit open: failing fast without calling the provider "
                f"(opened {self._clock() - self._opened_at:.1f}s ago)"
            )
        try:
            result = func(*args, **kwargs)
        except ProviderError as exc:
            if exc.status in RETRYABLE_STATUSES:
                self._record_failure(f"retryable provider error {exc.status}")
            # Non-retryable errors (400, 401, ...) are the caller's bug:
            # they are raised without touching the failure count.
            raise
        except (TimeoutError, ConnectionError) as exc:
            self._record_failure(f"{type(exc).__name__}: {exc}")
            raise
        # Success resets the count in the closed state, and counts as a
        # successful probe in the half-open state.
        if state is CircuitState.HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self.successes_to_close:
                self._record(CircuitState.HALF_OPEN, CircuitState.CLOSED,
                             "trial call succeeded")
                self._failures = 0
                self._opened_at = None
                self._half_open_successes = 0
        else:
            self._failures = 0
        return result

    def _record_failure(self, cause: str) -> None:
        previous = self.state
        if previous is CircuitState.HALF_OPEN:
            # A failed probe reopens the breaker and restarts the timeout.
            self._record(previous, CircuitState.OPEN,
                         f"trial call failed ({cause})")
            self._opened_at = self._clock()
            self._half_open_successes = 0
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._record(previous, CircuitState.OPEN,
                         f"{self._failures} retryable failures ({cause})")
            self._opened_at = self._clock()


class FlakyProvider:
    """A mock provider that fails a fixed number of times, then recovers.

    ``failures`` is a list of ProviderError instances to raise in order;
    once exhausted, calls succeed.  ``calls`` counts every invocation,
    which is how the demo proves the breaker stops calling it.
    """

    def __init__(self, failures: list[ProviderError]) -> None:
        self._failures = list(failures)
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        if self._failures:
            raise self._failures.pop(0)
        return f"answer to: {prompt}"


def demo() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=5, reset_timeout_s=30.0, clock=clock)
    provider = FlakyProvider([ProviderError(503, "service unavailable")] * 5)

    for i in range(5):
        try:
            breaker.call(provider.complete, "hello")
        except ProviderError as exc:
            print(f"call {i + 1}: provider failed ({exc.status}), state={breaker.state.value}")
    print(f"after 5 failures: state={breaker.state.value}, provider calls={provider.calls}")

    # While open, the call fails fast and the provider is never invoked.
    try:
        breaker.call(provider.complete, "hello")
    except CircuitOpenError as exc:
        print(f"while open: {exc} (provider calls still {provider.calls})")

    # A 400 must not trip the breaker: prove it on a fresh breaker.
    fresh = CircuitBreaker(failure_threshold=5, clock=clock)
    bad = FlakyProvider([ProviderError(400, "bad request")] * 10)
    for _ in range(3):
        try:
            fresh.call(bad.complete, "hello")
        except ProviderError:
            pass
    print(f"after 3x 400: state={fresh.state.value} (a 400 is your bug, not a trip)")

    # Advance past the reset timeout: the breaker is half-open, a trial
    # succeeds, and it closes again.
    clock.advance(31)
    print(f"after 31s: state={breaker.state.value}")
    print("trial call:", breaker.call(provider.complete, "hello"))
    print(f"after recovery: state={breaker.state.value}")
    for t in breaker.transitions:
        print(f"  transition {t.from_state.value} -> {t.to_state.value}: {t.cause}")


if __name__ == "__main__":
    demo()
