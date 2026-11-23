"""The receipt ledger: a real file-backed record of receipts sent.

Both durable-execution demos in this chapter (the LangGraph checkpoint
demo in ``02_langgraph_checkpoint_resume.py`` and the Temporal demo in
``04_temporal_workflow.py``) send a receipt when a ticket is resolved or
escalated, and both need the same guarantee: sending the receipt twice
for one ticket must be impossible, even if the node that sends it is
killed mid-execution and re-run on resume. The ledger is the mechanism
that provides it, and it is a real file, not a dict: each receipt is one
JSON line in ``receipts.jsonl`` next to this module, so the record
survives the process that wrote it — which is the whole point of a side
effect. ``record_receipt`` checks the file first and appends nothing if
the ticket is already there, so a retry is a no-op by construction.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

DEFAULT_LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "receipts.jsonl")


def record_receipt(ticket_id: str,
                   ledger_path: str = DEFAULT_LEDGER_PATH) -> str:
    """Append a receipt for a ticket to the ledger, unless one exists.

    Returns the receipt string (``receipt:<ticket_id>``). A second call
    for the same ticket returns the existing receipt without appending
    a second line: the ledger, not the caller's memory, is what makes
    the send idempotent.
    """
    path = Path(ledger_path)
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("ticket_id") == ticket_id:
                return str(entry["receipt"])
    receipt = f"receipt:{ticket_id}"
    entry = {"ticket_id": ticket_id, "receipt": receipt,
             "recorded_at": datetime.datetime.now(
                 datetime.timezone.utc).isoformat()}
    with open(path, "a") as fh:
        fh.write(json.dumps(entry) + "\n")
    return receipt


def receipt_count(ticket_id: str,
                  ledger_path: str = DEFAULT_LEDGER_PATH) -> int:
    """How many ledger entries exist for a ticket (the idempotency check)."""
    path = Path(ledger_path)
    if not path.exists():
        return 0
    return sum(
        1 for line in path.read_text().splitlines()
        if line.strip() and json.loads(line).get("ticket_id") == ticket_id
    )
