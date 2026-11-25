"""Cascade router (Chapter 11, cost-aware routing).

Routing chooses the cheapest model that can do the job *before* the
task starts; the cascade in ``model_cascade.py`` steps *down* only
after a model has failed.  The two are complements, not synonyms:
routing saves money on the healthy path, cascading saves the request
on the failing path, and a request can be routed cheaply and still
cascade if the routed model fails.

This module implements a small cascade router over a *simulated*
model pool.  Each simulated model has a cost per 1M tokens, a
capability profile (which task types it may serve), and a scripted
quality score per task type -- the score stands in for the measured
pass rate you would get by running your own evaluation set against
each model, the way the Claude Cookbook cost notebook insists you do
before trading cost against quality
(https://platform.claude.com/cookbook/cost-optimization-cost-optimization).
The cheapest model only counts if it clears the quality bar for that
task type: a saving bought with a wrong answer is not a saving.

The simulated pool is the offline worked example: the routing
decision, the expected-cost arithmetic, the golden-set eval and the
shadow log are deterministic logic, and they are studied here
without spending money. The scripted quality scores are a deliberate
stand-in for measured pass rates -- they are labelled as such, and
the live tiers below are what replace the stand-in with a real
model: ``LiveTier`` calls the model for real, and
``answer_routed`` routes a task with the same router and then
answers it on the routed tier, reporting the provider's real token
counts and real dollar cost. The configured backend exposes two models -- the configured model
and the judge model (``LLM_JUDGE_MODEL``, which defaults to the
configured model) -- so the live pool has two tiers -- small (judge)
and frontier (configured) --
and a task the simulated pool would route to mid is served live by
the cheap tier, which the live demo says plainly when it happens.

Prices below are *illustrative worked-example prices*, not real
vendor prices -- frontier $15 in / $60 out, mid $3 in / $12 out,
small $0.50 in / $2 out per 1M tokens.  Replace them with your own
measured cost per task before using this in production.  Quality
scores are likewise scripted stand-ins, not measured results.

No API key is needed for the deterministic demo: every model in the
simulated pool is a worked example. The live section at the end of
the demo answers two routed tasks with the real model, and needs a configured model backend (see "Connect a model" in the root README -- key-based or AWS Bedrock); it is skipped with a
note when no backend is configured.
Run:  python cascade_router.py
Test: python test_cascade_router.py
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# The simulated model pool
# ---------------------------------------------------------------------------

TASK_TYPES = ("summarise", "extract", "format", "plan", "reason")


@dataclass
class SimulatedModel:
    """One model in the pool: price, capability, scripted quality."""

    name: str
    input_cost_per_1m: float   # illustrative dollars per 1M input tokens
    output_cost_per_1m: float  # illustrative dollars per 1M output tokens
    # Task types this model is allowed to attempt at all.
    capabilities: frozenset[str] = frozenset(TASK_TYPES)
    # Scripted quality score (0-1) per task type; stands in for a
    # measured pass rate on your evaluation set.
    quality: dict[str, float] = field(default_factory=dict)

    def cost_per_call(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_cost_per_1m
                + output_tokens * self.output_cost_per_1m) / 1_000_000

    def quality_for(self, task_type: str) -> float:
        return self.quality.get(task_type, 0.0)


def default_pool() -> list[SimulatedModel]:
    """Cheapest first.  Quality is scripted; see the module docstring."""
    return [
        SimulatedModel(
            "small", 0.50, 2.00,
            quality={"summarise": 0.92, "extract": 0.88, "format": 0.95,
                     "plan": 0.55, "reason": 0.60}),
        SimulatedModel(
            "mid", 3.00, 12.00,
            quality={"summarise": 0.95, "extract": 0.93, "format": 0.97,
                     "plan": 0.78, "reason": 0.82}),
        SimulatedModel(
            "frontier", 15.00, 60.00,
            quality={"summarise": 0.98, "extract": 0.97, "format": 0.99,
                     "plan": 0.97, "reason": 0.98}),
    ]


# ---------------------------------------------------------------------------
# Tasks, decisions, and the complexity scorer
# ---------------------------------------------------------------------------

@dataclass
class Task:
    prompt: str
    task_type: str  # one of TASK_TYPES
    input_tokens: int = 2000
    output_tokens: int = 500


@dataclass
class RoutingDecision:
    model: str
    reason: str
    complexity: float          # 0 (trivial) to 1 (hardest), from the scorer
    predicted_quality: float   # scripted quality of the chosen model
    estimated_cost: float      # dollars for this one call


# Words that, in a prompt, signal work a small model handles badly.
# This is a rule-based stand-in for a learned router: a real
# predictive router is a small classifier trained on labelled
# (task, cheapest model that passed) pairs, and this scorer is what
# you replace with it once you have the labels.
_HARD_MARKERS = ("multi-step", "trade-off", "tradeoff", "prove",
                 "design", "compare", "reason", "plan", "strategy",
                 "edge case", "ambiguous")
_BASE_COMPLEXITY = {"summarise": 0.15, "format": 0.10, "extract": 0.40,
                    "plan": 0.80, "reason": 0.85}


def complexity_score(task: Task) -> float:
    """Rule-based complexity scorer (stand-in for a learned router).

    Starts from the task type, then adds weight for hard markers in
    the prompt and for length: a long prompt with several hard
    markers is treated as harder than a short one of the same type.
    Returns a score in [0, 1].
    """
    score = _BASE_COMPLEXITY.get(task.task_type, 0.5)
    lowered = task.prompt.lower()
    for marker in _HARD_MARKERS:
        if marker in lowered:
            score += 0.12
    if len(task.prompt) > 400:
        score += 0.10
    elif len(task.prompt) > 150:
        score += 0.05
    return min(1.0, max(0.0, score))


def expected_cost(route_probabilities: dict[str, float],
                  models: dict[str, SimulatedModel],
                  input_tokens: int, output_tokens: int) -> float:
    """Expected cost per call: sum over models of P(route) x cost.

    ``route_probabilities`` maps a model name to the share of traffic
    routed to it; the shares must sum to 1.  This is the formula the
    lesson works through: the cheapest routing only counts if every
    model it routes to clears the quality bar, which this function
    does not check -- the caller must enforce the quality constraint
    before trusting the number.
    """
    total_share = sum(route_probabilities.values())
    if abs(total_share - 1.0) > 1e-9:
        raise ValueError(f"route shares must sum to 1, got {total_share}")
    return sum(share * models[name].cost_per_call(input_tokens, output_tokens)
               for name, share in route_probabilities.items())


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------

class CascadeRouter:
    """Routes each task (or each step) to the cheapest capable model.

    ``mode`` selects the routing strategy:
    - ``"predictive"``: the complexity scorer predicts how hard the
      task is, and the router picks the cheapest model whose scripted
      quality clears ``min_quality`` for the task type, biased by
      that score -- easy tasks go cheap, hard tasks go up.
    - ``"fixed"``: non-predictive rules keyed only on task type --
      summarise/format to small, extract to mid, plan/reason to
      frontier -- with no look at the individual prompt.
    - ``"always_small"`` / ``"always_frontier"``: degenerate baselines
      used by the eval harness (and by the golden-set gate test) to
      show what happens when routing ignores quality, or ignores cost.

    ``shadow=True`` turns on shadow scoring: the router logs the model
    it *would* have chosen, but the caller keeps using the enforced
    model (frontier by default).  Shadow mode never changes what the
    user gets; it only produces the log you need to judge a new
    router before you let it decide.
    """

    def __init__(self, models: list[SimulatedModel] | None = None,
                 mode: str = "predictive", min_quality: float = 0.85,
                 version: str = "v2-predictive") -> None:
        self.models = sorted(models or default_pool(),
                             key=lambda m: m.cost_per_call(2000, 500))
        self.by_name = {m.name: m for m in self.models}
        self.mode = mode
        self.min_quality = min_quality
        self.version = version
        self.shadow_log: list[dict] = []

    # -- the core decision -----------------------------------------------
    def route(self, task: Task) -> RoutingDecision:
        complexity = complexity_score(task)
        if self.mode == "always_small":
            chosen = self.by_name["small"]
            reason = "always_small baseline: cheapest, quality ignored"
        elif self.mode == "always_frontier":
            chosen = self.by_name["frontier"]
            reason = "always_frontier baseline: quality first, cost ignored"
        elif self.mode == "fixed":
            chosen = self._fixed_route(task)
            reason = f"fixed rule for task type {task.task_type!r}"
        else:  # predictive
            chosen = self._predictive_route(task, complexity)
            reason = (f"predictive: complexity {complexity:.2f}, cheapest "
                      f"model clearing quality >= {self.min_quality}")
        return RoutingDecision(
            model=chosen.name, reason=reason, complexity=complexity,
            predicted_quality=chosen.quality_for(task.task_type),
            estimated_cost=chosen.cost_per_call(task.input_tokens,
                                                task.output_tokens))

    def _cheapest_clearing(self, task: Task) -> SimulatedModel:
        """Cheapest model that may serve this type and clears the bar."""
        for model in self.models:  # cheapest first
            if (task.task_type in model.capabilities
                    and model.quality_for(task.task_type) >= self.min_quality):
                return model
        return self.by_name["frontier"]  # nobody clears it: go frontier

    def _predictive_route(self, task: Task, complexity: float) -> SimulatedModel:
        cheapest = self._cheapest_clearing(task)
        if complexity >= 0.70:
            # Hard task: do not trust the cheap model even if its
            # scripted score clears the bar -- route up to frontier.
            return self.by_name["frontier"]
        if complexity >= 0.40 and cheapest.name == "small":
            # Medium task: the small model is the cheapest that clears
            # the bar on paper, but the prompt looks harder than its
            # type alone suggests, so step up one rung.
            return self.by_name["mid"]
        return cheapest

    def _fixed_route(self, task: Task) -> SimulatedModel:
        table = {"summarise": "small", "format": "small",
                 "extract": "mid", "plan": "frontier", "reason": "frontier"}
        return self.by_name[table.get(task.task_type, "frontier")]

    # -- per-step routing for a multi-step agent run ----------------------
    def run_agent(self, steps: list[Task]) -> dict:
        """Route every step of an agent run independently.

        A planning step may need the frontier model while a formatting
        step in the same run does not; routing once for the whole run
        would pay frontier prices for both, or risk the plan on the
        small model.  Returns the per-step decisions and the total.
        """
        decisions = [self.route(step) for step in steps]
        return {
            "decisions": decisions,
            "total_cost": round(sum(d.estimated_cost for d in decisions), 4),
            "models_used": [d.model for d in decisions],
        }

    # -- shadow scoring ----------------------------------------------------
    def shadow_route(self, task: Task,
                     enforced_model: str = "frontier") -> RoutingDecision:
        """Log what the router *would* choose, without enforcing it.

        Returns a decision for the enforced model (what the user
        actually gets); the router's own choice is recorded in
        ``shadow_log`` only.  Compare the two columns afterwards:
        where they disagree and the enforced answer was no better,
        the router was right and can be promoted.
        """
        would = self.route(task)
        enforced = self.by_name[enforced_model]
        self.shadow_log.append({
            "prompt": task.prompt,
            "task_type": task.task_type,
            "would_choose": would.model,
            "would_cost": round(would.estimated_cost, 4),
            "enforced": enforced_model,
            "enforced_cost": round(
                enforced.cost_per_call(task.input_tokens,
                                       task.output_tokens), 4),
            "router_version": self.version,
        })
        return RoutingDecision(
            model=enforced_model,
            reason=f"shadow mode: router would choose {would.model}, "
                   f"enforced model {enforced_model} served instead",
            complexity=would.complexity,
            predicted_quality=enforced.quality_for(task.task_type),
            estimated_cost=enforced.cost_per_call(task.input_tokens,
                                                  task.output_tokens))


# ---------------------------------------------------------------------------
# Eval harness: golden set, replay matrix, quality gate
# ---------------------------------------------------------------------------

@dataclass
class GoldenTask(Task):
    """A task with a known minimum quality: the bar the router must clear."""
    min_quality: float = 0.85


@dataclass
class EvalReport:
    router_version: str
    tasks: int
    passed: int
    pass_rate: float
    avg_cost_per_task: float
    expected_cost_per_task: float
    failures: list[str]  # prompts that missed their quality bar


def evaluate_router(router: CascadeRouter,
                    golden_set: list[GoldenTask]) -> EvalReport:
    """Run a router over the golden set and grade it on quality and cost."""
    failures: list[str] = []
    total_cost = 0.0
    for task in golden_set:
        decision = router.route(task)
        total_cost += decision.estimated_cost
        if decision.predicted_quality < task.min_quality:
            failures.append(
                f"{task.prompt!r} ({task.task_type}): routed to "
                f"{decision.model} with quality "
                f"{decision.predicted_quality:.2f} < {task.min_quality}")
    passed = len(golden_set) - len(failures)
    n = max(1, len(golden_set))
    return EvalReport(
        router_version=router.version, tasks=len(golden_set), passed=passed,
        pass_rate=passed / n,
        avg_cost_per_task=round(total_cost / n, 4),
        expected_cost_per_task=round(total_cost / n, 4),
        failures=failures)


def replay_matrix(routers: list[CascadeRouter],
                  golden_set: list[GoldenTask]) -> list[EvalReport]:
    """Replay the same golden set across router versions, side by side.

    The point of the matrix is that a new router version is judged on
    the *same* tasks as the old one: if v2 is cheaper but fails a
    task v1 passed, the matrix shows it before users do.
    """
    return [evaluate_router(router, golden_set) for router in routers]


def default_golden_set() -> list[GoldenTask]:
    return [
        GoldenTask("Summarise this refund policy in two sentences.",
                   "summarise"),
        GoldenTask("Format this address list as a table.", "format"),
        GoldenTask("Extract the order number and date from this email.",
                   "extract"),
        GoldenTask("Summarise this 40-page contract.", "summarise"),
        GoldenTask("Design a multi-step migration plan with trade-off "
                   "analysis and edge cases for the billing service.",
                   "plan"),
        GoldenTask("Prove which of these two pricing models is cheaper "
                   "and compare the ambiguous cases.", "reason"),
    ]


# ---------------------------------------------------------------------------
# Live tiers: the same routing decision, answered by the real model
# ---------------------------------------------------------------------------

@dataclass
class LiveTier:
    """One tier of the live pool: a real model route, not a simulation.

    ``name`` is the tier name in the simulated pool (``"small"`` or
    ``"frontier"``); ``model_id`` is the route on the configured
    backend. The cheap tier is the judge model, the frontier tier is
    the configured model. ``complete``
    returns the model's real answer plus the provider-reported
    token counts and dollar cost for that call, read off the
    client's usage totals. ``max_tokens`` is generous (1500) because
    this model spends a large share of a small budget on hidden
    reasoning and can return empty content if the budget is too
    tight.
    """

    name: str
    model_id: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    def complete(self, client, prompt: str) -> tuple[str, int, int, float]:
        self.calls += 1
        before_in = client.total_prompt_tokens
        before_out = client.total_completion_tokens
        before_cost = client.total_cost_usd
        answer = client.complete(
            [{"role": "user", "content": prompt}],
            model=self.model_id, max_tokens=1500,
        )
        in_tokens = client.total_prompt_tokens - before_in
        out_tokens = client.total_completion_tokens - before_out
        cost = client.total_cost_usd - before_cost
        self.prompt_tokens += in_tokens
        self.completion_tokens += out_tokens
        self.cost_usd += cost
        return answer, in_tokens, out_tokens, cost


def live_tiers(client) -> dict[str, LiveTier]:
    """The live pool: two real tiers, keyed by simulated-pool name."""
    return {
        "small": LiveTier("small", client.judge_model),
        "frontier": LiveTier("frontier", client.model),
    }


# The simulated pool has a mid tier the configured backend has no
# separate model for, so
# a live answer routed to mid is served by the cheap tier instead.
# That collapse is the honest mapping on this backend, and it is
# reported in the result rather than hidden.
_LIVE_TIER_FOR_ROUTE = {"small": "small", "mid": "small",
                        "frontier": "frontier"}


def answer_routed(task: Task, client, router: CascadeRouter | None = None,
                  tiers: dict[str, LiveTier] | None = None) -> dict:
    """Route a task with the deterministic router, then answer it for real.

    The routing decision is the same one the simulated pool makes --
    complexity score, quality bar, cheapest tier that clears it --
    and the answer comes from the real model on the routed tier.
    Returns the decision, the tier that actually served it, the
    answer text, and the provider-reported tokens and dollars.
    """
    router = router or CascadeRouter(mode="predictive")
    tiers = tiers or live_tiers(client)
    decision = router.route(task)
    tier_name = _LIVE_TIER_FOR_ROUTE[decision.model]
    tier = tiers[tier_name]
    answer, in_tokens, out_tokens, cost = tier.complete(client, task.prompt)
    return {
        "routed_model": decision.model,
        "served_tier": tier_name,
        "served_model_id": tier.model_id,
        "collapsed_mid_to_small": decision.model == "mid",
        "complexity": round(decision.complexity, 2),
        "answer": answer,
        "prompt_tokens": in_tokens,
        "completion_tokens": out_tokens,
        "cost_usd": cost,
    }


def live_demo() -> None:
    """Answer two routed tasks with the real model: one easy, one hard.

    The easy summarisation routes to the cheap tier, the hard
    planning task routes to the frontier tier, and each answer is
    real, with real token counts and real dollars. Two live calls.
    """
    from llm_client import LLMClient

    client = LLMClient()
    tiers = live_tiers(client)
    for task in [
        Task("Summarise this refund policy in two sentences: refunds "
             "are accepted within 30 days of purchase with a receipt.",
             "summarise"),
        Task("Design a multi-step migration plan with trade-off analysis "
             "and edge cases for the billing service, in three short "
             "bullet points.", "plan"),
    ]:
        result = answer_routed(task, client, tiers=tiers)
        note = (" (mid has no live tier; served by the cheap tier)"
                if result["collapsed_mid_to_small"] else "")
        print(f"live: {task.task_type} task routed to "
              f"{result['routed_model']} -> served by "
              f"{result['served_tier']} ({result['served_model_id']}){note}")
        print(f"live: answer: {result['answer'].strip()[:200]!r}")
        print(f"live: {result['prompt_tokens']} prompt + "
              f"{result['completion_tokens']} completion tokens, "
              f"cost ${result['cost_usd']:.4f}")
    print(f"live: client totals: {client.total_prompt_tokens} prompt + "
          f"{client.total_completion_tokens} completion tokens, "
          f"${client.total_cost_usd:.4f}")


def demo() -> None:
    pool = default_pool()
    by_name = {m.name: m for m in pool}
    router = CascadeRouter(pool, mode="predictive", version="v2-predictive")
    always_frontier = CascadeRouter(pool, mode="always_frontier",
                                    version="always-frontier")

    # Worked expected-cost example (same traffic mix as the lesson):
    # 60% easy, 30% medium, 10% hard, 2,000 in / 500 out tokens a call.
    routed = expected_cost({"small": 0.6, "mid": 0.3, "frontier": 0.1},
                           by_name, 2000, 500)
    frontier_only = expected_cost({"frontier": 1.0}, by_name, 2000, 500)
    print(f"expected cost per call: routed ${routed:.4f} vs "
          f"always-frontier ${frontier_only:.4f} "
          f"({(1 - routed / frontier_only):.0%} cheaper, worked example)")

    # Golden set: routing saves cost *while clearing the quality bar*.
    golden = default_golden_set()
    for report in replay_matrix([always_frontier, router], golden):
        print(f"{report.router_version:16} pass {report.passed}/{report.tasks} "
              f"({report.pass_rate:.0%})  avg cost "
              f"${report.avg_cost_per_task:.4f}/task")
        for failure in report.failures:
            print(f"    FAIL: {failure}")

    # Per-step routing: one run, three steps, three different models.
    run = router.run_agent([
        Task("Design a multi-step migration plan with trade-off analysis.",
             "plan"),
        Task("Extract the order number and date from this email.", "extract"),
        Task("Format this address list as a table.", "format"),
    ])
    print("\nper-step routing for one agent run:")
    for step, decision in zip(
            ["plan the migration", "extract order details", "format output"],
            run["decisions"]):
        print(f"  {step:22} -> {decision.model:9} "
              f"(${decision.estimated_cost:.4f}, quality "
              f"{decision.predicted_quality:.2f})")
    print(f"  total ${run['total_cost']:.4f} vs "
          f"${3 * by_name['frontier'].cost_per_call(2000, 500):.4f} "
          f"if every step had used the frontier model")

    # Routing up: the hard planning task goes to the frontier model.
    hard = Task("Design a multi-step migration plan with trade-off analysis "
                "and edge cases for the billing service.", "plan")
    print(f"\nhard task routed to: {router.route(hard).model} "
          f"(complexity {complexity_score(hard):.2f})")

    # Shadow scoring: the log records the choice; frontier still serves.
    shadow = CascadeRouter(pool, mode="predictive", version="v2-predictive")
    served = shadow.shadow_route(
        Task("Summarise this refund policy in two sentences.", "summarise"))
    print(f"shadow mode: served by {served.model}, router would choose "
          f"{shadow.shadow_log[0]['would_choose']}")


if __name__ == "__main__":
    demo()
    print("\n--- live check: routed tasks answered by the real model ---")
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
