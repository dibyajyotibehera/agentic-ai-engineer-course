"""Audit-trail reconstruction: prove what happened from the log alone.

This module answers the question the chapter's audit exercise asks:
given *only* the audit log — no run row, no live process, no chat
history — can you reconstruct who approved what, and when, and prove
the log itself has not been tampered with?

Two functions do the work, and both are deliberately independent of
the agent code in ``durable_pause.py``:

* :func:`verify` — recomputes the hash chain over the whole log
  (see ``store.verify_chain``) and reports whether it is intact.
* :func:`reconstruct` — replays one run's events in sequence order
  and rebuilds the story: the task, the proposed write, the approval
  request and its deadline, the decision and who made it, whether
  the write ran, and whether anything is missing (for example a
  ``write_executed`` event with no preceding ``approval_decided``
  event, which would mean a write happened without approval).

Usage::

    python audit.py --db hitl.db --run-id run-xxxxxxxxxxxx
    python audit.py --db hitl.db --verify-only
"""

from __future__ import annotations

import argparse
import datetime
import json

import store


def _iso(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(
        epoch, tz=datetime.timezone.utc
    ).isoformat()


def reconstruct(conn, run_id: str) -> dict:
    """Rebuild one run's story from its audit events alone."""
    events = store.list_events(conn, run_id)
    if not events:
        raise KeyError(f"no audit events for run_id: {run_id}")
    story: dict = {
        "run_id": run_id,
        "task": None,
        "proposed_write": None,
        "approval_requested_at": None,
        "approver": None,
        "deadline": None,
        "decision": None,
        "decided_by": None,
        "decided_at": None,
        "write_executed": False,
        "write_path": None,
        "completed": False,
        "anomalies": [],
    }
    seen_decision = False
    for event in events:
        payload = event["payload"]
        event_type = event["event_type"]
        if event_type == "run_created":
            story["task"] = payload.get("task")
        elif event_type == "approval_requested":
            story["approval_requested_at"] = _iso(event["created_at"])
            story["approver"] = payload.get("approver")
            story["deadline"] = payload.get("expires_at")
            story["proposed_write"] = {
                "tool": payload.get("tool"),
                "path": payload.get("path"),
            }
        elif event_type == "approval_decided":
            seen_decision = True
            story["decision"] = payload.get("decision")
            story["decided_by"] = event["actor"]
            story["decided_at"] = _iso(event["created_at"])
        elif event_type == "write_executed":
            if not seen_decision:
                story["anomalies"].append(
                    "write_executed with no preceding approval_decided"
                )
            elif story["decision"] != "approved":
                story["anomalies"].append(
                    "write_executed even though the decision was not"
                    f" 'approved' (was {story['decision']!r})"
                )
            story["write_executed"] = True
            story["write_path"] = payload.get("path")
        elif event_type == "run_completed":
            story["completed"] = True
        elif event_type == "run_abandoned":
            story["completed"] = False
    if story["decision"] == "approved" and not story["write_executed"]:
        story["anomalies"].append(
            "approval was 'approved' but no write_executed event exists"
        )
    if story["decision"] is None and story["write_executed"]:
        story["anomalies"].append("write ran with no decision recorded")
    return story


def verify(conn) -> dict:
    """Verify the hash chain over the whole audit log."""
    return store.verify_chain(conn)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(store.default_db_path()))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    conn = store.connect(args.db)
    chain = verify(conn)
    print(f"Hash chain intact: {chain['ok']}"
          f" ({chain['events_checked']} events checked)")
    if not chain["ok"]:
        print(f"First bad entry: seq {chain['first_bad_seq']}:"
              f" {chain['reason']}")
        return 1
    if args.verify_only:
        return 0
    if not args.run_id:
        parser.error("--run-id is required unless --verify-only is given")
    story = reconstruct(conn, args.run_id)
    print(json.dumps(story, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
