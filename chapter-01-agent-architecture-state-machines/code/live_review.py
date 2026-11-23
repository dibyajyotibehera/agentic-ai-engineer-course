"""Real model calls for Chapter 1: drafting, reviewing, and deciding.

This is the chapter's only model-facing module. Everything in it calls a
real model through ``llm_client.LLMClient`` — there is no stand-in here.
The three functions map onto the three jobs a triage agent asks a model
to do, and each one is used by more than one file in this chapter, which
is why they live here instead of being copied into each demo:

* ``draft_reply`` — write the reply to a customer. Agent work, so it runs
  on the client's default model.
* ``review_draft`` — judge whether a draft is good enough to send. A
  short classification, so it runs on the cheap judge route
  (``kimi-k3.1-pro:floor``) with a small token budget.
* ``decide_next_step`` — the free-form loop's next-step decision, also a
  short classification on the judge route.

The draft prompts are deliberately different by attempt and by ticket,
because the chapter's exhibit needs a first draft that genuinely fails
review and a second draft that genuinely passes it — but only for the
tickets that need rework. A straightforward ticket (one that does not
need rework) gets a substantive reply on the first attempt, because
there is nothing to investigate first. A ticket that needs rework gets
a brief holding reply on attempt 1 — an acknowledgement with no
substance, the honest first contact while the issue is investigated —
and a substantive reply on attempt 2 and later. The reviewer judges
exactly that distinction, so a holding reply fails and a substantive
reply passes, and the three control-flow shapes in
``01_dag_vs_cycle.py`` keep the same outcomes live as they have
deterministic: the directed acyclic graph (DAG) still escalates only
the ticket that needs a second draft, while the cyclic graph resolves
it on the second draft.

Token budgets are generous on purpose. The model used here is a
reasoning model: it can spend a small budget entirely on reasoning and
return empty content, so every call asks for far more tokens than the
reply needs — 1,500 for a draft, 800 for a verdict — even though the
reply is a few sentences or a small JSON object. The draft call retries
once with a doubled budget if the first reply comes back empty, and
raises loudly if the retry is empty too: an empty draft passed silently
to the reviewer would be a demo pretending to work. The cost is still
a fraction of a cent per call.
"""

from __future__ import annotations

from llm_client import LLMClient, LLMError

MAX_TOKENS_DRAFT = 1500
MAX_TOKENS_JUDGE = 800


def draft_reply(client: LLMClient, ticket_id: str, summary: str,
                attempt: int, needs_rework: bool = True) -> str:
    """Write a real reply draft for a ticket with a real model.

    A straightforward ticket (``needs_rework`` false) gets a substantive
    reply on the first attempt: there is nothing to investigate first.
    A ticket that needs rework gets a holding reply on attempt 1 — it
    acknowledges the ticket and promises an update, but gives the
    customer nothing concrete — and a substantive reply on attempt 2
    and later. The difference is the point: the reviewer below judges
    substance, not politeness, and it is what makes the retry in the
    cyclic shape meaningful instead of a second roll of the same dice.
    """
    if attempt == 1 and needs_rework:
        instruction = (
            "Write a brief holding reply: acknowledge the issue, say the "
            "team is looking into it, and promise an update. Do not offer "
            "a workaround, a timeline, or any next step the customer can "
            "take. Two sentences at most."
        )
    else:
        instruction = (
            "Write a substantive reply: acknowledge the issue, explain "
            "what is happening in one sentence, and give the customer "
            "something concrete — a workaround they can use now, a clear "
            "next step, or a specific timeline. Three sentences at most."
        )
    for budget in (MAX_TOKENS_DRAFT, MAX_TOKENS_DRAFT * 2):
        text = client.complete(
            [
                {"role": "system",
                 "content": "You are a support agent drafting a reply to a "
                            "customer. Write only the reply, no preamble."},
                {"role": "user",
                 "content": f"Ticket {ticket_id}: {summary}\n{instruction}"},
            ],
            max_tokens=budget,
        ).strip()
        if text:
            return text
    raise LLMError(
        f"model returned an empty draft for {ticket_id} (attempt {attempt}) "
        "even after a retry with a doubled token budget"
    )


def review_draft(client: LLMClient, ticket_id: str, draft_text: str,
                 attempt: int) -> dict:
    """Judge a draft with a real model, as a structured verdict.

    Returns ``{"passed": bool, "reason": str}``. A draft passes only if
    it gives the customer something concrete — a workaround, a next
    step, or a timeline — rather than a bare acknowledgement. A holding
    reply fails on substance, however polite it is.
    """
    return client.structured(
        [
            {"role": "system",
             "content": "You are a support-quality reviewer. Read the "
                        "draft reply and judge it against one rule: a "
                        "draft passes only if it gives the customer "
                        "something concrete — a workaround, a next step, "
                        "or a specific timeline. A polite acknowledgement "
                        "with nothing concrete fails. Reply with a JSON "
                        "object with exactly two fields: \"passed\" "
                        "(true or false) and \"reason\" (one sentence)."},
            {"role": "user",
             "content": f"Ticket {ticket_id}, draft attempt {attempt}:\n"
                        f"{draft_text}"},
        ],
        model=client.judge_model,
        max_tokens=MAX_TOKENS_JUDGE,
        required_fields=("passed", "reason"),
    )


def decide_next_step(client: LLMClient, ticket_id: str, summary: str,
                     attempt: int, draft_text: str, review_passed: bool,
                     review_reason: str, history: list[str]) -> dict:
    """Ask a real model what the free-form loop should do next.

    Returns ``{"action": str, "reason": str}`` where the action is one
    of ``resolve``, ``redraft``, or ``escalate``. The model sees the
    ticket, the latest draft, and the reviewer's verdict, plus the
    steps taken so far. The caller owns the stop condition — if the
    attempt budget is spent, a ``redraft`` decision is overruled to
    ``escalate`` — because the model proposes the next step and code
    decides whether the loop is allowed to continue.
    """
    history_text = "\n".join(history[-8:]) if history else "(no steps yet)"
    return client.structured(
        [
            {"role": "system",
             "content": "You are the controller of a support-triage "
                        "agent loop. Given the ticket, the latest draft, "
                        "and the reviewer's verdict, choose the next "
                        "action: \"resolve\" if the draft passed review "
                        "and the ticket is done, \"redraft\" if the draft "
                        "failed review and another attempt could fix it, "
                        "or \"escalate\" if the ticket cannot be resolved "
                        "by redrafting. Reply with a JSON object with "
                        "exactly two fields: \"action\" (one of resolve, "
                        "redraft, escalate) and \"reason\" (one sentence)."},
            {"role": "user",
             "content": f"Ticket {ticket_id}: {summary}\n"
                        f"Attempt {attempt} draft:\n{draft_text}\n"
                        f"Reviewer verdict: "
                        f"{'passed' if review_passed else 'failed'} — "
                        f"{review_reason}\n"
                        f"Steps so far:\n{history_text}"},
        ],
        model=client.judge_model,
        max_tokens=MAX_TOKENS_JUDGE,
        required_fields=("action", "reason"),
    )
