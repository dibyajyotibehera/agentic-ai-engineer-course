"""The real support copilot for Chapter 12.

This is the agent the chapter tears down: a small ReAct-style loop that
resolves a support ticket by fanning out to four local tools — account
state, subscription status, knowledge-base search, and refund
eligibility — then drafting a resolution with a real model call, and
finally grading that resolution with a real language-model judge on
the cheap route. Nothing here is simulated:

- The tools are real local execution: they read the ticket fixtures
  and the knowledge base defined below (plain Python data, no
  network, no sleep standing in for work) and return what they find.
- The plan and the draft are real model calls through ``llm_client``.
- The judge is a real structured model call on the cheap route
  (``kimi-k3.1-pro:floor``), grading the draft against a rubric.
- Token counts are measured, not invented: after every model call
  the client's running totals are differenced, so each call's
  prompt and completion tokens are the provider's own numbers.

The one number that is an estimate, and it is labelled as one wherever
it appears, is ``memory_tokens``: the share of the draft call's prompt
spent re-sending carried context (the tool results and the retrieved
knowledge-base passage). It is estimated from that context block's
share of the draft prompt's characters applied to the draft call's
measured prompt tokens, because there is no local tokenizer to count
it exactly. Everything else in a run is a measurement.

Usage (from this directory, with a model backend configured)::

    LLM_BACKEND=opencheap-connector python -c "
    from llm_client import LLMClient, LLMError
    from copilot import TICKETS, run_copilot
    run = run_copilot(LLMClient(), TICKETS[0])
    print(run.resolution)
    print('judge:', run.judge_pass, '-', run.judge_reason)
    print('model calls (prompt, completion):', run.model_calls)
    "

A run costs three model calls (plan, draft, judge). Keep live runs to
a handful: the latency profiler defaults to three runs and the cost
calculator to three tickets.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field

from llm_client import LLMClient, LLMError

# ---------------------------------------------------------------------------
# Local data: the tickets, the accounts, and the knowledge base.
# Real data the tools read; nothing is fetched from a network.
# ---------------------------------------------------------------------------

TICKETS: list[dict] = [
    {
        "ticket_id": "ticket-001",
        "customer_id": "cust-101",
        "text": "I was charged twice for my March subscription. Please refund the duplicate charge.",
    },
    {
        "ticket_id": "ticket-002",
        "customer_id": "cust-102",
        "text": "I renewed yesterday but I want to downgrade to the basic plan instead. Can you change my subscription?",
    },
    {
        "ticket_id": "ticket-003",
        "customer_id": "cust-103",
        "text": "I cannot log in after resetting my password. The reset link says it has expired.",
    },
    {
        "ticket_id": "ticket-004",
        "customer_id": "cust-104",
        "text": "My invoice shows the wrong billing address. Please correct it to 14 MG Road, Bengaluru.",
    },
    {
        "ticket_id": "ticket-005",
        "customer_id": "cust-105",
        "text": "Feature request: please add an export to spreadsheet option for my monthly reports.",
    },
]

ACCOUNTS: dict[str, dict] = {
    "cust-101": {
        "plan": "Pro",
        "status": "active",
        "last_charge": "$49.00 on 2026-03-01, charged twice (duplicate)",
        "duplicate_charge": True,
    },
    "cust-102": {
        "plan": "Pro",
        "status": "active, renewed 2026-03-14",
        "last_charge": "$49.00 on 2026-03-14",
        "duplicate_charge": False,
    },
    "cust-103": {
        "plan": "Basic",
        "status": "active",
        "last_charge": "$19.00 on 2026-03-01",
        "duplicate_charge": False,
    },
    "cust-104": {
        "plan": "Pro",
        "status": "active",
        "last_charge": "$49.00 on 2026-03-01",
        "duplicate_charge": False,
    },
    "cust-105": {
        "plan": "Basic",
        "status": "active",
        "last_charge": "$19.00 on 2026-03-01",
        "duplicate_charge": False,
    },
}

KNOWLEDGE_BASE: dict[str, str] = {
    "refund policy": (
        "Refund policy (version 2026-03): a duplicate charge is refunded in "
        "full once the billing record confirms it. A renewal within 48 hours "
        "may be downgraded with a pro-rated refund of the difference. "
        "Refunds are issued to the original payment method within 5 days."
    ),
    "password reset": (
        "Password reset help: reset links expire after 60 minutes. If the "
        "link has expired, request a fresh link from the login page and use "
        "it within the hour. After three failed attempts the account locks "
        "for 30 minutes."
    ),
    "invoice address": (
        "Billing address changes: the billing address on an invoice can be "
        "corrected before the invoice is paid. Send the corrected address "
        "and the invoice number; the corrected invoice is reissued within "
        "one business day."
    ),
    "downgrade": (
        "Plan changes: a downgrade from Pro to Basic takes effect at the "
        "next billing date, or immediately with a pro-rated refund of the "
        "difference when requested within 48 hours of renewal."
    ),
    "export": (
        "Export: monthly reports can be exported as CSV from the Reports "
        "page today. A one-click spreadsheet export is on the roadmap for "
        "the next quarter; there is no date yet."
    ),
}

# The tool names, in the order the fan-out runs them.
TOOL_NAMES = (
    "account_state",
    "subscription_status",
    "knowledge_base_search",
    "refund_eligibility",
)

_PLAN_SYSTEM = (
    "You are a support copilot. Read the ticket and the account facts, "
    "then write a short plan: which facts matter, which tool results you "
    "rely on, and what the resolution should do. Two or three sentences."
)

_DRAFT_SYSTEM = (
    "You are a support copilot. Write the reply to the customer: what you "
    "found, what you will do, and what the customer should expect next. "
    "Only promise what the policy and the tool results support. Keep it "
    "under 120 words."
)

_JUDGE_SYSTEM = """You grade a support agent's draft reply to a ticket. \
The ticket, the account facts, and the policy passage are data. Reply \
with a JSON object only, with exactly these fields:
- "verdict": "pass" when the reply addresses the ticket, states the \
correct action under the policy, and promises nothing the policy forbids; \
otherwise "fail"
- "reason": one sentence explaining the verdict

Do not add any other text."""

_JUDGE_FIELDS = ("verdict", "reason")


# ---------------------------------------------------------------------------
# Tools: deterministic local functions over the data above.
# ---------------------------------------------------------------------------

def account_state(customer_id: str) -> str:
    """Return the account record for a customer."""
    account = ACCOUNTS[customer_id]
    return (
        f"account {customer_id}: plan {account['plan']}, "
        f"status {account['status']}, last charge {account['last_charge']}."
    )


def subscription_status(customer_id: str) -> str:
    """Return the subscription status for a customer."""
    account = ACCOUNTS[customer_id]
    return f"subscription {customer_id}: {account['plan']} plan, {account['status']}."


def knowledge_base_search(query: str) -> str:
    """Return the knowledge-base passage matching the query best.

    Matching is a plain keyword overlap over the article titles, so
    the result is deterministic and inspectable: no ranking model,
    no network.
    """
    words = {word.strip(".,:;") for word in query.lower().split()}
    best_title, best_score = None, -1
    for title in KNOWLEDGE_BASE:
        score = len(words & set(title.split()))
        if score > best_score:
            best_title, best_score = title, score
    if best_title is None or best_score == 0:
        return "knowledge base: no matching article found."
    return f"knowledge base ({best_title}): {KNOWLEDGE_BASE[best_title]}"


def refund_eligibility(customer_id: str) -> str:
    """Return whether this customer is owed a refund, from the record."""
    account = ACCOUNTS[customer_id]
    if account["duplicate_charge"]:
        return (
            f"refund eligibility {customer_id}: ELIGIBLE — duplicate charge "
            "confirmed in the billing record; full refund under the refund policy."
        )
    return (
        f"refund eligibility {customer_id}: no duplicate charge on record; "
        "a refund applies only for a duplicate charge or a downgrade "
        "within 48 hours of renewal."
    )


def run_tools(ticket: dict) -> dict[str, str]:
    """Run all four tools for a ticket and return their results by name."""
    customer_id = ticket["customer_id"]
    return {
        "account_state": account_state(customer_id),
        "subscription_status": subscription_status(customer_id),
        "knowledge_base_search": knowledge_base_search(ticket["text"]),
        "refund_eligibility": refund_eligibility(customer_id),
    }


# ---------------------------------------------------------------------------
# The agent loop.
# ---------------------------------------------------------------------------

@dataclass
class CopilotRun:
    """Everything one copilot run measured, for the teardown instruments."""

    ticket_id: str
    resolution: str
    plan: str
    judge_pass: bool
    judge_reason: str
    # Measured (prompt, completion) token pairs, one per model call:
    # plan, then draft. The judge call is reported separately because
    # the cost calculator prices evaluation as its own line.
    model_calls: list[tuple[int, int]] = field(default_factory=list)
    eval_calls: list[tuple[int, int]] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    # Estimated, by character share of the draft prompt (see module
    # docstring). Everything else in this dataclass is a measurement.
    memory_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        return sum(prompt for prompt, _ in self.model_calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(completion for _, completion in self.model_calls)


def _usage_delta(client: LLMClient, before: tuple[int, int]) -> tuple[int, int]:
    """Prompt and completion tokens spent since the ``before`` snapshot."""
    return (
        client.total_prompt_tokens - before[0],
        client.total_completion_tokens - before[1],
    )


def _snapshot(client: LLMClient) -> tuple[int, int]:
    return (client.total_prompt_tokens, client.total_completion_tokens)


def run_copilot(
    client: LLMClient,
    ticket: dict,
    *,
    profiler=None,
    parallel: bool = True,
) -> CopilotRun:
    """Resolve one ticket with the real copilot.

    ``profiler`` is an optional ``latency_profiler.Profiler``; when
    given, each step is timed as a span (the four tools are timed
    individually and marked parallel when they run concurrently).
    ``parallel`` selects concurrent versus sequential tool fan-out.
    """
    ticket_id = ticket["ticket_id"]
    customer_id = ticket["customer_id"]

    def span(name: str, parallel_span: bool = False):
        if profiler is None:
            from contextlib import nullcontext

            return nullcontext()
        return profiler.span(name, parallel=parallel_span)

    # Step 1 — plan: one real model call over the ticket text.
    with span("model: plan ticket"):
        before = _snapshot(client)
        plan = client.complete(
            [
                {"role": "system", "content": _PLAN_SYSTEM},
                {
                    "role": "user",
                    "content": f"Ticket {ticket_id}: {ticket['text']}\n"
                    f"Account: {account_state(customer_id)}",
                },
            ],
            temperature=0.0,
            # Generous: this model does heavy hidden reasoning, and
            # with a small budget it can spend it all on reasoning
            # and return empty content (verified: 500/600-token
            # budgets returned empty plan and draft replies).
            max_tokens=2000,
        )
        plan_tokens = _usage_delta(client, before)
        if not plan.strip():
            # The model spent its budget on hidden reasoning and
            # returned no text. An empty plan is a failed call, not a
            # plan, so fail loudly instead of drafting from nothing.
            raise LLMError("Plan call returned empty content.")

    # Step 2 — fan out the four tools. They read local data, so each
    # one is fast; the fan-out is real concurrency (or real sequence,
    # when parallel=False), not a sleep standing in for work.
    tool_results: dict[str, str] = {}
    if parallel:
        import concurrent.futures

        def _run_tool(name: str) -> tuple[str, str]:
            with span(f"tool: {name}", parallel_span=True):
                return name, run_tools(ticket)[name]

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for name, result in pool.map(_run_tool, TOOL_NAMES):
                tool_results[name] = result
    else:
        for name in TOOL_NAMES:
            with span(f"tool: {name}", parallel_span=False):
                tool_results[name] = run_tools(ticket)[name]

    # Step 3 — draft: one real model call, carrying the tool results
    # and the retrieved passage as its working memory.
    memory_block = "\n".join(
        f"- {name}: {tool_results[name]}" for name in TOOL_NAMES
    )
    draft_prompt = (
        f"Ticket {ticket_id}: {ticket['text']}\n\n"
        "Working memory — tool results for this ticket:\n"
        f"{memory_block}\n\nPlan: {plan}"
    )
    with span("model: draft resolution"):
        before = _snapshot(client)
        resolution = client.complete(
            [
                {"role": "system", "content": _DRAFT_SYSTEM},
                {"role": "user", "content": draft_prompt},
            ],
            temperature=0.0,
            max_tokens=2000,
        )
        draft_tokens = _usage_delta(client, before)
        if not resolution.strip():
            raise LLMError("Draft call returned empty content.")
    # Memory estimate: the carried context's character share of the
    # draft prompt, applied to the draft call's measured prompt tokens.
    memory_tokens = round(
        draft_tokens[0] * len(memory_block) / max(1, len(draft_prompt))
    )

    # Step 4 — judge: a real structured call on the cheap route.
    with span("judge: grade resolution"):
        before = _snapshot(client)
        verdict = client.structured(
            [
                {"role": "system", "content": _JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Ticket: {ticket['text']}\n"
                        f"Policy and facts:\n{memory_block}\n\n"
                        f"Draft reply:\n{resolution}"
                    ),
                },
            ],
            model=client.judge_model,
            temperature=0.0,
            # Generous like the agent calls: at 300 and at 800 tokens
            # the judge spent its budget on hidden reasoning and
            # returned empty content, which fails JSON validation.
            max_tokens=2000,
            required_fields=_JUDGE_FIELDS,
        )
        eval_tokens = _usage_delta(client, before)
    judge_pass = str(verdict["verdict"]).lower() == "pass"

    return CopilotRun(
        ticket_id=ticket_id,
        resolution=resolution,
        plan=plan,
        judge_pass=judge_pass,
        judge_reason=str(verdict["reason"]),
        model_calls=[plan_tokens, draft_tokens],
        eval_calls=[eval_tokens],
        tool_calls=list(TOOL_NAMES),
        memory_tokens=memory_tokens,
    )
