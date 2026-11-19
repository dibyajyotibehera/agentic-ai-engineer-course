"""
01 — Three shapes for the same job: DAG, cyclic graph, free-form loop.

No third-party dependencies: run with the standard library only.

    python 01_dag_vs_cycle.py

The job is the same in all three shapes so the comparison is honest: triage a
queue of support tickets, draft a reply for each one, have a (mock) reviewer
judge the draft, redraft at most twice, and escalate anything that still
fails. The mock reviewer is deterministic — a ticket either passes on the
first draft or needs exactly one redraft — so the only thing that varies
between the three runs is the shape of the control flow, not the luck of a
model call.

What to notice
--------------
* The directed acyclic graph (DAG) version cannot express "try again" at
  all. Its edges only move forward, so a failed draft has nowhere to go
  except a dead-end branch that was drawn in advance. That is the DAG's
  strength (every path is enumerable, every run terminates) and its limit.
* The cyclic version adds one back edge — review -> draft — guarded by an
  attempt counter. The cycle is what makes retry possible, and the guard is
  what makes the cycle safe: without it, a stubborn ticket loops forever.
* The free-form loop hands the next-step decision to a mock model at every
  step. It can do everything the cyclic version can, plus things nobody
  drew in advance — and that is exactly why it is the hardest to test, to
  bound, and to resume after a crash. Every step re-reads the whole
  transcript, so its cost grows with the square of the step count, and its
  behaviour can only be characterised statistically, never enumerated.

The lesson's decision framework lives in the parent README.md; this file is
the exhibit that makes the framework concrete.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

MAX_ATTEMPTS = 2


@dataclass
class Ticket:
    """One support ticket. ``needs_rework`` is the mock reviewer's verdict."""

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


def draft(ticket: Ticket, attempt: int) -> str:
    return f"draft v{attempt} for {ticket.ticket_id}: {ticket.summary}"


def review(ticket: Ticket, attempt: int) -> bool:
    """Mock reviewer: a ticket that needs rework fails its first draft only."""
    return not (ticket.needs_rework and attempt == 1)


# ---------------------------------------------------------------------------
# Shape 1: a directed acyclic graph (DAG). Edges only move forward.
# ---------------------------------------------------------------------------

def run_dag(tickets: list[Ticket]) -> RunLog:
    """Draft once, review once. A failed draft goes to a pre-drawn dead end.

    There is no back edge, so there is no retry. The graph is a pipeline:
    draft -> review -> (resolve | escalate). Every path through it is known
    before the run starts, which is why a DAG is the easiest shape to test
    and the easiest to reason about — and why it cannot do iterative work.
    """
    log = RunLog()
    for ticket in tickets:
        log.record(f"draft {ticket.ticket_id} (attempt 1)")
        log.record(f"review {ticket.ticket_id}")
        if review(ticket, attempt=1):
            log.record(f"resolve {ticket.ticket_id}")
            log.resolved.append(ticket.ticket_id)
        else:
            # No way back to draft: the only forward edge is to escalation.
            log.record(f"escalate {ticket.ticket_id} (no retry edge exists)")
            log.escalated.append(ticket.ticket_id)
    return log


# ---------------------------------------------------------------------------
# Shape 2: a cyclic graph. One guarded back edge: review -> draft.
# ---------------------------------------------------------------------------

def run_cyclic(tickets: list[Ticket]) -> RunLog:
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
            log.record(f"draft {ticket.ticket_id} (attempt {attempt})")
            draft(ticket, attempt)
            log.record(f"review {ticket.ticket_id} (attempt {attempt})")
            if review(ticket, attempt):
                log.record(f"resolve {ticket.ticket_id}")
                log.resolved.append(ticket.ticket_id)
                break
            if attempt >= MAX_ATTEMPTS:
                log.record(f"escalate {ticket.ticket_id} (attempts exhausted)")
                log.escalated.append(ticket.ticket_id)
                break
            attempt += 1  # the back edge, taken only while the guard holds
    return log


# ---------------------------------------------------------------------------
# Shape 3: a free-form agent loop. The model picks the next step each time.
# ---------------------------------------------------------------------------

def mock_model_decide(ticket: Ticket, attempt: int, history: list[str]) -> str:
    """Stand-in for a language model choosing the next action.

    A real model would read the history and emit a tool call; this mock
    reproduces the shape of that decision — draft, redraft, resolve, or
    escalate — without needing an application programming interface (API)
    key. The point is structural: the sequence of steps is decided at
    run time, step by step, and is not visible in the source code.
    """
    if attempt > MAX_ATTEMPTS:
        return "escalate"
    if review(ticket, attempt):
        return "resolve"
    return "redraft" if attempt < MAX_ATTEMPTS else "escalate"


def run_free_form(tickets: list[Ticket]) -> RunLog:
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
        while steps_taken < hard_cap:
            steps_taken += 1
            decision = mock_model_decide(ticket, attempt, log.steps)
            log.record(f"model decides '{decision}' for {ticket.ticket_id}")
            if decision == "resolve":
                log.resolved.append(ticket.ticket_id)
                break
            if decision == "escalate":
                log.escalated.append(ticket.ticket_id)
                break
            attempt += 1  # "redraft": the model chose to loop
        else:
            log.record(f"HARD CAP hit on {ticket.ticket_id}: escalating")
            log.escalated.append(ticket.ticket_id)
    return log


def main() -> None:
    for name, runner in [
        ("directed acyclic graph (DAG)", run_dag),
        ("cyclic graph", run_cyclic),
        ("free-form agent loop", run_free_form),
    ]:
        log = runner(TICKETS)
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


if __name__ == "__main__":
    main()
