"""Cost kill-switch (Chapter 11, Exercise 3).

A metered agent loop that halts safely when a budget is exceeded.
The meter counts *every* call the run makes -- retries, fallback
calls and sub-agent calls included -- and converts token counts to
cost from a price table you supply, the way the Claude Cookbook cost
optimisation notebook does with its usage_cost() function
(https://platform.claude.com/cookbook/cost-optimization-cost-optimization).

Budgets are abstract cost units, not real money, and the numbers
here (per-run 100, per-user 500, global 2,000) are chosen defaults
for the exercise.  In production, derive the per-run budget from
your measured cost per task.

No API key is needed for the deterministic demo: the meter and the
budget are driven by a model that returns a fixed token count per
call, which is deliberate -- a kill-switch must be testable without
spending real money, so the meter and budget are separable from the
provider by design, and the fixed-count model is the injectable
stand-in the lesson prescribes for that test. The live check at the
end of this file (``live_demo``) instead drives the same meter with
real token counts from real model calls, and needs a configured model backend (see "Connect a model" in the root README -- key-based or AWS Bedrock); it is skipped with a note when no
backend is configured.
Run:  python cost_kill_switch.py
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PriceTable:
    """Cost units per 1,000 tokens.  Abstract units, not real prices."""

    input_per_1k: float = 1.0
    output_per_1k: float = 4.0

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens / 1000) * self.input_per_1k + (
            output_tokens / 1000
        ) * self.output_per_1k


@dataclass
class CostMeter:
    """Running tally of what a run has spent, in cost units."""

    prices: PriceTable = field(default_factory=PriceTable)
    spent: float = 0.0
    calls: int = 0

    def record(self, input_tokens: int, output_tokens: int) -> float:
        cost = self.prices.cost(input_tokens, output_tokens)
        self.spent += cost
        self.calls += 1
        return cost

    def estimate_next_call(self, input_tokens: int, max_output_tokens: int) -> float:
        """Pre-flight estimate for the next call, before it is made."""
        return self.prices.cost(input_tokens, max_output_tokens)


class BudgetExceeded(Exception):
    """Raised when a hard budget is breached; carries the meter state."""

    def __init__(self, scope: str, spent: float, budget: float) -> None:
        super().__init__(f"{scope} budget exceeded: spent {spent:.1f} of {budget:.1f}")
        self.scope = scope
        self.spent = spent
        self.budget = budget


@dataclass
class Budgets:
    """Per-run, per-user and global budgets, each with a warning level."""

    per_run: float = 100.0
    per_user: float = 500.0
    global_: float = 2000.0
    warn_fraction: float = 0.8
    warned: set[str] = field(default_factory=set)

    def check(self, meter: CostMeter, user_spent: float, global_spent: float,
              log: list[str]) -> None:
        """Warn at 80%, raise at 100% -- for every scope."""
        for scope, spent, budget in (
            ("run", meter.spent, self.per_run),
            ("user", user_spent, self.per_user),
            ("global", global_spent, self.global_),
        ):
            if spent >= budget:
                raise BudgetExceeded(scope, spent, budget)
            if spent >= budget * self.warn_fraction and scope not in self.warned:
                self.warned.add(scope)
                log.append(
                    f"WARNING: {scope} budget {spent:.1f}/{budget:.1f} "
                    f"({spent / budget:.0%}) -- approaching the hard stop"
                )


class MockModel:
    """Returns a fixed token count per call, so spend is predictable.

    Deliberate: the kill-switch test needs a model whose spend is
    known in advance, so the loop can be driven to its hard stop
    without spending real money. The same loop driven by real token
    counts lives in ``LiveModel`` below.
    """

    def __init__(self, input_tokens: int = 500, output_tokens: int = 500) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.calls = 0

    def complete(self, prompt: str) -> tuple[str, int, int]:
        self.calls += 1
        return f"step result for: {prompt}", self.input_tokens, self.output_tokens


class LiveModel:
    """A real model behind the same ``complete`` interface as ``MockModel``.

    Each call is a real call on the judge model, and the
    token counts returned are the provider-reported counts for that
    call, read off the client's usage totals -- so the meter records
    real spend, and the kill-switch trips on real money, not on a
    scripted count. ``input_tokens`` is only a rough pre-flight
    estimate (a few hundred tokens for a short step); the post-call
    record always uses the real counts. ``max_tokens`` is generous
    (1500) because this model spends a large share of a small budget
    on hidden reasoning and can return empty content if the budget
    is too tight.
    """

    def __init__(self, client, input_tokens: int = 300) -> None:
        self.client = client
        self.input_tokens = input_tokens
        self.output_tokens = 1500
        self.calls = 0

    def complete(self, prompt: str) -> tuple[str, int, int]:
        self.calls += 1
        before_in = self.client.total_prompt_tokens
        before_out = self.client.total_completion_tokens
        answer = self.client.complete(
            [{"role": "user", "content": prompt}],
            model=self.client.judge_model, max_tokens=1500,
        )
        in_tokens = self.client.total_prompt_tokens - before_in
        out_tokens = self.client.total_completion_tokens - before_out
        return answer, in_tokens, out_tokens


@dataclass
class MeteredLoop:
    """An agent loop that stops mid-run when a budget is exceeded.

    The loop checks the budget *before* each call (pre-flight estimate)
    as well as after it, so one oversized call cannot blow the budget
    on its own.  On halt it writes a run log of completed steps, the
    refused step, and the cost at halt -- and says plainly that the
    stop was about cost, not about the user's request.
    """

    model: MockModel | LiveModel
    budgets: Budgets
    max_output_tokens: int = 500
    meter: CostMeter = field(default_factory=CostMeter)
    user_spent: float = 0.0
    global_spent: float = 0.0
    log: list[str] = field(default_factory=list)

    def run(self, steps: list[str]) -> dict:
        completed: list[str] = []
        refused_reason: str | None = None
        for step in steps:
            # Pre-flight: refuse the step if its estimate alone breaches.
            estimate = self.meter.estimate_next_call(
                self.model.input_tokens, self.max_output_tokens)
            if self.meter.spent + estimate > self.budgets.per_run:
                refused_reason = (
                    f"estimated cost {estimate:.1f} for step {step!r} would "
                    f"breach the per-run budget "
                    f"({self.meter.spent:.1f}/{self.budgets.per_run:.1f})")
                self.log.append(f"REFUSED step {step!r}: {refused_reason}")
                break
            answer, in_tokens, out_tokens = self.model.complete(step)
            cost = self.meter.record(in_tokens, out_tokens)
            self.user_spent += cost
            self.global_spent += cost
            completed.append(step)
            self.log.append(f"step {step!r}: cost {cost:.1f}, "
                            f"run total {self.meter.spent:.1f}")
            try:
                self.budgets.check(self.meter, self.user_spent,
                                   self.global_spent, self.log)
            except BudgetExceeded as exc:
                self.log.append(f"HALTED: {exc}")
                return self._report(completed, halted=True, reason=str(exc))
        if refused_reason is not None:
            return self._report(completed, halted=True,
                                reason=f"pre-flight refusal: {refused_reason}")
        return self._report(completed, halted=False, reason="all steps completed")

    def _report(self, completed: list[str], halted: bool, reason: str) -> dict:
        return {
            "halted": halted,
            "reason": reason,
            "completed_steps": completed,
            "model_calls": self.meter.calls,
            "spent": round(self.meter.spent, 1),
            "user_message": (
                "I stopped this run because it reached its cost budget, "
                "not because there was anything wrong with your request. "
                f"{len(completed)} steps completed; the rest were not started."
                if halted else "Run completed within budget."
            ),
        }


def live_demo() -> None:
    """Drive the same metered loop with real token counts: two short steps.

    The meter records what the provider actually reported for each
    call, and the provider-reported dollar cost is printed alongside
    the abstract-unit tally, so the two accountings can be compared.
    Two live calls on the judge model.
    """
    from llm_client import LLMClient

    client = LLMClient()
    loop = MeteredLoop(model=LiveModel(client), budgets=Budgets(per_run=100.0))
    report = loop.run([
        "Summarise in one sentence: the refund policy is 30 days.",
        "Extract the order number from: order 4821 shipped Tuesday.",
    ])
    for line in loop.log:
        print(line)
    print(f"live: halted={report['halted']}, model_calls={report['model_calls']}, "
          f"spent={report['spent']} abstract units, "
          f"real cost=${client.total_cost_usd:.4f}, "
          f"real tokens={client.total_prompt_tokens} prompt + "
          f"{client.total_completion_tokens} completion")


def demo() -> None:
    # Each call costs 0.5 * 1.0 + 0.5 * 4.0 = 2.5 units; a 100-unit
    # budget allows 40 calls, so a 60-step task must halt mid-run.
    loop = MeteredLoop(model=MockModel(), budgets=Budgets(per_run=100.0))
    report = loop.run([f"research step {i}" for i in range(60)])
    for line in loop.log:
        print(line)
    print(f"\nhalted={report['halted']}, model_calls={report['model_calls']}, "
          f"spent={report['spent']}")
    print("user message:", report["user_message"])

    # Pre-flight check: one oversized call that the post-call check
    # alone would have let through.  Its *estimated* cost (200,000
    # input tokens at the capped output) already breaches the budget,
    # so the call is refused before it is ever made.
    big = MeteredLoop(model=MockModel(input_tokens=200_000, output_tokens=20_000),
                      budgets=Budgets(per_run=100.0))
    report = big.run(["one huge step"])
    print(f"\noversized call: halted={report['halted']}, "
          f"model_calls={big.model.calls} (0 means the call was never made)")


if __name__ == "__main__":
    demo()
    print("\n--- live check: the same meter driven by real token counts ---")
    try:
        from llm_client import LLMClient

        if LLMClient.has_backend():
            live_demo()
        else:
            print("live check skipped: no model backend configured "
                  "(connect a model: LLM_API_KEY, LLM_BASE_URL and LLM_MODEL, "
                  "or LLM_BACKEND=bedrock with LLM_BEDROCK_MODEL_ID)")
    except Exception as exc:  # noqa: BLE001 - the live check must not hide the demo
        print(f"live check failed: {exc}")
