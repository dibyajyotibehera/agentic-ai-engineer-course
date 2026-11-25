"""Three-tier memory store for Chapter 4.

A framework-neutral, standard-library-only implementation of the memory
layer taught in this chapter:

* **Working tier** -- the current session's turns, held in memory and in
  SQLite, and the only tier that is free to write to.
* **Episodic tier** -- summarised past tasks, stored with an embedding so a
  new task can retrieve the episodes that resemble it.
* **Semantic tier** -- settled facts and rules, versioned, with a source and
  an authority level, so that a newer policy can supersede an older one
  without deleting it.

Design rules enforced by this module, not left to convention:

* Every read and every write requires a ``tenant_id``. A missing tenant is
  a ``ValueError``; a query scoped to one tenant can never return another
  tenant's memories.
* Nothing reaches the episodic or semantic tier except through the
  ``WriteGate``. The gate's five questions (durable, new-or-changed, safe,
  attributable, worth its tokens) are implemented in
  :class:`WriteGate.evaluate`.
* Semantic memories are versioned and append-only. Supersession flips a
  status flag and records a pointer to the replacement; it never deletes.
  A low-authority claim can never silently overwrite a high-authority fact:
  the pair is flagged ``contested`` instead.
* Eviction is explicit and composable. The store layers a time-to-live
  ceiling, importance-decay ranking and least-recently-used tie-breaking;
  see :class:`EvictionPolicy`.

Embeddings here are a deterministic stand-in, not a real embedding model:
:func:`fake_embed` hashes the text into a fixed-length vector so that
similar texts get similar vectors well enough for the exercises to behave.
That stand-in stays deliberately: no embedding model is configured for
this chapter, so there is no real embedding model for the store to call,
and no local embedding model is installed here either. The stand-in is
therefore the honest offline default, and it is never presented as an
embedding:
it is named ``fake_embed``, and every similarity threshold tuned for it
says so in its comment. The single swap point is :func:`embed_text`:
pass a real embedding callable to ``MemoryStore(embedder=...)`` and
every write, duplicate check, conflict check and retrieval uses it
without any other code changing. The production replacement is a real
embedding model -- for example OpenAI's text-embedding-3-small, $0.02
per 1M tokens, 1,536 dimensions (see ``token_budget.py`` and
``pgvector_schema.sql``, whose ``vector(1536)`` column matches it) --
and with a real model the conflict threshold in ``_resolve_conflicts``
should move from 0.60 to around 0.85, as the comment there says.

No API key and no paid service are needed to run this module with the
stand-in embeddings. Passing a real ``embedder`` is the caller's
choice, and the embedder owns its own credentials.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Token estimation and embeddings (stand-ins, clearly labelled as such)
# ---------------------------------------------------------------------------

DIMENSIONS = 64


def estimate_tokens(text: str) -> int:
    """Rough token estimate: about four characters per token.

    This is an estimate for budgeting in the exercises, not a tokenizer.
    Replace with a real tokenizer when you wire in a model.
    """
    return max(1, len(text) // 4)


def fake_embed(text: str, dimensions: int = DIMENSIONS) -> list[float]:
    """Deterministic stand-in embedding.

    Each word contributes to a few hashed buckets, so texts sharing words
    end up close together under cosine similarity. Good enough for the
    exercises; replace with a real embedding model in production.
    A hash is not an embedding: it carries no meaning beyond shared
    words, so paraphrases with different words score low against each
    other, which is why the conflict threshold below is 0.60 instead
    of the ~0.85 a real embedding model would use.
    """
    vector = [0.0] * dimensions
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        digest = hashlib.sha256(word.encode()).digest()
        for i in range(4):
            bucket = digest[i] % dimensions
            vector[bucket] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def embed_text(text: str, dimensions: int = DIMENSIONS, embedder=None) -> list[float]:
    """The single swap point for embeddings.

    With ``embedder=None`` this returns the deterministic stand-in
    :func:`fake_embed`. Pass a real embedding callable -- a function
    taking one text and returning a list of floats, for example a
    wrapper around an embedding-model API call -- and that callable
    is used instead, everywhere in the store. Only this function
    needs to change when the store moves to production embeddings.
    """
    if embedder is not None:
        return embedder(text)
    return fake_embed(text, dimensions)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two unit (or unnormalised) vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class Tier(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryStatus(str, Enum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    CONTESTED = "contested"


@dataclass
class Memory:
    """One stored memory, in any tier."""

    id: int | None = None
    tenant_id: str = ""
    tier: Tier = Tier.EPISODIC
    text: str = ""
    source: str = ""
    authority: str = "low"  # "low" | "medium" | "high"
    importance: float = 0.5  # 0.0 - 1.0, as judged at write time
    version: int = 1
    status: MemoryStatus = MemoryStatus.CURRENT
    superseded_by: int | None = None
    embedding: list[float] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    access_count: int = 0
    expires_at: float | None = None  # Unix timestamp, or None for no expiry

    def token_estimate(self) -> int:
        return estimate_tokens(self.text)


# ---------------------------------------------------------------------------
# The write gate
# ---------------------------------------------------------------------------

# Patterns that must never be stored. Extend this list for your domain; the
# gate refuses a candidate if any pattern matches, and names the reason.
SECRET_PATTERNS = [
    re.compile(r"\b(?:api[_-]?key|password|passwd|secret|token)\b\s*[:=]", re.I),
    re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),  # card-like numbers
    re.compile(r"\bcard ends in \d{4}\b", re.I),
    re.compile(r"\bexpires? \d{2}/\d{2}\b", re.I),
    re.compile(r"\b(?:ssn|social security)\b", re.I),
]

# Words that mark a candidate as a transient observation, not a durable fact.
TRANSIENT_MARKERS = ("checked", "at 14:", "started at", "just now", "today at")


@dataclass
class GateVerdict:
    admitted: bool
    reason: str  # "admitted" | "unsafe" | "transient" | "duplicate" | "unattributed" | "not_worth_tokens"
    detail: str = ""


class WriteGate:
    """Decides what earns a write to the episodic or semantic tier.

    The five questions, in order: durable, new-or-changed, safe,
    attributable, worth its tokens. A candidate must pass all five.
    """

    def __init__(
        self,
        min_score: float = 0.25,
        duplicate_threshold: float = 0.85,
        store: "MemoryStore | None" = None,
        embedder=None,
    ) -> None:
        self.min_score = min_score
        self.duplicate_threshold = duplicate_threshold
        self.store = store
        self.embedder = embedder  # real embedding callable, or None for the stand-in
        self.log: list[dict] = []  # auditable verdict history

    def score(self, importance: float, expected_reuse: float) -> float:
        """Worth-its-tokens score: importance x expected reuse.

        Both inputs are 0.0-1.0 judgements made at write time. The threshold
        (default 0.25) is a product decision: raise it and the store stays
        small and trustworthy; lower it and the store grows noisy.
        """
        return importance * expected_reuse

    def evaluate(
        self,
        text: str,
        *,
        tenant_id: str,
        source: str,
        importance: float = 0.5,
        expected_reuse: float = 0.5,
        tier: Tier = Tier.EPISODIC,
    ) -> GateVerdict:
        """Return a verdict for one candidate memory."""
        verdict = self._decide(text, tenant_id, source, importance, expected_reuse, tier)
        self.log.append(
            {
                "text": text,
                "tenant_id": tenant_id,
                "tier": tier.value,
                "admitted": verdict.admitted,
                "reason": verdict.reason,
                "detail": verdict.detail,
            }
        )
        return verdict

    def _decide(self, text, tenant_id, source, importance, expected_reuse, tier):
        if not tenant_id or not source:
            return GateVerdict(False, "unattributed", "tenant_id and source are required")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            return GateVerdict(False, "unsafe", "matches a secret/payment pattern")
        lowered = text.lower()
        if any(marker in lowered for marker in TRANSIENT_MARKERS):
            return GateVerdict(False, "transient", "a one-off observation, not a durable fact")
        if tier is Tier.SEMANTIC and len(text.split()) < 4:
            return GateVerdict(False, "transient", "too fragmentary to be a settled fact")
        if self.store is not None:
            for existing in self.store.list_memories(tenant_id, tier):
                if existing.status is MemoryStatus.SUPERSEDED:
                    continue
                sim = cosine_similarity(
                    embed_text(text, embedder=self.embedder),
                    existing.embedding or embed_text(existing.text, embedder=self.embedder),
                )
                if sim >= self.duplicate_threshold:
                    return GateVerdict(False, "duplicate", f"near-duplicate of memory {existing.id} (similarity {sim:.2f})")
        if self.score(importance, expected_reuse) < self.min_score:
            return GateVerdict(
                False,
                "not_worth_tokens",
                f"score {self.score(importance, expected_reuse):.2f} below threshold {self.min_score}",
            )
        return GateVerdict(True, "admitted", "passed all five gate questions")


# ---------------------------------------------------------------------------
# Eviction policies
# ---------------------------------------------------------------------------

class EvictionPolicy:
    """Composable eviction: time-to-live ceiling, importance decay, LRU tie-break.

    A memory's survival score is ``importance * exp(-age / half_life)``,
    where ``age`` is in seconds and ``half_life`` is the policy's tunable
    parameter. A support policy might deserve a half-life of months; a
    transient workaround deserves one of days. Memories past their
    ``expires_at`` are evicted first, whatever their score.
    """

    def __init__(self, half_life_seconds: float = 30 * 24 * 3600, cap: int = 100) -> None:
        self.half_life_seconds = half_life_seconds
        self.cap = cap

    def survival_score(self, memory: Memory, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        age = max(0.0, now - memory.created_at)
        return memory.importance * math.exp(-age / self.half_life_seconds)

    def choose_evict(self, memories: list[Memory], now: float | None = None) -> Memory | None:
        """Pick the single best eviction candidate, or None if under the cap."""
        now = now if now is not None else time.time()
        if len(memories) <= self.cap:
            return None
        expired = [m for m in memories if m.expires_at is not None and m.expires_at <= now]
        if expired:
            return min(expired, key=lambda m: m.last_accessed_at)
        # Lowest survival score; least-recently-used breaks ties.
        return min(memories, key=lambda m: (self.survival_score(m, now), m.last_accessed_at))

    def rank_for_retrieval(self, memory: Memory, similarity: float, now: float | None = None) -> float:
        """Blend used at retrieval time: similarity, recency and importance."""
        now = now if now is not None else time.time()
        age_days = max(0.0, now - memory.created_at) / 86400.0
        recency = 1.0 / (1.0 + age_days / 30.0)
        return 0.6 * similarity + 0.2 * recency + 0.2 * memory.importance


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL,
    authority TEXT NOT NULL DEFAULT 'low',
    importance REAL NOT NULL DEFAULT 0.5,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'current',
    superseded_by INTEGER,
    embedding TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    last_accessed_at REAL NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_memories_tenant_tier
    ON memories (tenant_id, tier, status);
"""


class MemoryStore:
    """A three-tier memory store over SQLite.

    Tenant scoping is structural: ``tenant_id`` is a required argument of
    every read and write, and every query filters on it.
    """

    AUTHORITY_RANK = {"low": 1, "medium": 2, "high": 3}

    def __init__(
        self,
        path: str | Path = ":memory:",
        gate: WriteGate | None = None,
        eviction: EvictionPolicy | None = None,
        embedder=None,
    ) -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.embedder = embedder  # real embedding callable, or None for the stand-in
        self.gate = gate or WriteGate(store=self, embedder=embedder)
        self.gate.store = self
        self.gate.embedder = embedder
        self.eviction = eviction or EvictionPolicy()

    def _embed(self, text: str) -> list[float]:
        """Embed one text with the configured embedder, or the stand-in."""
        return embed_text(text, embedder=self.embedder)

    # -- working tier ------------------------------------------------------

    def append_working(self, tenant_id: str, session_id: str, role: str, text: str) -> Memory:
        """Append a turn to working memory. Working writes are ungated: the
        current session is the one tier where writing is free."""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        memory = Memory(
            tenant_id=tenant_id,
            tier=Tier.WORKING,
            text=f"[{session_id}] {role}: {text}",
            source=f"session:{session_id}",
        )
        return self._insert(memory)

    def working_history(self, tenant_id: str, session_id: str) -> list[Memory]:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE tenant_id = ? AND tier = 'working' AND source = ? ORDER BY id",
            (tenant_id, f"session:{session_id}"),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    # -- episodic and semantic tiers (gated) -------------------------------

    def write_episode(
        self,
        tenant_id: str,
        text: str,
        *,
        source: str,
        importance: float = 0.5,
        expected_reuse: float = 0.5,
        expires_at: float | None = None,
    ) -> tuple[Memory | None, GateVerdict]:
        verdict = self.gate.evaluate(
            text, tenant_id=tenant_id, source=source,
            importance=importance, expected_reuse=expected_reuse, tier=Tier.EPISODIC,
        )
        if not verdict.admitted:
            return None, verdict
        memory = Memory(
            tenant_id=tenant_id, tier=Tier.EPISODIC, text=text, source=source,
            importance=importance, embedding=self._embed(text), expires_at=expires_at,
        )
        memory = self._insert(memory)
        self._enforce_cap(tenant_id, Tier.EPISODIC)
        return memory, verdict

    def write_fact(
        self,
        tenant_id: str,
        text: str,
        *,
        source: str,
        authority: str = "medium",
        importance: float = 0.7,
        expected_reuse: float = 0.7,
    ) -> tuple[Memory | None, GateVerdict]:
        """Write a semantic fact, with versioned supersession and conflict handling.

        A new fact supersedes a conflicting current fact only when its
        authority is at least as high as the old fact's. A lower-authority
        challenger is stored as ``contested`` and the current fact stands.
        """
        verdict = self.gate.evaluate(
            text, tenant_id=tenant_id, source=source,
            importance=importance, expected_reuse=expected_reuse, tier=Tier.SEMANTIC,
        )
        if not verdict.admitted:
            return None, verdict
        memory = Memory(
            tenant_id=tenant_id, tier=Tier.SEMANTIC, text=text, source=source,
            authority=authority, importance=importance, embedding=self._embed(text),
        )
        memory = self._insert(memory)
        self._resolve_conflicts(memory)
        return memory, verdict

    def _resolve_conflicts(self, new: Memory) -> None:
        """Supersede or contest near-duplicate current facts in the same tenant."""
        for old in self.list_memories(new.tenant_id, Tier.SEMANTIC):
            if old.id == new.id or old.status is not MemoryStatus.CURRENT:
                continue
            sim = cosine_similarity(new.embedding, old.embedding or self._embed(old.text))
            if sim < 0.60:
                # Threshold tuned for the stand-in embeddings in this module,
                # which give lower similarities than a real embedding model
                # would for paraphrases. With a real embedding model, use a
                # higher threshold (around 0.85) for the same decision.
                continue
            if self.AUTHORITY_RANK[new.authority] >= self.AUTHORITY_RANK[old.authority]:
                old.status = MemoryStatus.SUPERSEDED
                old.superseded_by = new.id
                new.version = old.version + 1
                self._update(old)
                self._update(new)
            else:
                # The challenger is flagged contested and the current fact
                # stands: a low-authority claim never silently overwrites
                # a high-authority fact. Both versions stay visible, and
                # the contest is routed to a human (see exercises.md).
                new.status = MemoryStatus.CONTESTED
                self._update(new)

    # -- retrieval ---------------------------------------------------------

    def retrieve(
        self,
        tenant_id: str,
        query: str,
        *,
        tier: Tier = Tier.EPISODIC,
        top_k: int = 5,
    ) -> list[tuple[Memory, float]]:
        """Top-k retrieval for one tenant and one tier, ranked by the blend
        of similarity, recency and importance. Updates access statistics."""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        query_vec = self._embed(query)
        scored: list[tuple[Memory, float]] = []
        for memory in self.list_memories(tenant_id, tier):
            if memory.status is MemoryStatus.SUPERSEDED:
                continue
            vec = memory.embedding or self._embed(memory.text)
            similarity = cosine_similarity(query_vec, vec)
            scored.append((memory, self.eviction.rank_for_retrieval(memory, similarity)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        top = scored[:top_k]
        for memory, _ in top:
            memory.access_count += 1
            memory.last_accessed_at = time.time()
            self._update(memory)
        return top

    def current_facts(self, tenant_id: str) -> list[Memory]:
        """Semantic facts currently in force for a tenant."""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        return [m for m in self.list_memories(tenant_id, Tier.SEMANTIC)
                if m.status is MemoryStatus.CURRENT]

    # -- eviction ----------------------------------------------------------

    def _enforce_cap(self, tenant_id: str, tier: Tier) -> None:
        memories = self.list_memories(tenant_id, tier)
        while len(memories) > self.eviction.cap:
            victim = self.eviction.choose_evict(memories)
            if victim is None:
                break
            self.conn.execute("DELETE FROM memories WHERE id = ?", (victim.id,))
            memories = [m for m in memories if m.id != victim.id]
        self.conn.commit()

    # -- helpers -----------------------------------------------------------

    def list_memories(self, tenant_id: str, tier: Tier) -> list[Memory]:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE tenant_id = ? AND tier = ? ORDER BY id",
            (tenant_id, tier.value),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def _insert(self, memory: Memory) -> Memory:
        cursor = self.conn.execute(
            """INSERT INTO memories (tenant_id, tier, text, source, authority,
               importance, version, status, superseded_by, embedding,
               created_at, last_accessed_at, access_count, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory.tenant_id, memory.tier.value, memory.text, memory.source,
                memory.authority, memory.importance, memory.version,
                memory.status.value, memory.superseded_by,
                json.dumps(memory.embedding), memory.created_at,
                memory.last_accessed_at, memory.access_count, memory.expires_at,
            ),
        )
        self.conn.commit()
        memory.id = cursor.lastrowid
        return memory

    def _update(self, memory: Memory) -> None:
        self.conn.execute(
            """UPDATE memories SET status = ?, superseded_by = ?, version = ?,
               last_accessed_at = ?, access_count = ? WHERE id = ?""",
            (
                memory.status.value, memory.superseded_by, memory.version,
                memory.last_accessed_at, memory.access_count, memory.id,
            ),
        )
        self.conn.commit()

    @staticmethod
    def _from_row(row: tuple) -> Memory:
        return Memory(
            id=row[0], tenant_id=row[1], tier=Tier(row[2]), text=row[3],
            source=row[4], authority=row[5], importance=row[6], version=row[7],
            status=MemoryStatus(row[8]), superseded_by=row[9],
            embedding=json.loads(row[10]), created_at=row[11],
            last_accessed_at=row[12], access_count=row[13], expires_at=row[14],
        )

    def close(self) -> None:
        self.conn.close()
