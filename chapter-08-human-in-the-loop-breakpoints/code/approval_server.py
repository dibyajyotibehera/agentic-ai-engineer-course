"""Webhook approval endpoint: the human's answer arrives over HTTP.

This server is the routing half of the chapter. It has two jobs, and
it keeps them separate on purpose:

1. **Receive** approval requests from the agent
   (``POST /hooks/approval``). The agent signs the request with a
   shared secret (Hash-based Message Authentication Code using the
   SHA-256 hash function, ``v0:<timestamp>:<raw body>`` — the same
   construction Slack documents for verifying its own requests), and
   this server verifies the signature *before* it trusts anything in
   the body. An unsigned or stale request is rejected with 401.
2. **Accept** the human's decision (``POST /approvals/<run_id>/decision``).
   The decision is written to the same SQLite store the agent reads
   from, so the next ``resume`` picks it up. The endpoint enforces
   the three properties the chapter's design depends on:

   * *Idempotency* — a repeated identical decision (same
     ``idempotency_key``) returns the original outcome instead of
     recording the decision twice. Slack and Teams both retry
     deliveries, so a decision endpoint that double-counts retries
     is a decision endpoint that lies.
   * *Timeout* — a decision that arrives after the approval's
     ``expires_at`` is recorded as ``expired`` and the run fails
     closed. The approver is told so in the response.
   * *First decision wins* — a second, different decision for the
     same approval is rejected with 409, because the audit trail
     must agree with what the agent acted on.

``GET /approvals/<run_id>`` reports the current state of an approval
without changing anything.

Run it with no credentials and no extra packages::

    python approval_server.py --port 8765

Then, in another shell, point the agent at it::

    APPROVAL_WEBHOOK_URL=http://127.0.0.1:8765/hooks/approval \\
        HITL_WEBHOOK_SECRET=dev-only-change-me \\
        python durable_pause.py run --task "Publish the weekly changelog"

The default secret is ``dev-only-change-me`` and the server says so
loudly on startup. Set ``HITL_WEBHOOK_SECRET`` to a long random value
before this listens on anything other than loopback. The signature
scheme is implemented in ``_verify_signature``; the Slack equivalent,
with the same five-minute replay window, lives in ``slack_gate.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import store
from durable_pause import record_decision

DEFAULT_SECRET = "dev-only-change-me"
MAX_TIMESTAMP_SKEW_SECONDS = 5 * 60  # five minutes, as Slack documents


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    base = f"v0:{timestamp}:".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def verify_signature(secret: str, headers, body: bytes) -> tuple[bool, str]:
    """Verify a signed webhook request. Returns (ok, reason)."""
    signature = headers.get("X-HITL-Signature") or headers.get("X-Slack-Signature")
    timestamp = headers.get("X-HITL-Timestamp") or headers.get(
        "X-Slack-Request-Timestamp"
    )
    if not signature or not timestamp:
        return False, "missing signature or timestamp header"
    try:
        timestamp_int = int(timestamp)
    except ValueError:
        return False, "timestamp header is not an integer"
    if abs(time.time() - timestamp_int) > MAX_TIMESTAMP_SKEW_SECONDS:
        return False, "timestamp is more than five minutes from local time"
    expected = compute_signature(secret, timestamp, body)
    if not hmac.compare_digest(expected, signature):
        return False, "signature does not match"
    return True, "ok"


def make_handler(db_path: str, secret: str):
    class ApprovalHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep stdout for the audit log
            pass

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def do_POST(self):  # noqa: N802 — http.server names this method
            body = self._read_body()
            ok, reason = verify_signature(secret, self.headers, body)
            if not ok:
                self._send(401, {"error": "unauthorised", "reason": reason})
                return
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._send(400, {"error": "request body is not valid JSON"})
                return
            conn = store.connect(db_path)
            try:
                if self.path == "/hooks/approval":
                    self._handle_incoming_approval(conn, payload)
                elif self.path.startswith("/approvals/") and self.path.endswith(
                    "/decision"
                ):
                    run_id = self.path[len("/approvals/"):-len("/decision")]
                    self._handle_decision(conn, run_id, payload)
                else:
                    self._send(404, {"error": "unknown endpoint"})
            finally:
                conn.close()

        def do_GET(self):  # noqa: N802 — http.server names this method
            conn = store.connect(db_path)
            try:
                if self.path.startswith("/approvals/"):
                    run_id = self.path[len("/approvals/"):]
                    run = store.get_run(conn, run_id)
                    if run is None:
                        self._send(404, {"error": f"unknown run_id: {run_id}"})
                        return
                    approval = run["state"].get("approval") or {}
                    self._send(200, {
                        "run_id": run_id,
                        "status": run["status"],
                        "approval": approval,
                    })
                    return
                self._send(404, {"error": "unknown endpoint"})
            finally:
                conn.close()

        def _handle_incoming_approval(self, conn, payload: dict) -> None:
            """Log an approval request the agent routed to this endpoint.

            The pending approval itself already lives in the store —
            the agent wrote it there before dispatching — so this
            handler's job is receipt and audit, not creation. That
            separation is deliberate: the webhook is a notification
            channel, and losing a notification must never lose the
            approval.
            """
            run_id = payload.get("run_id")
            if not run_id:
                self._send(400, {"error": "payload has no run_id"})
                return
            store.append_event(
                conn, run_id, "approval_webhook_received", "approval-server",
                {"approval": payload.get("approval", {})},
            )
            self._send(200, {"received": True, "run_id": run_id})

        def _handle_decision(self, conn, run_id: str, payload: dict) -> None:
            decision = payload.get("decision")
            decided_by = payload.get("decided_by") or payload.get("by")
            if decision not in ("approved", "rejected"):
                self._send(400, {
                    "error": "decision must be 'approved' or 'rejected'",
                })
                return
            if not decided_by:
                self._send(400, {"error": "decided_by is required"})
                return
            run = store.get_run(conn, run_id)
            if run is None:
                self._send(404, {"error": f"unknown run_id: {run_id}"})
                return
            approval = run["state"].get("approval") or {}
            idempotency_key = payload.get("idempotency_key")
            if (
                idempotency_key
                and approval.get("decision") is not None
                and approval.get("idempotency_key") == idempotency_key
            ):
                # Exact replay of a decision already recorded.
                self._send(200, {
                    "run_id": run_id,
                    "decision": approval["decision"],
                    "duplicate": True,
                })
                return
            try:
                result = record_decision(
                    conn, run_id, decision=decision,
                    decided_by=decided_by,
                    note=payload.get("note", ""),
                )
            except ValueError as exc:
                self._send(409, {"error": str(exc)})
                return
            if idempotency_key:
                # Re-fetch: record_decision above wrote the decision to
                # the store, and the `run` snapshot taken before it is
                # now stale — writing it back would erase the decision.
                fresh = store.get_run(conn, run_id)
                fresh_approval = fresh["state"].get("approval") or {}
                fresh_approval["idempotency_key"] = idempotency_key
                fresh["state"]["approval"] = fresh_approval
                store.update_run(conn, run_id, state=fresh["state"])
            status = 200
            self._send(status, {
                "run_id": run_id,
                "decision": result["decision"],
                "duplicate": result["duplicate"],
                "note": ("Decision arrived after the deadline; the run"
                         " failed closed." if result["decision"] == "expired"
                         else ""),
            })

    return ApprovalHandler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=str(store.default_db_path()))
    args = parser.parse_args()
    secret = os.environ.get("HITL_WEBHOOK_SECRET", DEFAULT_SECRET)
    if secret == DEFAULT_SECRET:
        print("WARNING: using the default development webhook secret"
              " 'dev-only-change-me'.")
        print("Set HITL_WEBHOOK_SECRET to a long random value before"
              " listening on a non-loopback address.")
    server = ThreadingHTTPServer(
        (args.host, args.port), make_handler(args.db, secret)
    )
    print(f"Approval webhook listening on http://{args.host}:{args.port}")
    print("Endpoints: POST /hooks/approval, "
          "POST /approvals/<run_id>/decision, GET /approvals/<run_id>")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
