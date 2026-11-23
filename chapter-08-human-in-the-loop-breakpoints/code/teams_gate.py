"""Microsoft Teams approval gate: Adaptive Card in, action out.

This module is the Teams half of the chapter's chat-user-interface
gate. :func:`build_approval_card` builds the Adaptive Card JSON for
an approval request, following the shape Microsoft documents for
approval cards: the request as the card body, ``Approve`` and
``Reject`` buttons, and the approval identifier carried in the
action data so the response can be matched to the pending approval.
:func:`handle_card_action` turns the submitted action data back
into a decision. :func:`post_approval` really delivers the card: it
POSTs the message to an incoming webhook URL
(``TEAMS_WEBHOOK_URL``) over Hypertext Transfer Protocol, using the
standard library only.

One honest limit on the webhook route: an incoming webhook can post
a card but cannot receive an interactive ``Action.Execute``
response, so a card posted this way is for review, not for a live
approval — a real approval gate needs a bot (or Power Automate's
"post an Adaptive Card and wait for a response" action) with an
endpoint that receives the ``adaptiveCard/action`` invoke request.
The docstring of :func:`post_approval` says so, and the card it
posts is the same card a bot would send, so the gate mechanics —
card shape, action data, decision parsing — are the ones exercised
here and in the tests.

One Teams-specific trap is worth naming in advance: the card the
approver sees must be the card *for that approver*. Microsoft's
documentation on up-to-date views shows the pattern — a refresh
property that gives approvers a card with buttons and everyone else
a card without them — because a card posted to a channel is visible
to the whole channel, and a button anyone can press is not an
approval control, it is a suggestion box.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"


def build_approval_card(approval: dict) -> dict:
    """Build the Adaptive Card JSON for one approval request."""
    approval_id = approval["approval_id"]
    return {
        "$schema": ADAPTIVE_CARD_SCHEMA,
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": "Approval requested — agent write",
                "weight": "Bolder",
                "size": "Medium",
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Tool", "value": approval["tool"]},
                    {"title": "File", "value": approval["path"]},
                    {"title": "Run", "value": approval["run_id"]},
                    {"title": "Expires", "value": approval["expires_at"]},
                ],
            },
            {
                "type": "TextBlock",
                "text": approval.get("content_preview", ""),
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": "Approving runs the write exactly once."
                        " Rejecting — or letting this expire —"
                        " runs nothing.",
                "wrap": True,
                "isSubtle": True,
            },
        ],
        "actions": [
            {
                "type": "Action.Execute",
                "title": "Approve",
                "style": "positive",
                "data": {"approval_id": approval_id, "decision": "approved"},
            },
            {
                "type": "Action.Execute",
                "title": "Reject",
                "style": "destructive",
                "data": {"approval_id": approval_id, "decision": "rejected"},
            },
        ],
    }


def handle_card_action(action_data: dict, user_id: str) -> dict:
    """Turn submitted Adaptive Card action data into a decision.

    Raises ``ValueError`` on anything that is not a well-formed
    approval action — the same rule as the Slack gate: an unknown
    action is an error, never a decision.
    """
    approval_id = action_data.get("approval_id")
    decision = action_data.get("decision")
    if not approval_id:
        raise ValueError("action data has no approval_id")
    if decision not in ("approved", "rejected"):
        raise ValueError(f"unknown decision in action data: {decision!r}")
    return {
        "approval_id": approval_id,
        "decision": decision,
        "decided_by": user_id or "unknown",
    }


def render_local(approval: dict) -> str:
    """Print the card JSON for review when no Teams app is configured."""
    return json.dumps(
        {"type": "message",
         "attachments": [{
             "contentType": "application/vnd.microsoft.card.adaptive",
             "content": build_approval_card(approval),
         }]},
        indent=2,
    )


def post_approval(approval: dict, webhook_url: str | None = None) -> dict:
    """Deliver the approval card to Microsoft Teams.

    POSTs the Adaptive Card from :func:`build_approval_card` to an
    incoming webhook URL — ``webhook_url`` if given, otherwise the
    ``TEAMS_WEBHOOK_URL`` environment variable — and returns the
    delivery status. Without a URL it raises ``RuntimeError`` with
    the setup steps, instead of pretending the card was sent, and a
    non-2xx response raises ``RuntimeError`` carrying the status,
    so a failed delivery is never mistaken for a delivered approval.

    This is the webhook delivery route, and it has one honest limit:
    an incoming webhook can post a card but cannot receive an
    interactive ``Action.Execute`` response, so a card delivered
    this way is for review, not for a live approval. A real approval
    gate needs a bot (or Power Automate's "post an Adaptive Card and
    wait for a response" action) with a messaging endpoint that
    receives the ``adaptiveCard/action`` invoke request. On that
    invoke request, verify the caller, call
    :func:`handle_card_action`, and record the decision through the
    same decision endpoint the webhook server exposes — never by
    trusting the card's contents alone. After the first decision,
    edit the message so the card shows who decided and removes the
    buttons (Microsoft's up-to-date views pattern), so a second
    click cannot re-decide.
    """
    url = webhook_url or os.environ.get("TEAMS_WEBHOOK_URL")
    if not url:
        raise RuntimeError(
            "No Teams webhook URL: pass webhook_url or set "
            "TEAMS_WEBHOOK_URL to an incoming webhook URL for the "
            "channel. Until then, call render_local and inspect the "
            "card JSON."
        )
    message = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": build_approval_card(approval),
        }],
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(message).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
            return {"delivered": True, "status": response.status}
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Teams webhook returned HTTP {error.code}: "
            f"{error.read().decode(errors='replace')[:500]}"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError(
            f"Teams webhook post did not complete: {error}"
        ) from None
