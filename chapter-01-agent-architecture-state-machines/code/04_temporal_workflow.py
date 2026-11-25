"""
04 — The same triage job as a Temporal workflow (durable execution).

Two ways to run this file. Without a Temporal server — the default — it
runs a local replay simulator that demonstrates the durable-execution model
against a JSON event log, so the core ideas are testable with no
infrastructure:

    python 04_temporal_workflow.py simulate
    python 04_temporal_workflow.py simulate --live   # real model calls

With a Temporal server (``temporal server start-dev``) and the Python
software development kit (SDK) installed (``pip install temporalio``), the
same workflow definition runs for real:

    python 04_temporal_workflow.py worker    # in one terminal
    python 04_temporal_workflow.py start --ticket T-102   # in another

The workflow code is identical in both modes. That is the point of the
file: durable execution is a property of how the code is split, not of
which server you point it at.

Drafting and reviewing are real model calls in both modes. The default
simulator run is deterministic — the draft is a template and the review is
a rule — because the simulator's purpose is to demonstrate the replay
model, and a replay demonstration needs the same activity results on
every run. Pass ``--live`` and the simulator calls a real model for the
draft and the review instead, recording the results in the event log;
run it a second time and the recorded results are replayed with no new
model calls at all, which is exactly the property Temporal sells. The
``--live`` run needs a configured model backend — key-based with
``LLM_API_KEY``, ``LLM_BASE_URL`` and ``LLM_MODEL``, or AWS Bedrock
with ``LLM_BACKEND=bedrock``; see "Connect a model" in the root
README. The real worker path always uses
the real model and fails loudly at startup if no backend is configured,
because a worker that silently fell back to template drafts would be
pretending to do the job.

The receipt is a real side effect in both modes, not a log line: it is
appended to a JSON-lines ledger file (``receipts.jsonl`` next to this
file), keyed by ticket ID, and a second execution for the same ticket
finds the existing entry and writes nothing. That is the idempotency the
chapter demands, demonstrated against a real file rather than asserted
in a comment.

The split, and why it matters
-----------------------------
* The *workflow* (``TriageWorkflow.run``) is pure orchestration: loops,
  conditionals, and calls to activities. It must be deterministic — given
  the same input and the same recorded history, it must issue the same
  sequence of commands — because Temporal rebuilds its state by *replaying*
  the event history through this code after a crash. Anything that would
  make two replays diverge is forbidden here: the wall clock, random
  numbers, direct network or database calls. The Python SDK replaces those
  with replay-safe equivalents (``workflow.now()``, ``workflow.random()``,
  ``workflow.uuid4()``); this file uses ``workflow.now()`` for its
  timestamps.
* The *activities* (``draft_reply``, ``review_draft``, ``send_receipt``)
  are where the outside world is touched: model calls, database writes,
  emails. Their results are recorded in the event history, so on replay
  Temporal returns the recorded result instead of running the activity
  again. An activity that charged a card therefore runs exactly once per
  recorded result — but only if the activity itself is idempotent across
  *retries*, because Temporal retries a failed activity, and a retry is a
  fresh execution. The receipt activity here is keyed by ticket ID for
  exactly that reason, and the key is enforced against a real ledger file.
* The *worker* is the process that runs workflow and activity code. It
  holds no durable state of its own: kill it, start another, and the
  Temporal service hands the replacement the event history to replay.

What Temporal buys you that a checkpointer does not
----------------------------------------------------
A LangGraph checkpointer snapshots state at super-step boundaries; the node
that was running when the process died runs again from its first line, and
any work it did before the checkpoint is lost unless the node was
idempotent. Temporal records every activity result in the event history, so
a crashed activity is never re-run at all — the replay returns its recorded
result — and a workflow can sleep for months on a durable timer without
holding a process open. The price is the determinism contract on workflow
code, the event-history limits (see the lesson README.md), and an extra
service to operate. Chapter 1's decision framework is about judging
whether that price is worth paying for a given job.
"""

from __future__ import annotations

import argparse
import datetime
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from receipt_ledger import DEFAULT_LEDGER_PATH, receipt_count, record_receipt

SIM_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "temporal_event_log.json")
RECEIPT_LOG = DEFAULT_LEDGER_PATH

MAX_ATTEMPTS = 2

try:
    from temporalio import activity, workflow
    from temporalio.client import Client
    from temporalio.worker import Worker

    TEMPORAL_AVAILABLE = True
except ImportError:  # pragma: no cover - the simulator path needs no SDK
    TEMPORAL_AVAILABLE = False
    activity = workflow = Client = Worker = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Activities: the only place the outside world is touched.
#
# The implementations below are plain functions that do not import
# Temporal at all, so the simulator and the tests can call them directly.
# The ``@activity.defn`` wrappers in the Temporal section delegate to
# them. ``client=None`` means the deterministic rule (template draft,
# rule reviewer); pass a real ``LLMClient`` and the same function makes
# real model calls instead.
# ---------------------------------------------------------------------------

@dataclass
class DraftResult:
    ticket_id: str
    attempt: int
    text: str


@dataclass
class ReviewResult:
    passed: bool
    reason: str


def draft_reply_sync(ticket_id: str, summary: str, attempt: int,
                     client=None, needs_rework: bool = True) -> DraftResult:
    """Write the reply draft: a real model call when a client is given.

    The deterministic fallback is a template, used by the default
    simulator run so the replay demonstration is reproducible. It is
    not a model: it contains no judgement about the ticket, only the
    ticket's own words.
    """
    if client is None:
        return DraftResult(ticket_id, attempt,
                           f"draft v{attempt} for {ticket_id}: {summary}")
    from live_review import draft_reply

    text = draft_reply(client, ticket_id, summary, attempt,
                       needs_rework=needs_rework)
    return DraftResult(ticket_id, attempt, text)


def review_draft_sync(ticket_id: str, attempt: int, needs_rework: bool,
                      draft_text: str = "", client=None) -> ReviewResult:
    """Judge the draft: a real model verdict when a client is given.

    The deterministic fallback is the rule the chapter's other demos
    use — a ticket that needs rework fails its first draft only — so
    the simulator's outcomes are reproducible. With a client, the
    verdict comes from ``live_review.review_draft``, which judges
    whether the draft gives the customer something concrete.
    """
    if client is None:
        passed = not (needs_rework and attempt == 1)
        return ReviewResult(passed, "pass" if passed else "needs rework")
    from live_review import review_draft

    verdict = review_draft(client, ticket_id, draft_text, attempt)
    return ReviewResult(bool(verdict["passed"]), str(verdict["reason"]))


def send_receipt_sync(ticket_id: str,
                      ledger_path: str = RECEIPT_LOG) -> str:
    """Record the receipt in the real JSON-lines ledger, idempotently.

    The ledger is a file, not a dict: the receipt survives the process
    that wrote it, which is the whole point of a side effect. A second
    call for the same ticket finds the existing entry and appends
    nothing, so a retry — or a fresh run after the event log was
    deleted — cannot double the side effect. See ``receipt_ledger.py``.
    """
    return record_receipt(ticket_id, ledger_path)


# ---------------------------------------------------------------------------
# Temporal activity wrappers: thin delegates over the implementations.
# ---------------------------------------------------------------------------

_LIVE_CLIENT = None


def _live_client():
    """The worker's model client, built once. Fails loudly without a backend.

    The real worker path never falls back to template drafts: a worker
    that silently pretended to draft would be worse than one that
    refuses to start.
    """
    global _LIVE_CLIENT
    if _LIVE_CLIENT is None:
        from llm_client import LLMClient

        _LIVE_CLIENT = LLMClient()
    return _LIVE_CLIENT


if TEMPORAL_AVAILABLE:

    @activity.defn
    async def draft_reply(ticket_id: str, summary: str,
                          attempt: int, needs_rework: bool) -> DraftResult:
        # The model call lives here, in an activity, never in workflow
        # code: its result is recorded in the event history.
        return await asyncio.to_thread(
            draft_reply_sync, ticket_id, summary, attempt, _live_client(),
            needs_rework)

    @activity.defn
    async def review_draft(ticket_id: str, attempt: int,
                           needs_rework: bool,
                           draft_text: str) -> ReviewResult:
        return await asyncio.to_thread(
            review_draft_sync, ticket_id, attempt, needs_rework,
            draft_text, _live_client())

    @activity.defn
    async def send_receipt(ticket_id: str) -> str:
        # Idempotent by key against a real ledger file: a retry for the
        # same ticket is a no-op, so a crash between "receipt sent" and
        # "result recorded" cannot double the side effect.
        receipt = await asyncio.to_thread(send_receipt_sync, ticket_id)
        activity.logger.info("receipt recorded for %s (idempotent key)",
                             ticket_id)
        return receipt

    @workflow.defn
    class TriageWorkflow:
        """Deterministic orchestration only. No I/O, no clock, no randomness.

        Every branch here is a function of the workflow input and of activity
        results already recorded in the event history, which is what makes
        replay reconstruct exactly this execution after a crash.
        """

        @workflow.run
        async def run(self, ticket_id: str, summary: str,
                      needs_rework: bool) -> dict:
            log: list[str] = []
            attempt = 0
            while attempt < MAX_ATTEMPTS:
                attempt += 1
                draft = await workflow.execute_activity(
                    draft_reply,
                    args=[ticket_id, summary, attempt, needs_rework],
                    start_to_close_timeout=datetime.timedelta(seconds=60),
                )
                log.append(f"draft {draft.ticket_id} (attempt {draft.attempt})")
                verdict = await workflow.execute_activity(
                    review_draft,
                    args=[ticket_id, attempt, needs_rework, draft.text],
                    start_to_close_timeout=datetime.timedelta(seconds=60),
                )
                log.append(f"review {ticket_id}: {verdict.reason}")
                if verdict.passed:
                    await workflow.execute_activity(
                        send_receipt, args=[ticket_id],
                        start_to_close_timeout=datetime.timedelta(seconds=60),
                    )
                    return {"ticket_id": ticket_id, "status": "resolved",
                            "attempts": attempt,
                            "finished_at": workflow.now().isoformat(),
                            "log": log}
            await workflow.execute_activity(
                send_receipt, args=[ticket_id],
                start_to_close_timeout=datetime.timedelta(seconds=60),
            )
            return {"ticket_id": ticket_id, "status": "escalated",
                    "attempts": attempt,
                    "finished_at": workflow.now().isoformat(), "log": log}


# ---------------------------------------------------------------------------
# Local simulator: the durable-execution model with no server.
# ---------------------------------------------------------------------------

def simulate(ticket_id: str = "T-102", needs_rework: bool = True,
             live_client=None, log_path: str = SIM_LOG,
             ledger_path: str = RECEIPT_LOG) -> int:
    """Replay a JSON event log the way Temporal replays event history.

    Each activity result is appended to the event log *before* the
    workflow advances, mirroring Temporal recording the result in the
    event history. Run the simulation twice: the second run finds the
    recorded results and replays them instead of re-executing the
    activities — the crashed activities are never run again. Delete the
    log to start over.

    With ``live_client`` given, the draft and review activities are real
    model calls on the first run, and the second run replays their
    recorded results with no new model calls. Without it, the activities
    are the deterministic template and rule, so the demonstration is
    reproducible with no key.
    """
    history: list[dict] = []
    if os.path.exists(log_path):
        history = json.loads(Path(log_path).read_text())
        print(f"[simulate] replaying {len(history)} recorded events "
              f"from {os.path.basename(log_path)}")
    else:
        print("[simulate] no event log yet — fresh execution")

    def record(event: dict) -> None:
        history.append(event)
        with open(log_path, "w") as fh:
            json.dump(history, fh, indent=2)

    def activity_result(name: str, key: str, compute):
        for event in history:
            if event["activity"] == name and event["key"] == key:
                print(f"[simulate] replay: {name}({key}) -> recorded result, "
                      f"not re-executed")
                return event["result"]
        result = compute()
        record({"activity": name, "key": key, "result": result})
        print(f"[simulate] executed: {name}({key}) -> {result}")
        return result

    attempts = 0
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        draft_text = activity_result(
            "draft_reply", f"{ticket_id}:{attempts}",
            lambda: asdict(draft_reply_sync(
                ticket_id, f"ticket {ticket_id}", attempts, live_client,
                needs_rework)))["text"]
        passed = activity_result(
            "review_draft", f"{ticket_id}:{attempts}",
            lambda: asdict(review_draft_sync(
                ticket_id, attempts, needs_rework, draft_text,
                live_client)))["passed"]
        if passed:
            activity_result("send_receipt", ticket_id,
                            lambda: send_receipt_sync(ticket_id, ledger_path))
            print(f"[simulate] {ticket_id} resolved after {attempts} attempt(s)")
            print(f"[simulate] ledger holds "
                  f"{receipt_count(ticket_id, ledger_path)} receipt(s) for "
                  f"{ticket_id}")
            return 0
    activity_result("send_receipt", ticket_id,
                    lambda: send_receipt_sync(ticket_id, ledger_path))
    print(f"[simulate] {ticket_id} escalated after {attempts} attempts")
    print(f"[simulate] ledger holds "
          f"{receipt_count(ticket_id, ledger_path)} receipt(s) for {ticket_id}")
    return 0


# ---------------------------------------------------------------------------
# Real Temporal client / worker entry points (need a server + ``temporalio``).
# ---------------------------------------------------------------------------

async def _run_worker() -> None:
    if not TEMPORAL_AVAILABLE:
        print("temporalio is not installed: pip install temporalio, then "
              "start a dev server with `temporal server start-dev`.")
        return 1
    _live_client()  # fail fast if no model backend is configured
    client = await Client.connect("localhost:7233")
    worker = Worker(client, task_queue="triage-queue",
                    workflows=[TriageWorkflow],
                    activities=[draft_reply, review_draft, send_receipt])
    print("worker polling task queue 'triage-queue' (Ctrl-C to stop)")
    await worker.run()
    return 0


async def _run_start(ticket_id: str) -> None:
    if not TEMPORAL_AVAILABLE:
        print("temporalio is not installed: pip install temporalio, then "
              "start a dev server with `temporal server start-dev`.")
        return 1
    client = await Client.connect("localhost:7233")
    needs_rework = ticket_id in {"T-102", "T-104"}
    handle = await client.start_workflow(
        TriageWorkflow.run,
        args=[ticket_id, f"ticket {ticket_id}", needs_rework],
        id=f"triage-{ticket_id}", task_queue="triage-queue",
    )
    print(f"started workflow {handle.id}; waiting for result …")
    print(await handle.result())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Triage as a Temporal workflow (or a local simulator).")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("simulate",
                       help="replay the durable-execution model from a JSON log")
    p.add_argument("--live", action="store_true",
                   help="use a real model for drafting and reviewing "
                        "(needs a configured model backend; see "
                        "\"Connect a model\" in the root README)")
    sub.add_parser("worker", help="run a real Temporal worker (needs server)")
    p = sub.add_parser("start", help="start a real workflow (needs server)")
    p.add_argument("--ticket", default="T-102")
    args = parser.parse_args(argv)
    if args.command == "simulate":
        live_client = None
        if args.live:
            from llm_client import LLMClient

            live_client = LLMClient()  # raises loudly if no backend
        result = simulate(live_client=live_client)
        if live_client is not None:
            print(f"\n[live] model usage: "
                  f"{live_client.total_prompt_tokens} prompt + "
                  f"{live_client.total_completion_tokens} completion tokens, "
                  f"cost ${live_client.total_cost_usd:.4f}")
        return result
    if args.command == "worker":
        return asyncio.run(_run_worker())
    return asyncio.run(_run_start(args.ticket))


if __name__ == "__main__":
    raise SystemExit(main())
