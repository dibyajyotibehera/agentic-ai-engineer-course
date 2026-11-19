"""Microsoft Teams approval gate: Adaptive Card in, action out.

This module is the Teams half of the chapter's chat-user-interface
gate, and — like ``slack_gate.py`` — it is split into a part that
runs with no credentials and a part that is an explicit TODO:

* **Ready to run with no credentials** — :func:`build_approval_card`
  builds the Adaptive Card JSON for an approval request, following
  the shape Microsoft documents for approval cards: the request as
  the card body, ``Approve`` and ``Reject`` buttons, and the approval
  identifier carried in the action data so the response can be
  matched to the pending approval. :func:`handle_card_action` turns
  the submitted action data back into a decision.
* **TODO — needs a real Teams app** — :func:`post_approval` actually
  delivers the card. Teams has two delivery routes and they are not
  interchangeable: an incoming webhook can post a card but cannot
  receive an interactive ``Action.Execute`` response, so a real
  approval gate needs a bot (or Power Automate's "post an Adaptive
  Card and wait for a response" action) with an endpoint that
  receives the ``adaptiveCard/action`` invoke request. Until a bot
  is registered, :func:`render_local` prints the card JSON instead.

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


def post_approval(approval: dict, conversation_id: str) -> dict:
    """TODO: deliver the approval card to Microsoft Teams.

    Not runnable yet — it needs a real Teams app, which this chapter
    does not provision for you. To finish it:

    1. Register a Teams bot (or use Power Automate's "post an
       Adaptive Card and wait for a response" action) and point its
       messaging endpoint at your approval endpoint.
    2. Send the card from :func:`build_approval_card` as an
       ``application/vnd.microsoft.card.adaptive`` attachment.
    3. On the ``adaptiveCard/action`` invoke request, verify the
       caller, call :func:`handle_card_action`, and record the
       decision through the same decision endpoint the webhook
       server exposes — never by trusting the card's contents alone.
    4. After the first decision, edit the message so the card shows
       who decided and removes the buttons (Microsoft's up-to-date
       views pattern), so a second click cannot re-decide.

    Until then, call :func:`render_local` and inspect the card JSON.
    """
    if not os.environ.get("TEAMS_BOT_APP_ID"):
        raise NotImplementedError(
            "Teams posting is a stub: register a Teams bot and finish"
            " the send in post_approval (see its docstring)."
        )
    raise NotImplementedError(
        "Teams posting is a stub: the card send in post_approval"
        " still needs to be written (see its docstring)."
    )
