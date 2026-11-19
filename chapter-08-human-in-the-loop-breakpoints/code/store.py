"""Shared durable store for Chapter 8.

Two tables in one SQLite database:

* ``runs`` — one row per agent run: the checkpointed state of the run
  (a JSON document), its status, and the approval that is outstanding
  against it, if any.
* ``audit_events`` — an append-only, hash-chained log. Every event
  records the SHA-256 hash of the previous event, so deleting or
  editing an earlier row breaks the chain and is detectable by
  :func:`verify_chain`. The audit log is the only record the
  reconstruction exercise in ``exercises.md`` is allowed to use.

Nothing here needs a network connection or an API key. The standard
library is the only dependency.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    task        TEXT NOT NULL,
    status      TEXT NOT NULL,
    state_json  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at  REAL NOT NULL,
    prev_hash   TEXT NOT NULL,
    entry_hash  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_events (run_id, seq);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the store at ``db_path``."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def append_event(
    conn: sqlite3.Connection,
    run_id: str,
    event_type: str,
    actor: str,
    payload: dict,
    *,
    created_at: float | None = None,
) -> dict:
    """Append one event to the hash-chained audit log and return it.

    The chain is what makes the log tamper-evident rather than merely
    append-only: each entry commits to the hash of the entry before it,
    so an edit to any earlier row changes every hash after it.
    """
    created_at = time.time() if created_at is None else created_at
    row = conn.execute(
        "SELECT entry_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = row["entry_hash"] if row else "GENESIS"
    body = _canonical(
        {
            "run_id": run_id,
            "event_type": event_type,
            "actor": actor,
            "payload": payload,
            "created_at": created_at,
            "prev_hash": prev_hash,
        }
    )
    entry_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    cursor = conn.execute(
        "INSERT INTO audit_events"
        " (run_id, event_type, actor, payload_json, created_at,"
        "  prev_hash, entry_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, event_type, actor, _canonical(payload), created_at,
         prev_hash, entry_hash),
    )
    conn.commit()
    return {
        "seq": cursor.lastrowid,
        "run_id": run_id,
        "event_type": event_type,
        "actor": actor,
        "payload": payload,
        "created_at": created_at,
        "prev_hash": prev_hash,
        "entry_hash": entry_hash,
    }


def verify_chain(conn: sqlite3.Connection, run_id: str | None = None) -> dict:
    """Recompute the hash chain and report whether it is intact.

    Returns ``{"ok": True, "events_checked": n}`` on success, or
    ``{"ok": False, ...}`` naming the first sequence number whose
    stored hash does not match its recomputed hash. Pass ``run_id`` to
    check one run's events only; the chain is global, so a filtered
    check verifies the entries in order but not the links that cross
    into other runs — the unfiltered check is the authoritative one.
    """
    if run_id is None:
        rows = conn.execute(
            "SELECT * FROM audit_events ORDER BY seq"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
    prev_hash = "GENESIS"
    for row in rows:
        if run_id is not None and row["seq"] > 1:
            # In a filtered check the first row's prev_hash links to an
            # event outside the filter; verify the hash itself, not the
            # link, for that first row.
            pass
        payload = json.loads(row["payload_json"])
        body = _canonical(
            {
                "run_id": row["run_id"],
                "event_type": row["event_type"],
                "actor": row["actor"],
                "payload": payload,
                "created_at": row["created_at"],
                "prev_hash": row["prev_hash"],
            }
        )
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != row["entry_hash"]:
            return {
                "ok": False,
                "events_checked": row["seq"],
                "first_bad_seq": row["seq"],
                "reason": "entry hash does not match recomputed hash",
            }
        if run_id is None and row["prev_hash"] != prev_hash:
            return {
                "ok": False,
                "events_checked": row["seq"],
                "first_bad_seq": row["seq"],
                "reason": "prev_hash link is broken",
            }
        prev_hash = row["entry_hash"]
    return {"ok": True, "events_checked": len(rows)}


def create_run(
    conn: sqlite3.Connection, task: str, state: dict, *, run_id: str | None = None
) -> str:
    """Create a run in status ``planned`` and log its creation."""
    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    now = time.time()
    conn.execute(
        "INSERT INTO runs (run_id, task, status, state_json, created_at,"
        " updated_at) VALUES (?, ?, 'planned', ?, ?, ?)",
        (run_id, task, _canonical(state), now, now),
    )
    conn.commit()
    append_event(conn, run_id, "run_created", "agent",
                 {"task": task, "state": state}, created_at=now)
    return run_id


def get_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "task": row["task"],
        "status": row["status"],
        "state": json.loads(row["state_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def update_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str | None = None,
    state: dict | None = None,
) -> None:
    """Update a run's status and/or checkpointed state in one write."""
    current = get_run(conn, run_id)
    if current is None:
        raise KeyError(f"unknown run_id: {run_id}")
    new_status = status if status is not None else current["status"]
    new_state = state if state is not None else current["state"]
    conn.execute(
        "UPDATE runs SET status = ?, state_json = ?, updated_at = ?"
        " WHERE run_id = ?",
        (new_status, _canonical(new_state), time.time(), run_id),
    )
    conn.commit()


def list_events(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """All audit events for one run, in the order they happened."""
    rows = conn.execute(
        "SELECT * FROM audit_events WHERE run_id = ? ORDER BY seq", (run_id,)
    ).fetchall()
    return [
        {
            "seq": row["seq"],
            "run_id": row["run_id"],
            "event_type": row["event_type"],
            "actor": row["actor"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
            "prev_hash": row["prev_hash"],
            "entry_hash": row["entry_hash"],
        }
        for row in rows
    ]


def list_runs(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status is None:
        rows = conn.execute("SELECT * FROM runs ORDER BY created_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM runs WHERE status = ? ORDER BY created_at", (status,)
        ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "task": row["task"],
            "status": row["status"],
            "state": json.loads(row["state_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def default_db_path() -> Path:
    return Path(__file__).resolve().parent / "hitl.db"
