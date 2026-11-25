"""End-to-end demo: pause, approve, resume, verify — in one process.

Runs the whole chapter flow against a real model::

    python demo_end_to_end.py

(with a model connected — key-based, or ``LLM_BACKEND=bedrock`` —
see ``llm_client.py``). Without a configured backend it fails
loudly with the setup steps, because the plan step is a real model
call now and there is no template to fall back to.

What it does, in order:

1. Starts an agent run whose plan step asks the model to draft the
   proposed write, then pauses before its write tool.
2. Builds the Slack Block Kit card and the Teams Adaptive Card for
   the pending approval and prints both, so the gate payloads can
   be inspected without any chat account.
3. Routes a real-shaped Slack ``block_actions`` payload — the exact
   JSON a human clicking "Approve" would produce — through the
   same ``record_decision`` path the webhook endpoint uses. The
   human is external to this demo, so the click is supplied as a
   fixture; what is real is everything after it: signature-shaped
   payload parsing, decision recording, and the audit trail.
4. Resumes the run in a *fresh* store connection — a real close and
   reopen of the SQLite database, the same thing a process restart
   does — and executes the write exactly once.
5. Reconstructs the run from the audit log alone and verifies the
   hash chain, then prints the whole story, including the model
   tokens and cost for the run.

Every step asserts its own postcondition, so a silent failure is
impossible: if any step does not do what the chapter promises, the
demo exits non-zero with the failed assertion.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import audit
import durable_pause
import slack_gate
import store
import teams_gate
from llm_client import LLMClient, LLMError


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--backend":
        os.environ["LLM_BACKEND"] = sys.argv[2]
    try:
        client = LLMClient()
    except LLMError as error:
        print(f"Cannot run the demo: {error}", file=sys.stderr)
        print("Set LLM_API_KEY, LLM_BASE_URL and LLM_MODEL, "
              "or run with LLM_BACKEND=bedrock and LLM_BEDROCK_MODEL_ID "
              "set (see \"Connect a model\" in the root README).",
              file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "demo.db"
        conn = store.connect(db_path)

        # 1. The agent plans with the real model, then pauses durably
        # before the write.
        result = durable_pause.run_agent(
            conn, "Publish the weekly changelog",
            approver="approver@example.com", timeout_seconds=900,
            client=client,
        )
        run_id = result["run_id"]
        assert result["status"] == "awaiting_approval"
        write = result["approval"]
        print(f"1. Run {run_id} paused before its write tool.")
        print(f"   Model drafted {write['path']}:")
        print("   " + write["content_preview"].replace("\n", "\n   "))

        # The pause survives a restart: close and reopen the store,
        # as a fresh process would, and confirm the run is still there.
        conn.close()
        conn = store.connect(db_path)
        run = store.get_run(conn, run_id)
        assert run is not None and run["status"] == "awaiting_approval"
        print("   Restart survived: the paused run reloaded from SQLite.")

        # 2. Render both chat gates for the pending approval.
        approval = run["state"]["approval"]
        slack_card = slack_gate.render_local(approval)
        teams_card = teams_gate.render_local(approval)
        assert "approve_write" in slack_card and "Approve" in teams_card
        print("2. Slack Block Kit card and Teams Adaptive Card rendered")
        print("   (no Slack or Teams account needed — JSON only).")

        # 3. The human clicks Approve in Slack; route the decision in.
        # The payload is the exact JSON Slack would send for that
        # click, fed through the real interaction handler.
        payload = {
            "type": "block_actions",
            "user": {"id": "U123456", "username": "approver"},
            "actions": [{
                "action_id": "approve_write",
                "value": approval["approval_id"],
            }],
        }
        decision = slack_gate.handle_interaction_payload(payload)
        assert decision["decision"] == "approved"
        recorded = durable_pause.record_decision(
            conn, run_id, decision=decision["decision"],
            decided_by=decision["decided_by"], note="Looks right.",
        )
        assert recorded["decision"] == "approved"
        print(f"3. Human decision recorded: approved by"
              f" {decision['decided_by']}.")

        # 4. Resume from a fresh connection; the write runs once.
        conn.close()
        conn = store.connect(db_path)
        resumed = durable_pause.resume_agent(conn, run_id)
        assert resumed["status"] == "completed"
        assert resumed["write"]["applied"] is True
        again = durable_pause.resume_agent(conn, run_id)
        assert again["outcome"] == "already completed"
        print("4. Resumed after restart; write executed exactly once")
        print(f"   (second resume reports: {again['outcome']}).")

        # 5. Reconstruct the story from the audit log alone.
        chain = audit.verify(conn)
        assert chain["ok"] is True
        story = audit.reconstruct(conn, run_id)
        assert story["decision"] == "approved"
        assert story["decided_by"] == "U123456"
        assert story["write_executed"] is True
        assert story["anomalies"] == []
        print("5. Audit trail verified and reconstructed from the log alone:")
        print(json.dumps(story, indent=2))
        conn.close()
    print(
        f"\nModel usage for this run: {client.total_prompt_tokens} prompt "
        f"tokens, {client.total_completion_tokens} completion tokens"
        + (f", ${client.total_cost_usd:.4f}" if client.total_cost_usd else "")
    )
    print("\nDemo passed: pause, approve, resume, and audit all held.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
