"""Tests for Chapter 8: durable pause, webhook decisions, audit chain.

Run with the standard library only::

    python -m unittest test_chapter08 -v

The tests cover the properties the chapter promises, not the
incidental shape of the code: a pause that survives a reopened
store, a write that cannot run without an explicit approval, a
timeout that fails closed, a second decision that cannot overwrite
the first, a tampered audit log that fails verification, and a
Slack signature that rejects forgeries, replays, and wrong secrets.

The deterministic tests use a trivial planner fixture
(:func:`_test_planner`) injected through ``run_agent``'s ``planner``
argument, because those tests are about the pause, the resume and
the audit trail — not about what the model drafts. The fixture
lives in this file only and never ships as production code; the
production plan step is a real model call (see the live tests).

The live tests at the end need a configured model backend
(``LLM_API_KEY`` or ``LLM_BACKEND=opencheap-connector``). When no
backend is configured they skip instead of failing: a skipped live
test means "not run", not "passed".
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import audit
import durable_pause
import slack_gate
import store
import teams_gate
from llm_client import LLMClient


def _test_planner(task: str) -> dict:
    """Deterministic planner fixture for the pause/resume tests.

    This is a test double, not production code: it returns a fixed
    write so the deterministic tests can exercise the pause, the
    resume, the timeout and the audit trail without a model. The
    production plan step is ``durable_pause.plan_write``, a real
    model call covered by the live tests below.
    """
    filename = "changelog.md" if "changelog" in task.lower() else "output.md"
    return {
        "tool": "write_file",
        "risk": "high",
        "path": filename,
        "content": f"# Agent output\n\nTask: {task}\n",
        "idempotency_key": hashlib.sha256(task.encode("utf-8")).hexdigest()[:16],
    }


def _llm_available() -> bool:
    return LLMClient.has_backend()


def _live_client() -> LLMClient:
    return LLMClient()


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
            planner=_test_planner,
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
                approver="approver@example.com", timeout_seconds=900,
                planner=_test_planner)
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


class ChatGateDeliveryTests(unittest.TestCase):
    """The chat gates' real delivery, exercised over real HTTP.

    Both gates post to a local server standing in for Slack's and
    Teams' endpoints, so the tests prove the request that would
    leave the process — headers, body, error handling — not just
    the JSON that would go in it. The local server is the delivery
    endpoint under test, not a mock of the model: no model is
    involved in delivering a card.
    """

    def setUp(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.received = []

        class CaptureHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):  # noqa: N802 — http.server names this method
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                self.server.captured.append(
                    {"path": self.path,
                     "headers": dict(self.headers),
                     "body": json.loads(body) if body else {}})
                reply = self.server.reply_body
                payload = json.dumps(reply).encode()
                self.send_response(self.server.reply_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
        self.server.captured = self.received
        self.server.reply_status = 200
        self.server.reply_body = {"ok": True, "ts": "123.456"}
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.approval = {
            "approval_id": "appr-1", "run_id": "run-1",
            "tool": "write_file", "path": "output.md",
            "content_preview": "hello",
            "expires_at": "2026-11-18T00:00:00+00:00",
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        os.environ.pop("SLACK_BOT_TOKEN", None)
        os.environ.pop("SLACK_API_URL", None)

    def test_slack_post_sends_the_card_and_returns_slacks_reply(self):
        os.environ["SLACK_BOT_TOKEN"] = "xoxb-test-token"
        os.environ["SLACK_API_URL"] = (
            f"http://127.0.0.1:{self.port}/api/chat.postMessage")
        result = slack_gate.post_approval(self.approval, "#approvals")
        self.assertEqual(result["ts"], "123.456")
        self.assertEqual(len(self.received), 1)
        sent = self.received[0]
        self.assertEqual(
            sent["headers"].get("Authorization"), "Bearer xoxb-test-token")
        self.assertEqual(sent["body"]["channel"], "#approvals")
        actions = next(
            b for b in sent["body"]["blocks"] if b["type"] == "actions")
        self.assertEqual(
            [e["action_id"] for e in actions["elements"]],
            ["approve_write", "reject_write"])

    def test_slack_post_without_a_token_fails_loudly(self):
        os.environ.pop("SLACK_BOT_TOKEN", None)
        with self.assertRaises(RuntimeError):
            slack_gate.post_approval(self.approval, "#approvals")
        self.assertEqual(self.received, [])

    def test_slack_post_reports_slacks_error(self):
        os.environ["SLACK_BOT_TOKEN"] = "xoxb-test-token"
        os.environ["SLACK_API_URL"] = f"http://127.0.0.1:{self.port}/api/x"
        self.server.reply_body = {"ok": False, "error": "invalid_auth"}
        with self.assertRaises(RuntimeError) as caught:
            slack_gate.post_approval(self.approval, "#approvals")
        self.assertIn("invalid_auth", str(caught.exception))

    def test_teams_post_delivers_the_adaptive_card(self):
        result = teams_gate.post_approval(
            self.approval, f"http://127.0.0.1:{self.port}/webhook")
        self.assertTrue(result["delivered"])
        self.assertEqual(result["status"], 200)
        self.assertEqual(len(self.received), 1)
        sent = self.received[0]
        attachment = sent["body"]["attachments"][0]
        self.assertEqual(
            attachment["contentType"],
            "application/vnd.microsoft.card.adaptive")
        self.assertEqual(attachment["content"]["type"], "AdaptiveCard")
        self.assertEqual(len(attachment["content"]["actions"]), 2)

    def test_teams_post_without_a_url_fails_loudly(self):
        os.environ.pop("TEAMS_WEBHOOK_URL", None)
        with self.assertRaises(RuntimeError):
            teams_gate.post_approval(self.approval)
        self.assertEqual(self.received, [])

    def test_teams_post_reports_a_failed_delivery(self):
        self.server.reply_status = 403
        self.server.reply_body = {"error": "forbidden"}
        with self.assertRaises(RuntimeError) as caught:
            teams_gate.post_approval(
                self.approval, f"http://127.0.0.1:{self.port}/webhook")
        self.assertIn("403", str(caught.exception))


@unittest.skipUnless(_llm_available(), "needs a configured LLM backend")
class TestLivePlanningAndPause(unittest.TestCase):
    """Live tests: the model drafts the write; the gate still holds."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "live.db"
        self.conn = store.connect(self.db)
        self.client = _live_client()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_plan_write_drafts_real_content(self):
        write = durable_pause.plan_write(
            "Publish the weekly changelog", self.client)
        self.assertEqual(write["tool"], "write_file")
        self.assertTrue(write["path"].endswith(".md"))
        self.assertGreater(len(write["content"]), 20)
        # The draft is about the task it was given, not a template.
        self.assertIn("changelog", write["content"].lower())
        self.assertNotEqual(
            write["content"], "# Agent output\n\nTask: Publish the weekly changelog\n")

    def test_live_pause_approve_resume_writes_the_draft_once(self):
        result = durable_pause.run_agent(
            self.conn, "Publish the weekly changelog",
            approver="approver@example.com", timeout_seconds=900,
            client=self.client)
        run_id = result["run_id"]
        self.assertEqual(result["status"], "awaiting_approval")
        self.assertIsNotNone(result["model_usage"])
        durable_pause.record_decision(
            self.conn, run_id, decision="approved",
            decided_by="approver@example.com", note="Looks right.")
        resumed = durable_pause.resume_agent(self.conn, run_id)
        self.assertEqual(resumed["status"], "completed")
        self.assertTrue(resumed["write"]["applied"])
        written = Path(resumed["write"]["path"]).read_text(encoding="utf-8")
        self.assertIn("changelog", written.lower())
        self.assertNotIn("# Agent output", written)
        print(f"\nLive test usage: {self.client.total_prompt_tokens} prompt "
              f"tokens, {self.client.total_completion_tokens} completion "
              f"tokens"
              + (f", ${self.client.total_cost_usd:.4f}"
                 if self.client.total_cost_usd else ""))


if __name__ == "__main__":
    unittest.main()
