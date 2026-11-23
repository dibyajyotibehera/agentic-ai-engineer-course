"""
03 — State serialisation: versioned, small, and safe to reload anywhere.

Standard library only:

    python 03_state_serialisation.py

A checkpoint is only as good as its format. This file implements the three
rules the lesson argues for, in about a hundred lines:

1.  State is plain data. The serialisable state is a dataclass of strings,
    numbers, lists and dicts, converted to JavaScript Object Notation (JSON)
    through ``dataclasses.asdict``. No pickled objects, no live handles, no
    model clients, no file descriptors. If a value cannot survive
    ``json.dumps`` and ``json.loads`` unchanged, it does not belong in state.
2.  State carries a schema version, and old versions migrate. ``StateV1``
    stored the ticket queue as a bare list of strings; ``StateV2`` stores
    structured tickets with an explicit status. ``migrate()`` upgrades a V1
    snapshot to V2, and loading a snapshot with an unknown *future* version
    fails loudly instead of silently misreading it. Deploying new code over
    old checkpoints is the most common way a durable system corrupts itself;
    the version field plus a migration is the defence.
3.  Large payloads stay out of state. A 50-megabyte document is stored under
    a key in an external blob store and only the key travels in state — the
    claim-check pattern. The same rule keeps secrets out: an API key or a
    token in a checkpoint is a credential sitting in a database backup, so
    ``to_json()`` refuses to serialise any key whose name looks secret.

The blob store here is a real directory on disk, not an in-memory dict:
``BlobStore.put`` writes real bytes to a real file and ``get`` reads them
back. It stands in for object storage (Amazon S3, a database, a file
store) only in the sense that it is local disk rather than a service; the
claim-check mechanics it demonstrates — the key in state, the payload on
disk, the snapshot staying small — are the real ones, and the demo proves
them against a real 50-megabyte file.

Run the file to see all three rules exercised, including the failure modes:
an unknown version is rejected, and a secret smuggled into state raises
instead of being written.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

CURRENT_VERSION = 2

# Field names that must never be written into a checkpoint. A secret in a
# snapshot ends up in every backup, replica and log that snapshot touches.
SECRET_FIELD_NAMES = {"api_key", "token", "password", "secret", "auth_header"}


class StateError(ValueError):
    """Raised when a snapshot cannot be loaded or saved safely."""


@dataclass
class TicketV2:
    ticket_id: str
    summary: str
    status: str = "open"  # open | resolved | escalated


@dataclass
class AgentState:
    """Versioned, serialisable run state. Nothing here holds a live resource."""

    version: int = CURRENT_VERSION
    run_id: str = ""
    tickets: list[TicketV2] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    # Claim checks: keys into external storage, never the payloads themselves.
    attachments: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        self._check_no_secrets(asdict(self))
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def _check_no_secrets(payload: object, path: str = "") -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key.lower() in SECRET_FIELD_NAMES:
                    raise StateError(
                        f"refusing to serialise secret field {path}{key!r}: "
                        "keep credentials out of checkpoints"
                    )
                AgentState._check_no_secrets(value, f"{path}{key}.")
        elif isinstance(payload, list):
            for item in payload:
                AgentState._check_no_secrets(item, path)

    @classmethod
    def from_json(cls, raw: str) -> "AgentState":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StateError(f"snapshot is not valid JSON: {exc}") from exc
        version = payload.get("version")
        if version is None:
            raise StateError("snapshot has no 'version' field — refusing to guess")
        if version > CURRENT_VERSION:
            raise StateError(
                f"snapshot version {version} is newer than this code "
                f"(max {CURRENT_VERSION}): deploy the code that wrote it first"
            )
        if version == 1:
            payload = migrate_v1_to_v2(payload)
        if version not in (1, 2):
            raise StateError(f"unknown snapshot version {version}")
        payload["tickets"] = [TicketV2(**t) for t in payload.get("tickets", [])]
        return cls(**{k: v for k, v in payload.items()
                      if k in {"version", "run_id", "tickets", "log",
                               "attachments"}})


def migrate_v1_to_v2(payload: dict) -> dict:
    """Upgrade a version-1 snapshot: bare ticket strings become structures.

    Migrations must be pure functions of the old payload, and they must be
    tested against real snapshots captured in production — a migration that
    has only ever run on a hand-written fixture is a migration you have not
    tested. Keep every migration forever: a snapshot written two years ago
    still has to load.
    """
    tickets = payload.get("tickets", [])
    migrated = [
        {"ticket_id": t, "summary": "(migrated from v1: summary not recorded)",
         "status": "open"} if isinstance(t, str) else t
        for t in tickets
    ]
    return {**payload, "version": 2, "tickets": migrated,
            "attachments": payload.get("attachments", {})}


# ---------------------------------------------------------------------------
# The claim-check pattern: big payloads live outside state, on real disk.
# ---------------------------------------------------------------------------

class BlobStore:
    """A file-backed blob store: the claim-check pattern against real files.

    ``put`` writes real bytes to a real file under ``directory`` and
    returns the key; ``get`` reads the file back. Keys look like
    ``blob://exports/workspace-7.csv`` — the ``blob://`` scheme is stripped
    and the rest is the path under the store directory. A key that would
    escape the directory (``..`` or an absolute path) is refused, because
    a store that lets a key walk the filesystem is a path-traversal bug,
    not a store.

    This is local disk, not Amazon S3 — but the mechanics the chapter is
    teaching are the same ones a service-backed store has: the payload
    lives outside state, the key is the only thing state carries, and
    reading the payload back is a separate step from loading the state.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        relative = key[7:] if key.startswith("blob://") else key
        candidate = (self.directory / relative).resolve()
        if candidate != self.directory and self.directory.resolve() not in candidate.parents:
            raise ValueError(f"blob key escapes the store directory: {key!r}")
        return candidate

    def put(self, key: str, data: bytes) -> str:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path_for(key).read_bytes()


def demo() -> None:
    print("== 1. Round-trip: state survives JSON serialisation ==")
    state = AgentState(
        run_id="run-42",
        tickets=[TicketV2("T-101", "Refund for a duplicate charge")],
        log=["draft T-101", "review T-101: pass"],
    )
    raw = state.to_json()
    print(f"snapshot is {len(raw)} bytes of JSON")
    restored = AgentState.from_json(raw)
    assert restored == state, "round-trip changed the state"
    print("round-trip OK:", restored.tickets[0])

    print("\n== 2. Migration: a version-1 snapshot still loads ==")
    v1 = json.dumps({"version": 1, "run_id": "run-9",
                     "tickets": ["T-001", "T-002"], "log": ["draft T-001"]})
    migrated = AgentState.from_json(v1)
    print("migrated to v2:", migrated.tickets)

    print("\n== 3. Claim check: the document stays out of state ==")
    with tempfile.TemporaryDirectory(prefix="ch01-blob-store-") as tmpdir:
        store = BlobStore(tmpdir)
        payload = b"x" * 50_000_000  # a real 50 MB file, not a dict entry
        key = store.put("blob://exports/workspace-7.csv", payload)
        state.attachments["export"] = key
        snapshot = state.to_json()
        print(f"blob written to {tmpdir}/exports/workspace-7.csv "
              f"({len(payload):,} bytes on disk)")
        print(f"snapshot with a 50 MB attachment is still {len(snapshot)} bytes")
        assert "x" * 100 not in snapshot, "payload leaked into state"
        retrieved = store.get(key)
        assert retrieved == payload, "blob read back differs from what was stored"
        print(f"blob read back OK: {len(retrieved):,} bytes, contents match")
        # The temporary directory — and the 50 MB file with it — is
        # removed when the demo exits, so the repo stays clean.

    print("\n== 4. Failure modes ==")
    for label, bad in [
        ("future version", json.dumps({"version": 99, "tickets": []})),
        ("no version", json.dumps({"tickets": []})),
        ("not JSON", "{not json"),
    ]:
        try:
            AgentState.from_json(bad)
        except StateError as exc:
            print(f"  {label}: rejected — {exc}")
        else:
            raise AssertionError(f"{label} should have been rejected")
    sneaky = AgentState(run_id="run-77")
    sneaky.attachments["api_key"] = "sk-live-should-never-be-here"
    try:
        sneaky.to_json()
    except StateError as exc:
        print(f"  secret in state: rejected — {exc}")
    else:
        raise AssertionError("secret should have been rejected")
    print("\nAll checks passed.")


if __name__ == "__main__":
    demo()
