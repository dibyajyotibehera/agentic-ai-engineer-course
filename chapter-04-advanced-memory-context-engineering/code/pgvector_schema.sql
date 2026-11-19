-- Postgres with pgvector schema for Chapter 4, Exercise 1 (stretch goal).
--
-- Run once per database:
--   CREATE EXTENSION vector;
--   \i pgvector_schema.sql
--
-- The design mirrors memory_store.py: one table, a tenant column that
-- every query filters on, a tier column, and versioned semantic facts.
-- pgvector adds the vector column and the HNSW index for approximate
-- nearest-neighbour search. See https://github.com/pgvector/pgvector

CREATE TABLE IF NOT EXISTS memories (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    tier        TEXT NOT NULL CHECK (tier IN ('working', 'episodic', 'semantic')),
    text        TEXT NOT NULL,
    source      TEXT NOT NULL,
    authority   TEXT NOT NULL DEFAULT 'low' CHECK (authority IN ('low', 'medium', 'high')),
    importance  DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    version     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'current'
                CHECK (status IN ('current', 'superseded', 'contested')),
    superseded_by BIGINT REFERENCES memories (id),
    embedding   vector(1536),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    access_count INTEGER NOT NULL DEFAULT 0,
    expires_at  TIMESTAMPTZ
);

-- Tenant scoping is structural: the index leads with tenant_id, and every
-- read filters on it, so a forgotten tenant filter is a slow query, not a
-- leak. For stronger guarantees, add Postgres row-level security on
-- tenant_id as well.
CREATE INDEX IF NOT EXISTS idx_memories_tenant_tier_status
    ON memories (tenant_id, tier, status);

-- HNSW index for approximate nearest-neighbour search over the episodic
-- and semantic tiers. 1536 dimensions matches OpenAI text-embedding-3-small;
-- change the dimension to match your embedding model.
CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw
    ON memories USING hnsw (embedding vector_cosine_ops);

-- Example retrieval: top 5 episodic memories for one tenant, cosine order.
--   SELECT id, text, 1 - (embedding <=> $1::vector) AS similarity
--   FROM memories
--   WHERE tenant_id = $2 AND tier = 'episodic' AND status <> 'superseded'
--   ORDER BY embedding <=> $1::vector
--   LIMIT 5;
