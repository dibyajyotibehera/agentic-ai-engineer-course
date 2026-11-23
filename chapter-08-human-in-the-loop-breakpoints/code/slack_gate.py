"""Slack approval gate: Block Kit card in, signed interaction out.

This module is the Slack half of the chapter's chat-user-interface
gate. Two parts of it run with no credentials at all:

* :func:`build_approval_blocks` builds the Block Kit JSON for an
  approval card, and :func:`verify_slack_signature` implements
  Slack's request verification (Hash-based Message Authentication
  Code using the SHA-256 hash function over
  ``v0:<timestamp>:<raw body>``, compared in constant time, with a
  five-minute replay window — exactly as Slack documents it).
  :func:`handle_interaction_payload` parses a ``block_actions``
  payload into a decision the agent can act on. All three are
  exercised by the tests and by ``demo_end_to_end.py``.
* :func:`post_approval` really sends the card with
  ``chat.postMessage`` over Hypertext Transfer Protocol, using the
  standard library only. It needs a bot token (``SLACK_BOT_TOKEN``),
  a channel, and an app with interactivity enabled and pointed at
  your approval endpoint. Without a token it fails loudly instead
  of pretending to send; :func:`render_local` prints the card JSON
  instead so the shape of the gate can still be reviewed and tested
  with no Slack account.

Why the split matters: the approval *decision* is a security boundary,
and the card is only its presentation. The code that turns a button
click into a trusted, attributable decision — signature verification,
the ``action_id`` that names the approval, the approver's Slack user
identifier recorded in the audit trail — is the part that has to be
right, and it is the part that runs here without any Slack account.

Block Kit facts used below come from Slack's own documentation:
a message holds up to 50 blocks, an ``actions`` block holds up to 25
elements, and a button carries an ``action_id`` and a ``value`` that
come back in the interaction payload.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request

MAX_TIMESTAMP_SKEW_SECONDS = 5 * 60  # five minutes, per Slack's docs
SLACK_API_URL = "https://slack.com/api/chat.postMessage"


def build_approval_blocks(approval: dict) -> list[dict]:
    """Build the Block Kit blocks for one approval request.

    The card shows the human everything they need to decide and
    nothing they do not: what the agent wants to do, the exact file
    it would write, who asked, and when the request expires. The two
    buttons carry the approval identifier in their ``value`` so the
    interaction payload can be matched to the pending approval
    without trusting anything else in the message.
    """
    approval_id = approval["approval_id"]
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Approval requested — agent write",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Tool:*\n{approval['tool']}"},
                {"type": "mrkdwn", "text": f"*File:*\n{approval['path']}"},
                {"type": "mrkdwn", "text": f"*Run:*\n{approval['run_id']}"},
                {"type": "mrkdwn",
                 "text": f"*Expires:*\n{approval['expires_at']}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Proposed content:*\n```\n"
                        f"{approval.get('content_preview', '')}\n```",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve",
                             "emoji": True},
                    "style": "primary",
                    "action_id": "approve_write",
                    "value": approval_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject",
                             "emoji": True},
                    "style": "danger",
                    "action_id": "reject_write",
                    "value": approval_id,
                },
            ],
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "Approving runs the write exactly once. "
                        "Rejecting — or letting this expire — runs nothing.",
            }],
        },
    ]


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    raw_body: bytes,
    signature: str,
    *,
    now: float | None = None,
) -> bool:
    """Verify a request genuinely came from Slack.

    Rejects timestamps more than five minutes from local time (replay
    protection) and compares digests with ``hmac.compare_digest`` so
    the comparison does not leak timing information. The body must be
    the raw bytes Slack sent — parsing and re-serialising the JSON
    changes whitespace and key order, and with it the digest.
    """
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        timestamp_int = int(timestamp)
    except ValueError:
        return False
    now = time.time() if now is None else now
    if abs(now - timestamp_int) > MAX_TIMESTAMP_SKEW_SECONDS:
        return False
    base = f"v0:{timestamp}:".encode("utf-8") + raw_body
    digest = hmac.new(
        signing_secret.encode("utf-8"), base, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"v0={digest}", signature)


def handle_interaction_payload(payload: dict) -> dict:
    """Turn a Slack ``block_actions`` payload into a decision.

    Returns ``{"approval_id", "decision", "decided_by", "raw_action"}``.
    Raises ``ValueError`` on anything that is not a well-formed
    approval click — an unknown ``action_id`` is not a decision, it
    is an error, and treating it as one would let a stray button
    approve a write.
    """
    actions = payload.get("actions") or []
    if len(actions) != 1:
        raise ValueError("expected exactly one action in the payload")
    action = actions[0]
    mapping = {"approve_write": "approved", "reject_write": "rejected"}
    action_id = action.get("action_id")
    if action_id not in mapping:
        raise ValueError(f"unknown action_id: {action_id!r}")
    user = payload.get("user") or {}
    return {
        "approval_id": action.get("value"),
        "decision": mapping[action_id],
        "decided_by": user.get("id") or user.get("username") or "unknown",
        "raw_action": action_id,
    }


def render_local(approval: dict) -> str:
    """Print the card JSON for review when no Slack app is configured."""
    message = {
        "text": f"Approval requested for run {approval['run_id']}",
        "blocks": build_approval_blocks(approval),
    }
    return json.dumps(message, indent=2)


def post_approval(approval: dict, channel: str) -> dict:
    """Send the approval card to Slack with ``chat.postMessage``.

    Needs a real Slack app: create one, enable interactivity, and
    set its request URL to your approval endpoint (the server in
    ``approval_server.py``). Give the app the ``chat:write`` scope,
    install it, and set ``SLACK_BOT_TOKEN`` (``xoxb-...``) and
    ``SLACK_SIGNING_SECRET`` in the environment. The call uses the
    standard library only — no ``slack_sdk`` dependency — and
    verifies every inbound interaction with
    :func:`verify_slack_signature` before calling
    :func:`handle_interaction_payload`.

    Without a token it raises ``RuntimeError`` with the setup steps,
    instead of pretending the card was sent. On a Slack-side error
    (``{"ok": false, ...}``) it raises ``RuntimeError`` carrying
    Slack's error, so a failed delivery is never mistaken for a
    delivered approval. Returns Slack's response, including the
    message timestamp (``ts``) on success.
    """
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "No Slack bot token: set SLACK_BOT_TOKEN (xoxb-...) after "
            "creating a Slack app with the chat:write scope and "
            "interactivity pointed at your approval endpoint. "
            "Until then, call render_local and inspect the card JSON."
        )
    message = {
        "channel": channel,
        "text": f"Approval requested for run {approval['run_id']}",
        "blocks": build_approval_blocks(approval),
    }
    # SLACK_API_URL is an override seam for tests, so the delivery
    # can be exercised against a local server; production uses the
    # default Slack endpoint.
    url = os.environ.get("SLACK_API_URL", SLACK_API_URL)
    request = urllib.request.Request(
        url,
        data=json.dumps(message).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": "Bearer " + token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        raise RuntimeError(
            f"Slack chat.postMessage returned HTTP {error.code}: {detail}"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError(
            f"Slack chat.postMessage did not complete: {error}"
        ) from None
    if not result.get("ok"):
        raise RuntimeError(
            f"Slack chat.postMessage failed: {result.get('error')}"
        )
    return result
