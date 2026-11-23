"""
01 — Three shapes for the same job: DAG, cyclic graph, free-form loop.

No third-party dependencies: run with the standard library only.

    python 01_dag_vs_cycle.py            # deterministic exhibit, no key
    python 01_dag_vs_cycle.py --live     # real model calls (needs a backend)

The job is the same in all three shapes so the comparison is honest: triage a
queue of support tickets, draft a reply for each one, have a reviewer judge
the draft, redraft at most twice, and escalate anything that still fails.

Two engines run that job. The default engine is deterministic: the draft is
a template and the reviewer is a rule — a ticket either passes on the first
draft or needs exactly one redraft. That determinism is deliberate, not a
shortcut left in by accident: the file's purpose is to compare control-flow
topologies, and the comparison is only exact if every shape faces the same
reviewer with the same verdicts. A real model's verdicts vary from run to
run, so the deterministic engine is the controlled experiment and the live
engine is the same job with the stand-ins removed. Pass ``--live`` to run
the second engine: drafts are written by a real model, drafts are judged
by a real model acting as reviewer (``live_review.py``), and in the
free-form loop the next step is chosen by a real model at every step. The
live run needs a configured model backend — ``LLM_API_KEY`` or
``LLM_BACKEND=opencheap-connector`` on a machine where the OpenCheap
connector is registered — and it prints the token and cost totals at the
end. The outcomes are the same in both engines by design: the first draft
is a holding reply that fails review on substance, the second draft is
substantive and passes, so the directed acyclic graph (DAG) still escalates
the ticket that needs a second draft while the cyclic graph resolves it.

What to notice
--------------
* The directed acyclic graph (DAG) version cannot express "try again" at
  all. Its edges only move forward, so a failed draft has nowhere to go
  except a dead-end branch that was drawn in advance. That is the DAG's
  strength (every path is enumerable, every run terminates) and its limit.
* The cyclic version adds one back edge — review -> draft — guarded by an
  attempt counter. The cycle is what makes retry possible, and the guard is
  what makes the cycle safe: without it, a stubborn ticket loops forever.
* The free-form loop hands the next-step decision to a model at every step.
  It can do everything the cyclic version can, plus things nobody drew in
  advance — and that is exactly why it is the hardest to test, to bound,
  and to resume after a crash. Every step re-reads the whole transcript,
  so its cost grows with the square of the step count, and its behaviour
  can only be characterised statistically, never enumerated. In the live
  engine the decision is a real model call (``live_review.decide_next_step``);
  the deterministic engine reproduces the shape of that decision with a
  rule, so the loop can be run and tested with no key.

The lesson's decision framework lives in the parent README.md; this file is
the exhibit that makes the framework concrete.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field

MAX_ATTEMPTS = 2


@dataclass
class Ticket:
    """One support ticket. ``needs_rework`` is the reviewer's verdict."""

    ticket_id: str
    summary: str
    needs_rework: bool


@dataclass
class RunLog:
    """The durable record of a run: what happened, in order, as plain data."""

    steps: list[str] = field(default_factory=list)
    escalated: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)

    def record(self, step: str) -> None:
        self.steps.append(step)


TICKETS = [
    Ticket("T-101", "Refund for a duplicate charge", needs_rework=False),
    Ticket("T-102", "Export fails on large workspaces", needs_rework=True),
    Ticket("T-103", "Single sign-on login loop", needs_rework=False),
]


# ---------------------------------------------------------------------------
# The deterministic engine: the controlled exhibit.
# ---------------------------------------------------------------------------

def draft(ticket: Ticket, attempt: int) -> str:
    """Template draft. The live engine replaces this with a real model call."""
    return f"draft v{attempt} for {ticket.ticket_id}: {ticket.summary}"


def review(ticket: Ticket, attempt: int) -> bool:
    """Deterministic reviewer: a ticket that needs rework fails its first draft only.

    This rule is the controlled variable of the exhibit. Change it — as
    Exercise 1 asks you to, so T-102 needs two redrafts — and the three
    shapes' outcomes change in exactly the ways the lesson describes.
    The live engine replaces this rule with a real model judging a real
    draft (see ``live_review.review_draft``).
    """
    return not (ticket.needs_rework and attempt == 1)


def deterministic_decide(ticket: Ticket, attempt: int,
                         history: list[str]) -> str:
    """The deterministic version of the free-form loop's next-step decision.

    Reproduces the shape of a model choosing the next action — draft,
    redraft, resolve, or escalate — as a rule, so the loop runs with no
    key. The live engine replaces this with a real model call
    (``live_review.decide_next_step``).
    """
    if attempt > MAX_ATTEMPTS:
        return "escalate"
    if review(ticket, attempt):
        return "resolve"
    return "redraft" if attempt < MAX_ATTEMPTS else "escalate"


class DeterministicEngine:
    """Drafts from a template, reviews by rule, decides by rule."""

    def draft(self, ticket: Ticket, attempt: int) -> str:
        return draft(ticket, attempt)

    def review(self, ticket: Ticket, attempt: int,
               draft_text: str) -> tuple[bool, str]:
        passed = review(ticket, attempt)
        return passed, "pass" if passed else "needs rework"

    def decide(self, ticket: Ticket, attempt: int, draft_text: str,
               passed: bool, reason: str, history: list[str]) -> str:
        return deterministic_decide(ticket, attempt, history)


# ---------------------------------------------------------------------------
# The live engine: every stand-in replaced by a real model call.
# ---------------------------------------------------------------------------

class LiveEngine:
    """Drafts, reviews, and decides with a real model (``live_review``)."""

    def __init__(self, client) -> None:
        self.client = client

    def draft(self, ticket: Ticket, attempt: int) -> str:
        from live_review import draft_reply

        return draft_reply(self.client, ticket.ticket_id, ticket.summary,
                           attempt, needs_rework=ticket.needs_rework)

    def review(self, ticket: Ticket, attempt: int,
               draft_text: str) -> tuple[bool, str]:
        from live_review import review_draft

        verdict = review_draft(self.client, ticket.ticket_id, draft_text,
                               attempt)
        return bool(verdict["passed"]), str(verdict["reason"])

    def decide(self, ticket: Ticket, attempt: int, draft_text: str,
               passed: bool, reason: str, history: list[str]) -> str:
        from live_review import decide_next_step

        verdict = decide_next_step(self.client, ticket.ticket_id,
                                   ticket.summary, attempt, draft_text,
                                   passed, reason, history)
        action = str(verdict["action"]).strip().lower()
        if action not in ("resolve", "redraft", "escalate"):
            raise ValueError(f"model returned unknown action: {action!r}")
        return action


# ---------------------------------------------------------------------------
# Shape 1: a directed acyclic graph (DAG). Edges only move forward.
# ---------------------------------------------------------------------------

def run_dag(tickets: list[Ticket], engine) -> RunLog:
    """Draft once, review once. A failed draft goes to a pre-drawn dead end.

    There is no back edge, so there is no retry. The graph is a pipeline:
    draft -> review -> (resolve | escalate). Every path through it is known
    before the run starts, which is why a DAG is the easiest shape to test
    and the easiest to reason about — and why it cannot do iterative work.
    """
    log = RunLog()
    for ticket in tickets:
        text = engine.draft(ticket, 1)
        log.record(f"draft {ticket.ticket_id} (attempt 1)")
        log.record(f"review {ticket.ticket_id}")
        passed, reason = engine.review(ticket, 1, text)
        if passed:
            log.record(f"resolve {ticket.ticket_id}")
            log.resolved.append(ticket.ticket_id)
        else:
            # No way back to draft: the only forward edge is to escalation.
            log.record(f"escalate {ticket.ticket_id} (no retry edge exists; "
                       f"review: {reason})")
            log.escalated.append(ticket.ticket_id)
    return log


# ---------------------------------------------------------------------------
# Shape 2: a cyclic graph. One guarded back edge: review -> draft.
# ---------------------------------------------------------------------------

def run_cyclic(tickets: list[Ticket], engine) -> RunLog:
    """Draft, review, and loop back to draft while the guard allows it.

    The cycle is bounded by ``MAX_ATTEMPTS``. That bound is not decoration:
    it is the termination proof. A cycle without a guard is a loop that can
    run until the budget, the context window, or the operator's patience
    gives out — whichever fails first.
    """
    log = RunLog()
    for ticket in tickets:
        attempt = 1
        while True:
            text = engine.draft(ticket, attempt)
            log.record(f"draft {ticket.ticket_id} (attempt {attempt})")
            log.record(f"review {ticket.ticket_id} (attempt {attempt})")
            passed, reason = engine.review(ticket, attempt, text)
            if passed:
                log.record(f"resolve {ticket.ticket_id}")
                log.resolved.append(ticket.ticket_id)
                break
            if attempt >= MAX_ATTEMPTS:
                log.record(f"escalate {ticket.ticket_id} (attempts exhausted; "
                           f"review: {reason})")
                log.escalated.append(ticket.ticket_id)
                break
            attempt += 1  # the back edge, taken only while the guard holds
    return log


# ---------------------------------------------------------------------------
# Shape 3: a free-form agent loop. The model picks the next step each time.
# ---------------------------------------------------------------------------

def run_free_form(tickets: list[Ticket], engine) -> RunLog:
    """Reason, act, observe, repeat — until the model says it is done.

    Nothing here bounds the loop except the model's own judgement plus the
    hard step cap. If the model never emits "resolve" or "escalate", the cap
    is the only thing standing between the run and an unbounded bill. That
    is the free-form loop's defining risk, and the reason the lesson insists
    on a deterministic stop condition owned by code, not by the model.
    """
    log = RunLog()
    hard_cap = 10
    for ticket in tickets:
        attempt = 1
        steps_taken = 0
        draft_text = ""
        passed, reason = False, ""
        while steps_taken < hard_cap:
            steps_taken += 1
            if draft_text == "":
                draft_text = engine.draft(ticket, attempt)
                log.record(f"draft {ticket.ticket_id} (attempt {attempt})")
                passed, reason = engine.review(ticket, attempt, draft_text)
                log.record(f"review {ticket.ticket_id} (attempt {attempt}): "
                           f"{reason}")
            decision = engine.decide(ticket, attempt, draft_text, passed,
                                     reason, log.steps)
            log.record(f"model decides '{decision}' for {ticket.ticket_id}")
            if decision == "resolve":
                log.resolved.append(ticket.ticket_id)
                break
            if decision == "escalate":
                log.escalated.append(ticket.ticket_id)
                break
            # "redraft": the model chose to loop. The guard is owned by
            # code, not by the model: a redraft past the attempt budget
            # is overruled to an escalation.
            if attempt >= MAX_ATTEMPTS:
                log.record(f"redraft refused for {ticket.ticket_id}: "
                           f"attempt budget spent, escalating")
                log.escalated.append(ticket.ticket_id)
                break
            attempt += 1
            draft_text = ""
        else:
            log.record(f"HARD CAP hit on {ticket.ticket_id}: escalating")
            log.escalated.append(ticket.ticket_id)
    return log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Three control-flow shapes for the same triage job.")
    parser.add_argument(
        "--live", action="store_true",
        help="use a real model for drafting, reviewing, and deciding "
             "(needs LLM_API_KEY or LLM_BACKEND=opencheap-connector)")
    args = parser.parse_args(argv)
    if args.live:
        from llm_client import LLMClient

        client = LLMClient()  # raises loudly if no backend is configured
        engine = LiveEngine(client)
    else:
        client = None
        engine = DeterministicEngine()
    for name, runner in [
        ("directed acyclic graph (DAG)", run_dag),
        ("cyclic graph", run_cyclic),
        ("free-form agent loop", run_free_form),
    ]:
        log = runner(TICKETS, engine)
        print(f"\n== {name} ==")
        print(f"steps: {len(log.steps)}  resolved: {log.resolved}  "
              f"escalated: {log.escalated}")
        for step in log.steps:
            print(f"  - {step}")
    print(
        "\nSame tickets, same reviewer. The DAG escalates T-102 because it "
        "cannot retry; the cyclic graph and the loop both resolve it on the "
        "second draft. The difference between the last two is not the "
        "outcome on this input — it is who owns the next-step decision, "
        "and therefore who can prove the run will end."
    )
    if client is not None:
        print(f"\n[live] model usage: {client.total_prompt_tokens} prompt + "
              f"{client.total_completion_tokens} completion tokens, "
              f"cost ${client.total_cost_usd:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
