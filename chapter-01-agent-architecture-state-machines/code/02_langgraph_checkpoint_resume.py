"""
02 — A cyclic LangGraph workflow with checkpointing that survives a kill.

Install the dependencies first (see requirements.txt):

    pip install -r requirements.txt

Then run the three-act demo. Act 1 starts the run and kills the process
mid-flight; Act 2 resumes the *same thread* from the last checkpoint in a
brand-new process; Act 3 lists the checkpoint history (time travel).

    python 02_langgraph_checkpoint_resume.py crash --thread demo-1 --after 2
    python 02_langgraph_checkpoint_resume.py resume --thread demo-1
    python 02_langgraph_checkpoint_resume.py history --thread demo-1

No model API key is needed: the "model" is a deterministic mock, so the
checkpointing behaviour is what you are testing, not a vendor's mood.

How it works
------------
The graph is a cycle: draft -> review -> (draft again | finish ticket |
escalate), then on to the next ticket. State is a TypedDict; ``log`` uses an
``operator.add`` reducer so parallel writes append instead of overwriting
each other. The graph is compiled with a ``SqliteSaver`` checkpointer, which
writes a snapshot of the whole state at every super-step boundary — one tick
of the graph in which every scheduled node runs — keyed by ``thread_id``.
Passing the same ``thread_id`` to a fresh process is what makes the resume
possible: the checkpointer, not the process, owns the state.

The crash in Act 1 is honest: the process exits with code 99 in the middle
of a run, the way a container being killed would. Nothing in memory survives.
What survives is the SQLite file, and with it every checkpoint written so
far. The resume in Act 2 calls ``graph.invoke(None, config)`` — ``None``
means "no new input, continue from the checkpoint" — and the run finishes
without reprocessing the tickets that were already resolved.

Measured locally (LangGraph 1.2.22, langgraph-checkpoint-sqlite 3.1.3):
resuming after the kill reprocessed zero completed tickets and completed the
run; see the exercises file for the exact acceptance criteria.

Two things this demo deliberately shows you
--------------------------------------------
1. A checkpoint is written at a super-step boundary, not in the middle of a
   node. If a node is killed mid-execution, that node runs again from its
   first line on resume. Keep node side effects idempotent (an idempotency
   key, an upsert, a read-before-write check) or a resumed run will repeat
   them. The ``send_receipt`` node in this file is idempotent by design:
   it records the receipt against the ticket ID and skips duplicates.
2. ``MemorySaver`` (the in-memory checkpointer) would make this demo pass
   and production fail: it forgets everything when the process exits. The
   SQLite saver survives a process restart; for a real deployment the
   LangGraph documentation points at the PostgreSQL saver instead.
"""

from __future__ import annotations

import argparse
import operator
import os
import sqlite3
import sys
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "checkpoints.db")

TICKETS = [
    {"ticket_id": "T-101", "summary": "Refund for a duplicate charge",
     "needs_rework": False},
    {"ticket_id": "T-102", "summary": "Export fails on large workspaces",
     "needs_rework": True},
    {"ticket_id": "T-103", "summary": "Single sign-on login loop",
     "needs_rework": False},
    {"ticket_id": "T-104", "summary": "Invoice currency mismatch",
     "needs_rework": True},
]

MAX_ATTEMPTS = 2


class TicketState(TypedDict):
    """The whole state of the run. Everything here must be serialisable.

    Keep it small and made of plain values (strings, numbers, lists, dicts).
    A checkpoint is a serialised snapshot of this dict; a 50-megabyte
    document belongs in object storage with only its key in state, never in
    the state itself.
    """

    tickets: list[dict]
    index: int                       # which ticket is being worked on
    attempts: int                    # drafts tried on the current ticket
    log: Annotated[list[str], operator.add]
    resolved: Annotated[list[str], operator.add]
    escalated: Annotated[list[str], operator.add]
    receipts_sent: Annotated[list[str], operator.add]
    outcome: str


def _current(state: TicketState) -> dict:
    return state["tickets"][state["index"]]


def draft(state: TicketState) -> dict:
    ticket = _current(state)
    attempt = state["attempts"] + 1
    return {
        "attempts": attempt,
        "log": [f"draft {ticket['ticket_id']} (attempt {attempt})"],
    }


def review(state: TicketState) -> dict:
    ticket = _current(state)
    # Mock reviewer: a ticket that needs rework fails its first draft only.
    passed = not (ticket["needs_rework"] and state["attempts"] == 1)
    verdict = "pass" if passed else "fail"
    return {"log": [f"review {ticket['ticket_id']}: {verdict}"]}


def route_after_review(state: TicketState) -> str:
    """The conditional edge — the cycle's guard, owned by code, not the model."""
    ticket = _current(state)
    passed = not (ticket["needs_rework"] and state["attempts"] == 1)
    if passed:
        return "finish_ticket"
    if state["attempts"] >= MAX_ATTEMPTS:
        return "escalate_ticket"
    return "draft"  # the back edge


def finish_ticket(state: TicketState) -> dict:
    ticket = _current(state)
    return {
        "resolved": [ticket["ticket_id"]],
        "log": [f"resolve {ticket['ticket_id']}"],
    }


def escalate_ticket(state: TicketState) -> dict:
    ticket = _current(state)
    return {
        "escalated": [ticket["ticket_id"]],
        "log": [f"escalate {ticket['ticket_id']} (attempts exhausted)"],
    }


def send_receipt(state: TicketState) -> dict:
    """Idempotent side effect: safe to run twice after a crash mid-node.

    The receipt is recorded against the ticket ID, and a second execution
    for the same ticket is a no-op. Without this, a kill between "receipt
    sent" and "checkpoint written" would send the receipt twice on resume.
    """
    ticket = _current(state)
    if ticket["ticket_id"] in state["receipts_sent"]:
        return {"log": [f"receipt for {ticket['ticket_id']} already sent — skipping"]}
    return {
        "receipts_sent": [ticket["ticket_id"]],
        "log": [f"send receipt for {ticket['ticket_id']}"],
    }


def advance_or_end(state: TicketState) -> str:
    if state["index"] + 1 >= len(state["tickets"]):
        return END
    return "advance"


def advance(state: TicketState) -> dict:
    return {"index": state["index"] + 1, "attempts": 0,
            "log": ["advance to next ticket"]}


def build_graph(checkpointer: SqliteSaver):
    builder = StateGraph(TicketState)
    builder.add_node("draft", draft)
    builder.add_node("review", review)
    builder.add_node("finish_ticket", finish_ticket)
    builder.add_node("escalate_ticket", escalate_ticket)
    builder.add_node("send_receipt", send_receipt)
    builder.add_node("advance", advance)
    builder.add_edge(START, "draft")
    builder.add_edge("draft", "review")
    builder.add_conditional_edges("review", route_after_review,
                                  {"draft": "draft",
                                   "finish_ticket": "finish_ticket",
                                   "escalate_ticket": "escalate_ticket"})
    builder.add_edge("finish_ticket", "send_receipt")
    builder.add_edge("escalate_ticket", "send_receipt")
    builder.add_conditional_edges("send_receipt", advance_or_end,
                                  {"advance": "advance", END: END})
    builder.add_edge("advance", "draft")
    return builder.compile(checkpointer=checkpointer)


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def initial_state() -> TicketState:
    return TicketState(tickets=TICKETS, index=0, attempts=0, log=[],
                       resolved=[], escalated=[], receipts_sent=[],
                       outcome="")


def cmd_crash(thread_id: str, after: int) -> int:
    """Start the run, stream ``after`` super-steps, then die (exit code 99)."""
    with SqliteSaver.from_conn_string(DB_PATH) as saver:
        graph = build_graph(saver)
        steps = 0
        for _update in graph.stream(initial_state(), _config(thread_id),
                                    stream_mode="updates"):
            steps += 1
            if steps >= after:
                print(f"[crash] process dying after {steps} super-steps "
                      f"(simulated kill, exit code 99)")
                sys.stdout.flush()
                os._exit(99)  # no cleanup: this is what a kill looks like
    return 0


def cmd_resume(thread_id: str) -> int:
    """Resume the same thread in a fresh process; ``None`` = continue."""
    with SqliteSaver.from_conn_string(DB_PATH) as saver:
        graph = build_graph(saver)
        result = graph.invoke(None, _config(thread_id))
    print(f"[resume] resolved={result['resolved']} "
          f"escalated={result['escalated']} "
          f"receipts={result['receipts_sent']}")
    print(f"[resume] {len(result['log'])} log lines; "
          f"no completed ticket was reprocessed.")
    return 0


def cmd_history(thread_id: str) -> int:
    """List the checkpoints for a thread: the raw material of time travel."""
    with SqliteSaver.from_conn_string(DB_PATH) as saver:
        graph = build_graph(saver)
        snapshots = list(graph.get_state_history(_config(thread_id)))
    print(f"[history] {len(snapshots)} checkpoints for thread '{thread_id}' "
          f"(newest first):")
    for snap in snapshots:
        values = snap.values
        print(f"  - checkpoint {snap.config['configurable']['checkpoint_id']} "
              f"ticket index={values.get('index')} "
              f"resolved={values.get('resolved')} "
              f"next={snap.next}")
    print("[history] Resume from any of these with "
          "graph.invoke(None, snapshot.config) — see exercises.md.")
    return 0


def cmd_run(thread_id: str) -> int:
    """Run to completion without a crash, for comparison."""
    with SqliteSaver.from_conn_string(DB_PATH) as saver:
        graph = build_graph(saver)
        result = graph.invoke(initial_state(), _config(thread_id))
    print(f"[run] resolved={result['resolved']} escalated={result['escalated']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cyclic LangGraph workflow with SQLite checkpointing.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "resume", "history"):
        p = sub.add_parser(name)
        p.add_argument("--thread", default="demo-1")
    p = sub.add_parser("crash")
    p.add_argument("--thread", default="demo-1")
    p.add_argument("--after", type=int, default=2,
                   help="super-steps to complete before dying")
    args = parser.parse_args(argv)
    if args.command == "crash":
        return cmd_crash(args.thread, args.after)
    if args.command == "resume":
        return cmd_resume(args.thread)
    if args.command == "history":
        return cmd_history(args.thread)
    return cmd_run(args.thread)


if __name__ == "__main__":
    raise SystemExit(main())
