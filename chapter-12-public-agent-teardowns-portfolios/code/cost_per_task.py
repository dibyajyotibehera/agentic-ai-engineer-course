"""Cost-per-task calculator for the support copilot (Chapter 12).

The calculator prices one agent task — here, one support ticket — across the
four lines a teardown must account for:

1. Model cost: input and output tokens of every model call in the run.
2. Tool cost: paid application programming interface (API) calls, per call.
3. Memory cost: tokens spent carrying context (the three-tier memory system
   from Chapter 4: working, episodic, and semantic memory).
4. Evaluation cost: the price of grading the task at all.

Everything is standard library only. Token counts come from your own traces;
pass them in as a TaskTrace. The ``main()`` demo runs on a mock trace whose
token counts are ILLUSTRATIVE placeholders — replace them with measured
numbers before publishing a teardown.

Two reference figures are printed by the demo, exactly as verified for this
chapter (see README.md and references.md). They are constants, not computed
values, because they come from published studies and documentation:

- Judge economics (LangChain, "Can Jev Be a Better Agent Evaluator?",
  20 September 2026): Jev matched a human reviewer's pass-or-fail labels on
  all 500 repeated decisions across five captured agent runs at $0.34 total,
  while Claude Sonnet 4.6 as judge matched 80.0% and cost $28.17 — about
  83 times more for a less reliable verdict. The study is narrow (five
  cases, one weather agent, language-model judges at provider defaults);
  say so whenever you cite it.
- Computer-use fallback (Anthropic documentation): declaring the
  computer-use toolset adds about 4,500 input tokens per request and each
  screenshot costs roughly 1,000-1,800 input tokens; a 30-step task comes to
  about 783,000 input tokens, about $1.61 on Claude Sonnet 5.5 at list
  prices.

Usage:
    python cost_per_task.py
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Verified reference figures (see module docstring for sources and caveats).
# Do not edit these: they are published numbers, quoted exactly.
# ---------------------------------------------------------------------------

# LangChain Jev-as-a-judge study, 500 decisions (5 cases x 100 repetitions).
JEV_JUDGE_TOTAL_COST_USD = 0.34
JEV_JUDGE_AGREEMENT = 1.00  # 500 of 500 pass/fail decisions matched the human
CLAUDE_JUDGE_TOTAL_COST_USD = 28.17
CLAUDE_JUDGE_AGREEMENT = 0.80  # Claude Sonnet 4.6 as judge
JUDGE_STUDY_DECISIONS = 500

# Anthropic computer-use documentation.
COMPUTER_USE_TOOLSET_OVERHEAD_TOKENS = 4_500  # input tokens per request
SCREENSHOT_TOKENS_LOW = 1_000
SCREENSHOT_TOKENS_HIGH = 1_800
COMPUTER_USE_30_STEP_INPUT_TOKENS = 783_000
COMPUTER_USE_30_STEP_COST_USD = 1.61  # Claude Sonnet 5.5, list prices


@dataclass
class ModelPricing:
    """Per-token prices for one model, in US dollars per million tokens."""

    name: str
    input_per_million: float
    output_per_million: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        """Return the cost of one call with the given token counts."""
        return (
            input_tokens * self.input_per_million / 1_000_000
            + output_tokens * self.output_per_million / 1_000_000
        )


@dataclass
class ToolCall:
    """One tool invocation inside a task."""

    name: str
    calls: int = 1
    cost_per_call_usd: float = 0.0  # 0.0 for internal tools; set for paid APIs


@dataclass
class TaskTrace:
    """A measured (or mock) record of one agent task.

    Token counts are per task. ``eval_input_tokens`` / ``eval_output_tokens``
    are the tokens spent grading the task — the judge is a line item, not
    free. ``memory_tokens`` is the context carried on the run (working memory
    re-sent on every call, plus retrieved episodic and semantic memory).
    """

    task_id: str
    model_calls: list[tuple[int, int]] = field(default_factory=list)
    tools: list[ToolCall] = field(default_factory=list)
    memory_tokens: int = 0
    eval_input_tokens: int = 0
    eval_output_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        return sum(i for i, _ in self.model_calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(o for _, o in self.model_calls)


def cost_of_trace(trace: TaskTrace, pricing: ModelPricing) -> dict[str, float]:
    """Price one task across the four lines. Returns dollars per line."""
    model = sum(pricing.cost(i, o) for i, o in trace.model_calls)
    tools = sum(t.calls * t.cost_per_call_usd for t in trace.tools)
    # Memory tokens are input tokens on the model calls that carry them, so
    # they are priced at the input rate; they are reported separately because
    # a teardown reader needs to see what memory costs, not just what the
    # model costs.
    memory = trace.memory_tokens * pricing.input_per_million / 1_000_000
    evaluation = pricing.cost(trace.eval_input_tokens, trace.eval_output_tokens)
    return {
        "model": model,
        "tools": tools,
        "memory": memory,
        "evaluation": evaluation,
        "total": model + tools + memory + evaluation,
    }


def summarise(traces: list[TaskTrace], pricing: ModelPricing) -> dict[str, float]:
    """Aggregate per-task costs over many traces: mean and worst task."""
    if not traces:
        raise ValueError("Need at least one trace to summarise.")
    totals = [cost_of_trace(t, pricing) for t in traces]
    n = len(totals)
    mean = {k: sum(d[k] for d in totals) / n for k in totals[0]}
    worst = max(totals, key=lambda d: d["total"])
    return {"n": float(n), **{f"mean_{k}": v for k, v in mean.items()},
            "worst_total": worst["total"]}


def judge_cost_comparison() -> str:
    """Render the verified judge-economics figure with its caveat."""
    ratio = CLAUDE_JUDGE_TOTAL_COST_USD / JEV_JUDGE_TOTAL_COST_USD
    return (
        f"Judge economics (LangChain, Sep 2026; {JUDGE_STUDY_DECISIONS} decisions):\n"
        f"  Jev:              ${JEV_JUDGE_TOTAL_COST_USD:.2f} total, "
        f"{JEV_JUDGE_AGREEMENT:.0%} agreement with human labels\n"
        f"  Claude Sonnet 4.6: ${CLAUDE_JUDGE_TOTAL_COST_USD:.2f} total, "
        f"{CLAUDE_JUDGE_AGREEMENT:.0%} agreement with human labels\n"
        f"  Claude judge costs about {ratio:.0f}x Jev for a less reliable verdict.\n"
        f"  Caveat: five cases from a single weather agent; language-model\n"
        f"  judges ran at provider defaults. Narrow study — cite it as such."
    )


def computer_use_reference() -> str:
    """Render the verified computer-use cost figure."""
    return (
        "Computer-use fallback (Anthropic documentation):\n"
        f"  Toolset overhead: about {COMPUTER_USE_TOOLSET_OVERHEAD_TOKENS:,} input "
        "tokens per request\n"
        f"  Each screenshot: roughly {SCREENSHOT_TOKENS_LOW:,}-"
        f"{SCREENSHOT_TOKENS_HIGH:,} input tokens\n"
        f"  30-step task: about {COMPUTER_USE_30_STEP_INPUT_TOKENS:,} input tokens, "
        f"about ${COMPUTER_USE_30_STEP_COST_USD:.2f} on Claude Sonnet 5.5 "
        "(list prices)\n"
        "  A screen-driven fallback needs a budget and a kill-switch."
    )


# ---------------------------------------------------------------------------
# Attribution rollup (cost observability).
#
# The four-line calculator above prices ONE task. A bill, however, is priced
# per conversation, per user or team, and per product — so each step's cost
# must be attributed to its task, and tasks rolled up to the level the
# business thinks in. These functions add that rollup without changing the
# four-line calculator: ``cost_of_trace`` is untouched.
#
# The hierarchy is per-call -> per-task -> per-conversation -> per-owner.
# The tags (conversation id, owner, product) must be attached when the cost
# is recorded; they cannot be reconstructed after spans are aggregated
# without them, which is why attribution is decided before the bill arrives.
# ---------------------------------------------------------------------------

@dataclass
class AttributedStep:
    """One priced step, attributed to a task and a conversation."""

    step: str  # e.g. "model call 1", "tool: knowledge_base_search", "memory"
    line: str  # one of "model", "tools", "memory", "evaluation"
    cost_usd: float


@dataclass
class AttributedTask:
    """A task's four-line costs, with the tags the rollup needs."""

    task_id: str
    conversation_id: str
    owner: str  # user or team the cost belongs to
    product: str
    lines: dict[str, float]

    @property
    def total(self) -> float:
        return self.lines["total"]


def attribute_steps(trace: TaskTrace, pricing: ModelPricing) -> list[AttributedStep]:
    """Break one task's cost into attributed per-step lines.

    Model cost is split per model call (each call is a priced span); tool
    cost is split per tool; memory and evaluation are each one line, since
    they are carried or graded once per task. The per-step costs sum to the
    same total ``cost_of_trace`` returns for the same trace.
    """
    steps: list[AttributedStep] = []
    for i, (input_tokens, output_tokens) in enumerate(trace.model_calls, start=1):
        steps.append(AttributedStep(
            step=f"model call {i}",
            line="model",
            cost_usd=pricing.cost(input_tokens, output_tokens),
        ))
    for tool in trace.tools:
        steps.append(AttributedStep(
            step=f"tool: {tool.name}",
            line="tools",
            cost_usd=tool.calls * tool.cost_per_call_usd,
        ))
    steps.append(AttributedStep(
        step="memory (context carried)",
        line="memory",
        cost_usd=trace.memory_tokens * pricing.input_per_million / 1_000_000,
    ))
    steps.append(AttributedStep(
        step="evaluation (judge)",
        line="evaluation",
        cost_usd=pricing.cost(trace.eval_input_tokens, trace.eval_output_tokens),
    ))
    return steps


def attribute_task(
    trace: TaskTrace,
    pricing: ModelPricing,
    *,
    conversation_id: str,
    owner: str,
    product: str,
) -> AttributedTask:
    """Attribute one task's four-line costs to a conversation and an owner."""
    return AttributedTask(
        task_id=trace.task_id,
        conversation_id=conversation_id,
        owner=owner,
        product=product,
        lines=cost_of_trace(trace, pricing),
    )


def rollup_conversation(tasks: list[AttributedTask]) -> dict[str, float | str | int]:
    """Roll attributed tasks up to one per-conversation total.

    All tasks must belong to the same conversation; the rollup sums each of
    the four lines across the conversation's tasks and reports the mean cost
    per task, so a reader can see both the conversation's total and what one
    more task in it would cost. Returns dollars per line, plus the task
    count and the conversation, owner, and product tags.
    """
    if not tasks:
        raise ValueError("Need at least one attributed task to roll up.")
    conversation_ids = {t.conversation_id for t in tasks}
    if len(conversation_ids) != 1:
        raise ValueError(
            f"Rollup needs one conversation, got {sorted(conversation_ids)}."
        )
    first = tasks[0]
    totals = {k: sum(t.lines[k] for t in tasks) for k in first.lines}
    n = len(tasks)
    return {
        "conversation_id": first.conversation_id,
        "owner": first.owner,
        "product": first.product,
        "n_tasks": n,
        **{f"{k}": v for k, v in totals.items()},
        **{f"mean_{k}_per_task": v / n for k, v in totals.items()},
    }


def format_attribution_rollup(rollup: dict[str, float | str | int]) -> str:
    """Render a per-conversation attribution rollup as readable text."""
    lines = [
        "Attribution rollup — per-conversation unit economics "
        "(ILLUSTRATIVE token counts):",
        f"  conversation: {rollup['conversation_id']}  "
        f"owner: {rollup['owner']}  product: {rollup['product']}",
        f"  tasks in conversation: {rollup['n_tasks']}",
    ]
    for line in ("model", "tools", "memory", "evaluation"):
        lines.append(
            f"  {line:>11}: ${rollup[line]:.4f} total "
            f"(${rollup[f'mean_{line}_per_task']:.4f} per task)"
        )
    lines.append(
        f"  {'total':>11}: ${rollup['total']:.4f} per conversation "
        f"(${rollup['mean_total_per_task']:.4f} per task)"
    )
    return "\n".join(lines)


def mock_copilot_trace(task_id: str) -> TaskTrace:
    """Build a mock trace for one support-copilot ticket.

    Token counts are ILLUSTRATIVE placeholders showing the shape of a real
    trace: a ReAct loop of three model calls, four Model Context Protocol
    tool calls fanning out per ticket, memory context carried on the run, and
    one judge call grading the outcome. Replace every number with a measured
    trace before publishing.
    """
    return TaskTrace(
        task_id=task_id,
        model_calls=[(3_200, 420), (2_800, 380), (2_100, 510)],
        tools=[
            ToolCall("account_state", calls=1),
            ToolCall("subscription_status", calls=1),
            ToolCall("knowledge_base_search", calls=2),
            ToolCall("refund_eligibility", calls=1),
        ],
        memory_tokens=1_500,
        eval_input_tokens=1_800,
        eval_output_tokens=120,
    )


def main() -> None:
    # Prices are placeholders for the demo: set them from your provider's
    # published price page. The breakdown shape is what matters here.
    pricing = ModelPricing(
        name="illustrative-main-model",
        input_per_million=3.00,
        output_per_million=15.00,
    )
    traces = [mock_copilot_trace(f"ticket-{i:03d}") for i in range(5)]

    print("Support copilot — cost per task (ILLUSTRATIVE token counts)")
    print("=" * 64)
    for trace in traces[:1]:
        breakdown = cost_of_trace(trace, pricing)
        for line in ("model", "tools", "memory", "evaluation"):
            print(f"  {line:>11}: ${breakdown[line]:.4f}")
        print(f"  {'total':>11}: ${breakdown['total']:.4f} per ticket")
        print(f"  input tokens: {trace.total_input_tokens:,}; "
              f"output tokens: {trace.total_output_tokens:,}")
    summary = summarise(traces, pricing)
    monthly_volume = 10_000  # assumption, stated: tickets per month
    print(f"\nMean cost per ticket over {int(summary['n'])} mock traces: "
          f"${summary['mean_total']:.4f}")
    print(f"At {monthly_volume:,} tickets/month (assumption): "
          f"${summary['mean_total'] * monthly_volume:,.2f}/month")
    print(f"Worst mock task: ${summary['worst_total']:.4f}")
    print()
    print(judge_cost_comparison())
    print()
    print(computer_use_reference())
    print()
    # Attribution rollup demo (new; the four-line output above is unchanged).
    # Two mock tickets in one conversation, attributed to one team, rolled
    # up to the per-conversation total the business thinks in. Token counts
    # are the same ILLUSTRATIVE placeholders as the four-line demo.
    attributed = [
        attribute_task(
            trace, pricing,
            conversation_id="conv-001",
            owner="support-team",
            product="support-copilot",
        )
        for trace in traces[:2]
    ]
    for task in attributed:
        steps = attribute_steps(
            next(t for t in traces if t.task_id == task.task_id), pricing
        )
        print(f"Per-step attribution for {task.task_id}:")
        for step in steps:
            print(f"  {step.step:<36} {step.line:<11} ${step.cost_usd:.4f}")
    print()
    print(format_attribution_rollup(rollup_conversation(attributed)))


if __name__ == "__main__":
    main()
