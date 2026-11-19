"""Tests for Chapter 8: durable pause, webhook decisions, audit chain.

Run with the standard library only::

    python -m unittest test_chapter08 -v

The tests cover the properties the chapter promises, not the
incidental shape of the code: a pause that survives a reopened
store, a write that cannot run without an explicit approval, a
timeout that fails closed, a second decision that cannot overwrite
the first, a tampered audit log that fails verification, and a
Slack signature that rejects forgeries, replays, and wrong secrets.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path

import audit
import durable_pause
import slack_gate
import store
import teams_gate


class StoreAndPauseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.db"
        self.conn = store.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _paused_run(self, timeout_seconds=900):
        result = durable_pause.run_agent(
            self.conn, "Publish the weekly changelog",
            approver="approver@example.com", timeout_seconds=timeout_seconds,
        )
        return result["run_id"]

    def test_pause_survives_reopening_the_store(self):
        run_id = self._paused_run()
        self.conn.close()
        self.conn = store.connect(self.db)
        run = store.get_run(self.conn, run_id)
        self.assertEqual(run["status"], "awaiting_approval")
        self.assertEqual(run["state"]["step"], "awaiting_approval")

    def test_resume_without_a_decision_does_not_write(self):
        run_id = self._paused_run()
        outcome = durable_pause.resume_agent(self.conn, run_id)
        self.assertEqual(outcome["status"], "awaiting_approval")
        out_dir = Path(__file__).parent / "out"
        marker = f"run_id: {run_id}"
        written = [p for p in out_dir.glob("*") if p.is_file()
                   and marker in p.read_text(encoding="utf-8")]
        self.assertEqual(written, [])

    def test_approved_run_writes_exactly_once(self):
        run_id = self._paused_run()
        durable_pause.record_decision(
            self.conn, run_id, decision="approved",
            decided_by="approver@example.com")
        first = durable_pause.resume_agent(self.conn, run_id)
        self.assertTrue(first["write"]["applied"])
        second = durable_pause.execute_write(self.conn, run_id)
        self.assertFalse(second["applied"])
        self.assertIn("already applied", second["reason"])

    def test_rejected_run_never_writes(self):
        run_id = self._paused_run()
        durable_pause.record_decision(
            self.conn, run_id, decision="rejected",
            decided_by="approver@example.com", note="Wrong file.")
        outcome = durable_pause.resume_agent(self.conn, run_id)
        self.assertEqual(outcome["status"], "rejected")
        self.assertNotIn("write", outcome)

    def test_expired_approval_fails_closed(self):
        run_id = self._paused_run(timeout_seconds=1)
        time.sleep(1.2)
        recorded = durable_pause.record_decision(
            self.conn, run_id, decision="approved",
            decided_by="approver@example.com")
        self.assertEqual(recorded["decision"], "expired")
        outcome = durable_pause.resume_agent(self.conn, run_id)
        self.assertEqual(outcome["status"], "rejected")

    def test_second_different_decision_is_refused(self):
        run_id = self._paused_run()
        durable_pause.record_decision(
            self.conn, run_id, decision="approved",
            decided_by="approver@example.com")
        with self.assertRaises(ValueError):
            durable_pause.record_decision(
                self.conn, run_id, decision="rejected",
                decided_by="mallory@example.com")

    def test_audit_chain_detects_tampering(self):
        run_id = self._paused_run()
        durable_pause.record_decision(
            self.conn, run_id, decision="approved",
            decided_by="approver@example.com")
        durable_pause.resume_agent(self.conn, run_id)
        self.assertTrue(audit.verify(self.conn)["ok"])
        self.conn.execute(
            "UPDATE audit_events SET actor = 'mallory' WHERE seq = 2")
        self.conn.commit()
        result = audit.verify(self.conn)
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_bad_seq"], 2)

    def test_reconstruction_from_the_log_alone(self):
        run_id = self._paused_run()
        durable_pause.record_decision(
            self.conn, run_id, decision="approved",
            decided_by="approver@example.com")
        durable_pause.resume_agent(self.conn, run_id)
        story = audit.reconstruct(self.conn, run_id)
        self.assertEqual(story["decision"], "approved")
        self.assertEqual(story["decided_by"], "approver@example.com")
        self.assertTrue(story["write_executed"])
        self.assertTrue(story["completed"])
        self.assertEqual(story["anomalies"], [])


class SlackGateTests(unittest.TestCase):
    def test_signature_verifies_and_rejects_forgery(self):
        secret = "test-signing-secret"
        body = b'{"type": "block_actions"}'
        timestamp = str(int(time.time()))
        base = f"v0:{timestamp}:".encode() + body
        good = "v0=" + hmac.new(
            secret.encode(), base, hashlib.sha256).hexdigest()
        self.assertTrue(slack_gate.verify_slack_signature(
            secret, timestamp, body, good))
        self.assertFalse(slack_gate.verify_slack_signature(
            "wrong-secret", timestamp, body, good))
        self.assertFalse(slack_gate.verify_slack_signature(
            secret, timestamp, b'{"type": "tampered"}', good))

    def test_stale_timestamp_is_a_replay(self):
        secret = "test-signing-secret"
        body = b"{}"
        old = str(int(time.time()) - 601)
        base = f"v0:{old}:".encode() + body
        sig = "v0=" + hmac.new(
            secret.encode(), base, hashlib.sha256).hexdigest()
        self.assertFalse(slack_gate.verify_slack_signature(
            secret, old, body, sig))

    def test_interaction_payload_maps_to_a_decision(self):
        payload = {
            "user": {"id": "U123456"},
            "actions": [{"action_id": "reject_write",
                         "value": "appr-run-abc"}],
        }
        decision = slack_gate.handle_interaction_payload(payload)
        self.assertEqual(decision, {
            "approval_id": "appr-run-abc",
            "decision": "rejected",
            "decided_by": "U123456",
            "raw_action": "reject_write",
        })
        with self.assertRaises(ValueError):
            slack_gate.handle_interaction_payload({
                "user": {"id": "U1"},
                "actions": [{"action_id": "delete_everything",
                             "value": "appr-run-abc"}],
            })

    def test_approval_card_has_both_buttons(self):
        approval = {
            "approval_id": "appr-1", "run_id": "run-1", "tool": "write_file",
            "path": "output.md", "content_preview": "hello",
            "expires_at": "2026-11-18T00:00:00+00:00",
        }
        blocks = slack_gate.build_approval_blocks(approval)
        actions = next(b for b in blocks if b["type"] == "actions")
        self.assertEqual(
            [e["action_id"] for e in actions["elements"]],
            ["approve_write", "reject_write"],
        )
        self.assertLessEqual(len(blocks), 50)  # Slack's per-message limit


class TeamsGateTests(unittest.TestCase):
    def test_card_action_maps_to_a_decision(self):
        decision = teams_gate.handle_card_action(
            {"approval_id": "appr-1", "decision": "approved"}, "user-42")
        self.assertEqual(decision["decision"], "approved")
        self.assertEqual(decision["decided_by"], "user-42")
        with self.assertRaises(ValueError):
            teams_gate.handle_card_action(
                {"approval_id": "appr-1", "decision": "maybe"}, "user-42")

    def test_card_is_valid_adaptive_card_json(self):
        approval = {
            "approval_id": "appr-1", "run_id": "run-1", "tool": "write_file",
            "path": "output.md", "content_preview": "hello",
            "expires_at": "2026-11-18T00:00:00+00:00",
        }
        card = teams_gate.build_approval_card(approval)
        json.dumps(card)  # must be JSON-serialisable
        self.assertEqual(card["type"], "AdaptiveCard")
        self.assertEqual(len(card["actions"]), 2)


class ApprovalServerTests(unittest.TestCase):
    """The webhook decision endpoint, exercised over real HTTP."""

    def setUp(self):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer

        import approval_server

        self.http = http.client
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "server.db")
        self.secret = "test-webhook-secret"
        handler = approval_server.make_handler(self.db, self.secret)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.conn = store.connect(self.db)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.conn.close()
        self.tmp.cleanup()

    def _post(self, path, body, sign=True):
        raw = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if sign:
            timestamp = str(int(time.time()))
            base = f"v0:{timestamp}:".encode() + raw
            digest = hmac.new(
                self.secret.encode(), base, hashlib.sha256).hexdigest()
            headers["X-HITL-Signature"] = f"v0={digest}"
            headers["X-HITL-Timestamp"] = timestamp
        conn = self.http.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", path, body=raw, headers=headers)
        response = conn.getresponse()
        payload = json.loads(response.read())
        conn.close()
        return response.status, payload

    def test_decision_replay_resume_over_http(self):
        # The agent pauses and dispatches its signed webhook.
        import os
        os.environ["APPROVAL_WEBHOOK_URL"] = (
            f"http://127.0.0.1:{self.port}/hooks/approval")
        os.environ["HITL_WEBHOOK_SECRET"] = self.secret
        try:
            result = durable_pause.run_agent(
                self.conn, "Publish the weekly changelog",
                approver="approver@example.com", timeout_seconds=900)
        finally:
            del os.environ["APPROVAL_WEBHOOK_URL"]
            del os.environ["HITL_WEBHOOK_SECRET"]
        run_id = result["run_id"]
        self.assertTrue(result["webhook_delivery"]["delivered"])

        # An unsigned decision is refused.
        status, _ = self._post(
            f"/approvals/{run_id}/decision",
            {"decision": "approved", "decided_by": "approver@example.com"},
            sign=False)
        self.assertEqual(status, 401)

        # A signed decision lands, and its exact replay is a duplicate.
        body = {"decision": "approved", "decided_by": "approver@example.com",
                "note": "Looks right.", "idempotency_key": "key-1"}
        status, first = self._post(f"/approvals/{run_id}/decision", body)
        self.assertEqual(status, 200)
        self.assertEqual(first["decision"], "approved")
        self.assertFalse(first["duplicate"])
        status, replay = self._post(f"/approvals/{run_id}/decision", body)
        self.assertEqual(status, 200)
        self.assertTrue(replay["duplicate"])

        # Resume in a fresh connection completes the write exactly once.
        self.conn.close()
        self.conn = store.connect(self.db)
        resumed = durable_pause.resume_agent(self.conn, run_id)
        self.assertEqual(resumed["status"], "completed")
        self.assertTrue(resumed["write"]["applied"])
        story = audit.reconstruct(self.conn, run_id)
        self.assertEqual(story["anomalies"], [])


if __name__ == "__main__":
    unittest.main()
