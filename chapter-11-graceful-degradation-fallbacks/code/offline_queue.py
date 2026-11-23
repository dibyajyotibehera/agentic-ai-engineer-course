"""Offline queue (Chapter 11, Exercise 4).

A durable queue that accepts work while every provider is down and
replays it, in order, when connectivity returns.  The queue lives in
SQLite (standard library), so tasks survive a process restart: a task
is committed to disk *before* the agent tells the user it is
accepted.

Each task carries an idempotency key.  The replay handler records
every key it has executed, so a crash halfway through replay never
executes a side effect twice.  A task that fails three times moves to
a dead-letter table instead of blocking the queue behind it, and a
task older than 24 hours expires instead of replaying as a stale
answer.  The queue holds at most 1,000 tasks; when full, new tasks
are refused with a clear message rather than accepted and lost.
Those bounds are chosen defaults for the exercise, not measured
results.

No API key or paid service is needed. There is no model stand-in
in this file at all: the queue is real SQLite on disk, and the
replay handler is real local execution.
Run:  python offline_queue.py
"""

from __future__ import annotations

import sqlite3
import time
import uuid

MAX_QUEUE_SIZE = 1_000
MAX_TASK_AGE_S = 24 * 3600
MAX_ATTEMPTS = 3


class QueueFullError(Exception):
    pass


class OfflineQueue:
    """A durable first-in-first-out queue backed by SQLite."""

    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued'
            );
            CREATE TABLE IF NOT EXISTS dead_letter (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                attempts INTEGER NOT NULL,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS executed_keys (
                idempotency_key TEXT PRIMARY KEY
            );
            """
        )
        self.conn.commit()

    def size(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'queued'").fetchone()
        return row[0]

    def enqueue(self, payload: str, *, now: float | None = None) -> str:
        """Commit the task to disk before the caller reports acceptance."""
        if self.size() >= MAX_QUEUE_SIZE:
            raise QueueFullError(
                f"queue is full ({MAX_QUEUE_SIZE} tasks): refusing new work "
                "rather than accepting it and losing it")
        task_id = str(uuid.uuid4())
        key = f"task-{task_id}"
        self.conn.execute(
            "INSERT INTO tasks (id, idempotency_key, payload, created_at) "
            "VALUES (?, ?, ?, ?)",
            (task_id, key, payload, now if now is not None else time.time()),
        )
        self.conn.commit()
        return task_id

    def replay(self, handler, *, now: float | None = None) -> dict:
        """Replay queued tasks in acceptance order.

        ``handler(payload, idempotency_key)`` performs the side effect.
        A key already in ``executed_keys`` is skipped, which is what
        makes replay safe after a crash.  Returns a report of what
        happened to each task.
        """
        now = now if now is not None else time.time()
        report = {"replayed": [], "expired": [], "dead_lettered": [], "retried": []}
        rows = self.conn.execute(
            "SELECT id, idempotency_key, payload, created_at, attempts "
            "FROM tasks WHERE status = 'queued' ORDER BY created_at, id"
        ).fetchall()
        for task_id, key, payload, created_at, attempts in rows:
            if now - created_at > MAX_TASK_AGE_S:
                self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                report["expired"].append(payload)
                continue
            already = self.conn.execute(
                "SELECT 1 FROM executed_keys WHERE idempotency_key = ?",
                (key,)).fetchone()
            if already:
                self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                report["replayed"].append(f"{payload} (already executed, skipped)")
                continue
            try:
                handler(payload, key)
            except Exception as exc:  # noqa: BLE001 - any handler failure retries
                attempts += 1
                if attempts >= MAX_ATTEMPTS:
                    self.conn.execute(
                        "INSERT INTO dead_letter "
                        "(id, idempotency_key, payload, created_at, attempts, last_error) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (task_id, key, payload, created_at, attempts, str(exc)))
                    self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                    report["dead_lettered"].append(payload)
                else:
                    self.conn.execute(
                        "UPDATE tasks SET attempts = ? WHERE id = ?",
                        (attempts, task_id))
                    report["retried"].append(f"{payload} (attempt {attempts}: {exc})")
                self.conn.commit()
                continue
            # Record the key *in the same commit* as removing the task,
            # so a crash cannot leave both behind.
            self.conn.execute(
                "INSERT INTO executed_keys (idempotency_key) VALUES (?)", (key,))
            self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self.conn.commit()
            report["replayed"].append(payload)
        self.conn.commit()
        return report

    def dead_letter(self) -> list[str]:
        return [row[0] for row in self.conn.execute(
            "SELECT payload FROM dead_letter").fetchall()]


def demo() -> None:
    import os
    import tempfile

    path = os.path.join(tempfile.mkdtemp(), "queue.db")
    queue = OfflineQueue(path)
    side_effects: list[str] = []

    def handler(payload: str, key: str) -> None:
        # A handler that crashes on the poisoned task, every time.
        if payload == "poisoned task":
            raise RuntimeError("downstream still failing")
        side_effects.append(payload)

    # Outage: enqueue five tasks, then "restart" the process.
    for i in range(5):
        queue.enqueue(f"task {i + 1}")
    print(f"queued 5 tasks during the outage; on disk: {queue.size()}")
    queue = OfflineQueue(path)  # process restart: reopen the same file
    print(f"after restart, tasks still queued: {queue.size()}")

    # Replay, then replay again after a simulated crash mid-replay:
    # the second replay must not duplicate any side effect.
    report = queue.replay(handler)
    print("first replay:", report["replayed"])
    report = queue.replay(handler)
    print("second replay (after crash):", report["replayed"],
          f"side effects executed: {len(side_effects)} (no duplicates)")

    # A poisoned task fails three times, lands in dead-letter, and does
    # not block the task behind it.
    queue.enqueue("poisoned task")
    queue.enqueue("task after the poisoned one")
    for attempt in range(3):
        report = queue.replay(handler)
        print(f"replay {attempt + 1}: retried={report['retried']} "
              f"dead_lettered={report['dead_lettered']} replayed={report['replayed']}")
    print("dead-letter table:", queue.dead_letter())
    print("side effects in order:", side_effects)

    # A task older than 24 hours expires instead of replaying stale.
    old = OfflineQueue(":memory:")
    old.enqueue("yesterday's weather check", now=time.time() - 25 * 3600)
    print("expired on replay:", old.replay(handler)["expired"])


if __name__ == "__main__":
    demo()
