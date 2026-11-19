"""
04 — The same triage job as a Temporal workflow (durable execution).

Two ways to run this file. Without a Temporal server — the default — it
runs a local replay simulator that demonstrates the durable-execution model
against a JSON event log, so the core ideas are testable with no
infrastructure:

    python 04_temporal_workflow.py simulate

With a Temporal server (``temporal server start-dev``) and the Python
software development kit (SDK) installed (``pip install temporalio``), the
same workflow definition runs for real:

    python 04_temporal_workflow.py worker    # in one terminal
    python 04_temporal_workflow.py start --ticket T-102   # in another

The workflow code is identical in both modes. That is the point of the
file: durable execution is a property of how the code is split, not of
which server you point it at.

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
  exactly that reason.
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

SIM_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "temporal_event_log.json")

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


if TEMPORAL_AVAILABLE:

    @activity.defn
    async def draft_reply(ticket_id: str, summary: str, attempt: int) -> DraftResult:
        # In production this calls a language model through Amazon Bedrock or
        # another provider. The model call lives here, in an activity, never
        # in workflow code: its result is recorded in the event history.
        return DraftResult(ticket_id, attempt,
                           f"draft v{attempt} for {ticket_id}: {summary}")

    @activity.defn
    async def review_draft(ticket_id: str, attempt: int,
                           needs_rework: bool) -> ReviewResult:
        # Mock reviewer: a ticket that needs rework fails its first draft.
        passed = not (needs_rework and attempt == 1)
        return ReviewResult(passed, "pass" if passed else "needs rework")

    @activity.defn
    async def send_receipt(ticket_id: str) -> str:
        # Idempotent by key: a retry for the same ticket is a no-op, so a
        # crash between "receipt sent" and "result recorded" cannot double
        # the side effect. In production this would be an upsert or an
        # idempotency-keyed call, not a bare send.
        activity.logger.info("receipt sent for %s (idempotent key)", ticket_id)
        return f"receipt:{ticket_id}"

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
                    args=[ticket_id, summary, attempt],
                    start_to_close_timeout=datetime.timedelta(seconds=60),
                )
                log.append(f"draft {draft.ticket_id} (attempt {draft.attempt})")
                verdict = await workflow.execute_activity(
                    review_draft,
                    args=[ticket_id, attempt, needs_rework],
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

def _mock_review(ticket_id: str, attempt: int, needs_rework: bool) -> bool:
    return not (needs_rework and attempt == 1)


def simulate(ticket_id: str = "T-102", needs_rework: bool = True) -> int:
    """Replay a JSON event log the way Temporal replays event history.

    Each activity result is appended to ``temporal_event_log.json`` *before*
    the workflow advances, mirroring Temporal recording the result in the
    event history. Run the simulation twice: the second run finds the
    recorded results and replays them instead of re-executing the activities
    — the crashed activities are never run again. Delete the log to start
    over.
    """
    log_path = SIM_LOG
    history: list[dict] = []
    if os.path.exists(log_path):
        history = json.loads(open(log_path).read())
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
        activity_result("draft_reply", f"{ticket_id}:{attempts}",
                        lambda: f"draft v{attempts} for {ticket_id}")
        passed = activity_result(
            "review_draft", f"{ticket_id}:{attempts}",
            lambda: _mock_review(ticket_id, attempts, needs_rework))
        if passed:
            activity_result("send_receipt", ticket_id,
                            lambda: f"receipt:{ticket_id}")
            print(f"[simulate] {ticket_id} resolved after {attempts} attempt(s)")
            return 0
    activity_result("send_receipt", ticket_id, lambda: f"receipt:{ticket_id}")
    print(f"[simulate] {ticket_id} escalated after {attempts} attempts")
    return 0


# ---------------------------------------------------------------------------
# Real Temporal client / worker entry points (need a server + ``temporalio``).
# ---------------------------------------------------------------------------

async def _run_worker() -> None:
    if not TEMPORAL_AVAILABLE:
        print("temporalio is not installed: pip install temporalio, then "
              "start a dev server with `temporal server start-dev`.")
        return 1
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
    sub.add_parser("simulate",
                   help="replay the durable-execution model from a JSON log")
    sub.add_parser("worker", help="run a real Temporal worker (needs server)")
    p = sub.add_parser("start", help="start a real workflow (needs server)")
    p.add_argument("--ticket", default="T-102")
    args = parser.parse_args(argv)
    if args.command == "simulate":
        return simulate()
    if args.command == "worker":
        return asyncio.run(_run_worker())
    return asyncio.run(_run_start(args.ticket))


if __name__ == "__main__":
    raise SystemExit(main())
