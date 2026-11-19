"""Durable pause and resume: an agent that stops before a write tool.

This is the chapter's core program. It models the smallest agent loop
that still has the production property that matters here: a write tool
guarded by a human-approval gate, where the pause between asking and
answering survives the process being killed.

The loop has three steps, and the checkpoint is written to SQLite
(see ``store.py``) *before* each step boundary, never after:

1. ``plan`` — a deterministic mock planner turns the task into a
   proposed write (no model call, so this runs with no API key).
2. ``request_approval`` — the proposed write is recorded as a pending
   approval with a deadline, the run status becomes
   ``awaiting_approval``, and the process *exits*. Nothing is held in
   memory. A signed webhook is dispatched to the approval endpoint
   (see ``approval_server.py``) when ``APPROVAL_WEBHOOK_URL`` is set.
3. ``resume`` — a later process, possibly after a restart, reloads the
   run from the database, reads the human's decision, and either
   executes the write (exactly once, guarded by an idempotency key)
   or abandons the run.

Fail-closed behaviour: if the approval deadline passes with no
decision, or the decision is anything other than an explicit
``approved``, the write does not happen. Silence is a rejection.

Usage::

    python durable_pause.py run --task "Publish the weekly changelog"
    python durable_pause.py status --run-id run-xxxxxxxxxxxx
    python durable_pause.py approve --run-id run-xxxxxxxxxxxx \\
        --decision approved --by approver@example.com --note "Looks right"
    python durable_pause.py resume --run-id run-xxxxxxxxxxxx
    python durable_pause.py audit --run-id run-xxxxxxxxxxxx

The ``approve`` command is a command-line stand-in for the webhook
decision endpoint in ``approval_server.py``; in production the human
answers in Slack or Microsoft Teams and the decision arrives over
Hypertext Transfer Protocol, never through a shell on the agent host.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import store

DEFAULT_APPROVAL_TIMEOUT_SECONDS = 15 * 60  # 15 minutes
WRITE_OUTPUT_DIR = Path(__file__).resolve().parent / "out"


def _now_iso(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(
        epoch, tz=datetime.timezone.utc
    ).isoformat()


def _sign_payload(secret: str, body: bytes) -> str:
    """Hash-based Message Authentication Code for a webhook payload.

    The same construction Slack uses for request verification (see
    ``slack_gate.verify_slack_signature``): ``v0=<hex digest>`` of the
    Hash-based Message Authentication Code using the SHA-256 hash
    function over ``v0:<timestamp>:<raw body>``.
    """
    timestamp = str(int(time.time()))
    base = f"v0:{timestamp}:".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return f"v0={digest}", timestamp


def dispatch_approval_webhook(
    conn, run_id: str, approval: dict, webhook_url: str
) -> dict:
    """POST the approval request to the routing endpoint, signed.

    Returns ``{"delivered": True}`` on a 2xx response, otherwise
    ``{"delivered": False, "error": ...}`` — and the failure is logged
    in the audit trail rather than silently dropped, because an
    approval nobody received is indistinguishable from an approval
    nobody answered. Delivery failure does *not* un-pause the run:
    the pending approval still exists in the store, so a human can
    still decide through the command-line interface or the decision
    endpoint.
    """
    secret = os.environ.get("HITL_WEBHOOK_SECRET", "dev-only-change-me")
    payload = {
        "type": "approval_requested",
        "run_id": run_id,
        "approval": approval,
    }
    body = json.dumps(payload).encode("utf-8")
    signature, timestamp = _sign_payload(secret, body)
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-HITL-Signature": signature,
            "X-HITL-Timestamp": timestamp,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
        store.append_event(
            conn, run_id, "approval_webhook_delivered", "agent",
            {"webhook_url": webhook_url},
        )
        return {"delivered": True}
    except Exception as exc:  # noqa: BLE001 — delivery failure is data
        store.append_event(
            conn, run_id, "approval_webhook_failed", "agent",
            {"webhook_url": webhook_url, "error": str(exc)},
        )
        return {"delivered": False, "error": str(exc)}


def plan_write(task: str) -> dict:
    """Deterministic mock planner: task text in, proposed write out.

    A real agent would call a language model here; the planning logic
    is deliberately trivial so the pause, routing and audit behaviour
    — the subject of this chapter — can be exercised with no model
    and no API key. The proposed write is always a file write, which
    is the write tool this chapter gates.
    """
    filename = "changelog.md" if "changelog" in task.lower() else "output.md"
    return {
        "tool": "write_file",
        "risk": "high",  # every write through this gate is high risk
        "path": filename,
        "content": f"# Agent output\n\nTask: {task}\n",
        "idempotency_key": hashlib.sha256(task.encode("utf-8")).hexdigest()[:16],
    }


def run_agent(
    conn, task: str, *, approver: str, timeout_seconds: int
) -> dict:
    """Plan, then pause durably before the write. Returns the run."""
    state = {"step": "planned", "task": task, "write": plan_write(task)}
    run_id = store.create_run(conn, task, state)
    now = time.time()
    approval = {
        "approval_id": f"appr-{run_id}",
        "run_id": run_id,
        "tool": state["write"]["tool"],
        "path": state["write"]["path"],
        "content_preview": state["write"]["content"][:200],
        "requested_by": "agent",
        "approver": approver,
        "requested_at": _now_iso(now),
        "expires_at": _now_iso(now + timeout_seconds),
        "expires_epoch": now + timeout_seconds,
        "decision": None,
        "decided_by": None,
        "decided_at": None,
    }
    state["step"] = "awaiting_approval"
    state["approval"] = approval
    # Checkpoint FIRST, then announce: if the process dies between
    # these two lines, the run is still resumable from the store.
    store.update_run(conn, run_id, status="awaiting_approval", state=state)
    store.append_event(
        conn, run_id, "approval_requested", "agent",
        {"approval_id": approval["approval_id"], "approver": approver,
         "tool": approval["tool"], "path": approval["path"],
         "expires_at": approval["expires_at"]},
    )
    webhook_url = os.environ.get("APPROVAL_WEBHOOK_URL")
    delivery = None
    if webhook_url:
        delivery = dispatch_approval_webhook(conn, run_id, approval, webhook_url)
    return {"run_id": run_id, "status": "awaiting_approval",
            "approval": approval, "webhook_delivery": delivery}


def record_decision(
    conn, run_id: str, *, decision: str, decided_by: str, note: str = ""
) -> dict:
    """Record a human decision against a pending approval.

    Idempotent on a repeated identical decision, and refuses a second,
    different decision: the first decision wins, because letting a
    later click silently overwrite an earlier one would make the audit
    trail lie about what the agent acted on. A late decision — after
    ``expires_at`` — is recorded as ``expired`` and treated as a
    rejection (fail closed).
    """
    run = store.get_run(conn, run_id)
    if run is None:
        raise KeyError(f"unknown run_id: {run_id}")
    approval = run["state"].get("approval")
    if approval is None:
        raise ValueError(f"run {run_id} has no pending approval")
    if approval["decision"] is not None:
        if approval["decision"] == decision and approval["decided_by"] == decided_by:
            return {"run_id": run_id, "decision": decision, "duplicate": True}
        raise ValueError(
            f"run {run_id} was already decided: "
            f"{approval['decision']} by {approval['decided_by']}"
        )
    now = time.time()
    effective = decision
    if now > approval["expires_epoch"]:
        effective = "expired"
    approval["decision"] = effective
    approval["decided_by"] = decided_by
    approval["decided_at"] = _now_iso(now)
    approval["note"] = note
    run["state"]["approval"] = approval
    store.update_run(
        conn, run_id,
        status="approved" if effective == "approved" else "rejected",
        state=run["state"],
    )
    store.append_event(
        conn, run_id, "approval_decided", decided_by,
        {"decision": effective, "requested_decision": decision,
         "note": note, "approval_id": approval["approval_id"]},
    )
    return {"run_id": run_id, "decision": effective, "duplicate": False}


def execute_write(conn, run_id: str) -> dict:
    """Perform the gated write exactly once, then mark the run done.

    The idempotency key is the run identifier itself: the output file
    carries a marker line with the run identifier, and if a file with
    that marker already exists the write is skipped and reported as
    already applied. This is what makes a resumed run safe to retry —
    resuming twice must never publish twice.
    """
    run = store.get_run(conn, run_id)
    if run is None:
        raise KeyError(f"unknown run_id: {run_id}")
    write = run["state"]["write"]
    WRITE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = WRITE_OUTPUT_DIR / write["path"]
    marker = f"<!-- run_id: {run_id} -->\n"
    if target.exists() and marker.strip() in target.read_text(encoding="utf-8"):
        return {"run_id": run_id, "path": str(target), "applied": False,
                "reason": "already applied (idempotency marker present)"}
    target.write_text(marker + write["content"], encoding="utf-8")
    store.append_event(
        conn, run_id, "write_executed", "agent",
        {"tool": write["tool"], "path": str(target),
         "idempotency_key": write["idempotency_key"]},
    )
    return {"run_id": run_id, "path": str(target), "applied": True}


def resume_agent(conn, run_id: str) -> dict:
    """Resume a paused run: decide from the stored decision, then act.

    This is a fresh process's view of the run — it reads everything
    from the database, so it works after the original process has
    exited or the machine has restarted. The decision is checked
    twice: once here, and once inside ``record_decision`` at decision
    time, so a run can never execute a write on a stale or missing
    approval.
    """
    run = store.get_run(conn, run_id)
    if run is None:
        raise KeyError(f"unknown run_id: {run_id}")
    approval = run["state"].get("approval") or {}
    decision = approval.get("decision")
    if run["status"] == "completed":
        return {"run_id": run_id, "status": "completed",
                "outcome": "already completed"}
    if decision is None:
        # No decision yet — but check whether the deadline has passed,
        # so a run left waiting forever fails closed on resume too.
        if time.time() > approval.get("expires_epoch", 0):
            record_decision(conn, run_id, decision="approved",
                            decided_by="system-timeout",
                            note="No decision before the deadline.")
            return {"run_id": run_id, "status": "rejected",
                    "outcome": "approval timed out; write not performed"}
        return {"run_id": run_id, "status": run["status"],
                "outcome": "still awaiting approval"}
    if decision != "approved":
        store.update_run(conn, run_id, status="rejected")
        store.append_event(
            conn, run_id, "run_abandoned", "agent",
            {"reason": f"approval decision was {decision!r}"},
        )
        return {"run_id": run_id, "status": "rejected",
                "outcome": f"approval decision was {decision!r};"
                           " write not performed"}
    result = execute_write(conn, run_id)
    store.update_run(conn, run_id, status="completed")
    store.append_event(
        conn, run_id, "run_completed", "agent",
        {"write_path": result["path"], "applied": result["applied"]},
    )
    return {"run_id": run_id, "status": "completed",
            "outcome": "write executed", "write": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Durably pausing agent: pause before a write tool,"
                    " resume after a human approves."
    )
    parser.add_argument("--db", default=str(store.default_db_path()),
                        help="SQLite store path (default: code/hitl.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Plan a task, then pause for approval")
    p_run.add_argument("--task", required=True)
    p_run.add_argument("--approver", default="approver@example.com")
    p_run.add_argument("--timeout-seconds", type=int,
                       default=DEFAULT_APPROVAL_TIMEOUT_SECONDS)

    p_approve = sub.add_parser("approve", help="Record a human decision")
    p_approve.add_argument("--run-id", required=True)
    p_approve.add_argument("--decision", required=True,
                           choices=["approved", "rejected"])
    p_approve.add_argument("--by", required=True)
    p_approve.add_argument("--note", default="")

    p_resume = sub.add_parser("resume", help="Resume a paused run")
    p_resume.add_argument("--run-id", required=True)

    p_status = sub.add_parser("status", help="Show a run's checkpointed state")
    p_status.add_argument("--run-id", required=True)

    p_audit = sub.add_parser("audit", help="Show a run's audit trail")
    p_audit.add_argument("--run-id", required=True)

    args = parser.parse_args(argv)
    conn = store.connect(args.db)

    if args.command == "run":
        result = run_agent(conn, args.task, approver=args.approver,
                           timeout_seconds=args.timeout_seconds)
        print(json.dumps(result, indent=2))
        print("\nThe agent is paused. The process can now exit safely —")
        print("the run will resume from the database, not from memory.")
        return 0

    if args.command == "approve":
        try:
            result = record_decision(conn, args.run_id, decision=args.decision,
                                     decided_by=args.by, note=args.note)
        except (KeyError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "resume":
        try:
            result = resume_agent(conn, args.run_id)
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "status":
        run = store.get_run(conn, args.run_id)
        if run is None:
            print(f"error: unknown run_id: {args.run_id}", file=sys.stderr)
            return 1
        print(json.dumps(run, indent=2, default=str))
        return 0

    if args.command == "audit":
        events = store.list_events(conn, args.run_id)
        chain = store.verify_chain(conn)
        for event in events:
            print(f"#{event['seq']:>3} {_now_iso(event['created_at'])}"
                  f" {event['event_type']:<28} actor={event['actor']}"
                  f" {json.dumps(event['payload'], sort_keys=True)}")
        print(f"\nHash chain intact for the whole log: {chain['ok']}"
              f" ({chain['events_checked']} events checked)")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
