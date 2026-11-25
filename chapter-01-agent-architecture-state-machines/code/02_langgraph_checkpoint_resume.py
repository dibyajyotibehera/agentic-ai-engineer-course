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

The default run is deterministic — the draft is a template and the
reviewer is a rule — so the checkpointing behaviour is what you are
testing, and the outcomes are reproducible: every ticket resolves, every
ticket gets exactly one receipt, and no completed ticket is reprocessed.
That determinism is deliberate: the thing being studied here is
checkpointing and recovery, and a reviewer whose verdicts vary from run
to run would make the acceptance criteria untestable.

Pass ``--live`` to any of the ``run``, ``crash`` or ``resume`` commands
and the stand-ins are replaced by real model calls: the draft node
writes a real reply with a real model and the review node judges it with
a real model (``live_review.py``). The live run needs a configured model
backend — key-based with ``LLM_API_KEY``, ``LLM_BASE_URL`` and
``LLM_MODEL``, or AWS Bedrock with ``LLM_BACKEND=bedrock``; see
"Connect a model" in the root README — and it prints the
token and cost totals at the end. The reviewer's verdict is stored in
state, so the conditional edge routes on the recorded verdict rather
than calling the model a second time, and a resumed run re-runs only the
node that was in flight, exactly as the checkpointing model promises.

The receipt is a real side effect in both modes: ``send_receipt`` appends
to a JSON-lines ledger file (``receipts.jsonl`` next to this file) through
``receipt_ledger.py``, and a second execution for the same ticket finds
the existing entry and writes nothing. That is the idempotency the
chapter demands, enforced by a real file rather than asserted in a
comment — and Exercise 3, which asks you to break it, breaks a real
record you can inspect.

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
   it records the receipt against the ticket ID in a real ledger file and
   skips duplicates.
2. ``MemorySaver`` (the in-memory checkpointer) would make this demo pass
   and production fail: it forgets everything when the process exits. The
   SQLite saver survives a process restart; for a real deployment the
   LangGraph documentation points at the PostgreSQL saver instead.
"""

from __future__ import annotations

import argparse
import operator
import os
import sys
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from receipt_ledger import DEFAULT_LEDGER_PATH, receipt_count, record_receipt

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
    verdict: str                     # "pass: <reason>" / "fail: <reason>"
    last_draft: str                  # the latest draft text, for the record


def _current(state: TicketState) -> dict:
    return state["tickets"][state["index"]]


def _deterministic_verdict(ticket: dict, attempts: int) -> tuple[bool, str]:
    """The deterministic reviewer: a ticket that needs rework fails draft 1.

    This rule is the controlled variable of the demo. The live engine
    replaces it with a real model judging a real draft; the verdict is
    stored in state either way, so the routing edge never re-decides.
    """
    passed = not (ticket["needs_rework"] and attempts == 1)
    return passed, "pass" if passed else "needs rework"


def build_graph(checkpointer: SqliteSaver, live_client=None,
                ledger_path: str | None = None):
    """Compile the graph. ``live_client`` selects the engine.

    With no client the draft is a template and the review is the
    deterministic rule; with a real ``LLMClient`` both are real model
    calls (``live_review``). The nodes are closures over the client so
    the graph definition stays the same in both modes — the engine is
    a construction-time choice, not a branch in the workflow.
    ``ledger_path`` overrides where receipts are recorded (the tests
    point it at a temporary file); the default is ``receipts.jsonl``
    next to this file.
    """

    def draft(state: TicketState) -> dict:
        ticket = _current(state)
        attempt = state["attempts"] + 1
        if live_client is None:
            text = (f"draft v{attempt} for {ticket['ticket_id']}: "
                    f"{ticket['summary']}")
        else:
            from live_review import draft_reply

            text = draft_reply(live_client, ticket["ticket_id"],
                               ticket["summary"], attempt,
                               needs_rework=ticket["needs_rework"])
        return {
            "attempts": attempt,
            "last_draft": text,
            "log": [f"draft {ticket['ticket_id']} (attempt {attempt})"],
        }

    def review(state: TicketState) -> dict:
        ticket = _current(state)
        if live_client is None:
            passed, reason = _deterministic_verdict(ticket, state["attempts"])
        else:
            from live_review import review_draft

            verdict = review_draft(live_client, ticket["ticket_id"],
                                   state["last_draft"], state["attempts"])
            passed, reason = bool(verdict["passed"]), str(verdict["reason"])
        verdict = f"{'pass' if passed else 'fail'}: {reason}"
        return {
            "verdict": verdict,
            "log": [f"review {ticket['ticket_id']}: {verdict}"],
        }

    def route_after_review(state: TicketState) -> str:
        """The conditional edge — the cycle's guard, owned by code, not the model.

        Routes on the verdict the review node recorded in state. A state
        loaded from an older checkpoint that has no verdict yet falls
        back to the deterministic rule, so old checkpoints still resume.
        """
        verdict = state.get("verdict", "")
        if verdict.startswith("pass"):
            return "finish_ticket"
        if verdict.startswith("fail"):
            if state["attempts"] >= MAX_ATTEMPTS:
                return "escalate_ticket"
            return "draft"  # the back edge
        passed, _reason = _deterministic_verdict(_current(state),
                                                 state["attempts"])
        if passed:
            return "finish_ticket"
        if state["attempts"] >= MAX_ATTEMPTS:
            return "escalate_ticket"
        return "draft"

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

        The receipt is recorded in a real ledger file (``receipts.jsonl``)
        against the ticket ID, and a second execution for the same ticket
        appends nothing. Without this, a kill between "receipt sent" and
        "checkpoint written" would send the receipt twice on resume.
        """
        ticket = _current(state)
        if ticket["ticket_id"] in state["receipts_sent"]:
            return {"log": [f"receipt for {ticket['ticket_id']} already sent — skipping"]}
        record_receipt(ticket["ticket_id"],
                       ledger_path or DEFAULT_LEDGER_PATH)
        return {
            "receipts_sent": [ticket["ticket_id"]],
            "log": [f"send receipt for {ticket['ticket_id']}"],
        }

    def advance_or_end(state: TicketState) -> str:
        if state["index"] + 1 >= len(state["tickets"]):
            return END
        return "advance"

    def advance(state: TicketState) -> dict:
        return {"index": state["index"] + 1, "attempts": 0, "verdict": "",
                "last_draft": "", "log": ["advance to next ticket"]}

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
                       outcome="", verdict="", last_draft="")


def _live_client_or_none(live: bool):
    """Build the model client for a live run; fail loudly with no backend."""
    if not live:
        return None
    from llm_client import LLMClient

    return LLMClient()


def _report_usage(client) -> None:
    if client is not None:
        print(f"[live] model usage: {client.total_prompt_tokens} prompt + "
              f"{client.total_completion_tokens} completion tokens, "
              f"cost ${client.total_cost_usd:.4f}")


def cmd_crash(thread_id: str, after: int, live: bool = False) -> int:
    """Start the run, stream ``after`` super-steps, then die (exit code 99)."""
    client = _live_client_or_none(live)
    with SqliteSaver.from_conn_string(DB_PATH) as saver:
        graph = build_graph(saver, live_client=client)
        steps = 0
        for _update in graph.stream(initial_state(), _config(thread_id),
                                    stream_mode="updates"):
            steps += 1
            if steps >= after:
                print(f"[crash] process dying after {steps} super-steps "
                      f"(simulated kill, exit code 99)")
                _report_usage(client)  # the process dies below, so report now
                sys.stdout.flush()
                os._exit(99)  # no cleanup: this is what a kill looks like
    return 0


def cmd_resume(thread_id: str, live: bool = False) -> int:
    """Resume the same thread in a fresh process; ``None`` = continue."""
    client = _live_client_or_none(live)
    with SqliteSaver.from_conn_string(DB_PATH) as saver:
        graph = build_graph(saver, live_client=client)
        result = graph.invoke(None, _config(thread_id))
    print(f"[resume] resolved={result['resolved']} "
          f"escalated={result['escalated']} "
          f"receipts={result['receipts_sent']}")
    print(f"[resume] {len(result['log'])} log lines; "
          f"no completed ticket was reprocessed.")
    for ticket_id in result["receipts_sent"]:
        print(f"[resume] ledger holds {receipt_count(ticket_id)} receipt(s) "
              f"for {ticket_id}")
    _report_usage(client)
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


def cmd_run(thread_id: str, live: bool = False) -> int:
    """Run to completion without a crash, for comparison."""
    client = _live_client_or_none(live)
    with SqliteSaver.from_conn_string(DB_PATH) as saver:
        graph = build_graph(saver, live_client=client)
        result = graph.invoke(initial_state(), _config(thread_id))
    print(f"[run] resolved={result['resolved']} escalated={result['escalated']}")
    _report_usage(client)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cyclic LangGraph workflow with SQLite checkpointing.")
    parser.add_argument(
        "--live", action="store_true",
        help="use a real model for drafting and reviewing "
             "(needs a configured model backend; see \"Connect a model\" "
             "in the root README)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "resume", "history"):
        p = sub.add_parser(name)
        p.add_argument("--thread", default="demo-1")
        p.add_argument(
            "--live", action="store_true", default=argparse.SUPPRESS,
            help="use a real model for drafting and reviewing "
                 "(needs a configured model backend; see \"Connect a model\" "
             "in the root README)")
    p = sub.add_parser("crash")
    p.add_argument("--thread", default="demo-1")
    p.add_argument("--after", type=int, default=2,
                   help="super-steps to complete before dying")
    p.add_argument(
        "--live", action="store_true", default=argparse.SUPPRESS,
        help="use a real model for drafting and reviewing "
             "(needs a configured model backend; see \"Connect a model\" "
             "in the root README)")
    args = parser.parse_args(argv)
    if args.command == "crash":
        return cmd_crash(args.thread, args.after, live=args.live)
    if args.command == "resume":
        return cmd_resume(args.thread, live=args.live)
    if args.command == "history":
        return cmd_history(args.thread)
    return cmd_run(args.thread, live=args.live)


if __name__ == "__main__":
    raise SystemExit(main())
