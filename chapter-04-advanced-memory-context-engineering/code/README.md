# Code — Chapter 4: Advanced Memory and Context Engineering

Standard library only; Python 3.11 or later. The deterministic paths
need no API key and no paid service. The live paths — real model
compaction and real model tool-result summarisation — need a
configured model backend: set `LLM_API_KEY` (with `LLM_BASE_URL` and
`LLM_MODEL` if your provider is not OpenCheap's router and Kimi K3.1
Pro), or run with `LLM_BACKEND=opencheap-connector` on a machine
where the OpenCheap connector is registered, which authenticates
through the Secure Vault without the key entering the process.

| File | What it is |
| --- | --- |
| `llm_client.py` | A small production-style client for an OpenAI-compatible chat API, copied verbatim from Chapter 7 so this chapter stays self-contained: environment configuration, retries, structured JSON replies, token and cost totals. |
| `llm_summariser.py` | Real model summarisation: transcript-turn summarisation for compaction and tool-result summarisation for pruning. Cheap judge route (`kimi-k3.1-pro:floor`), temperature zero, generous `max_tokens` with one retry on an empty reply, loud failure after that. |
| `memory_store.py` | The three-tier store (working / episodic / semantic) over SQLite, with the write gate, versioned supersession and contested conflicts, composable eviction policies, and structural tenant scoping. Embeddings are a deterministic hashing stand-in, kept deliberately and documented in the module: the OpenCheap router lists only the chat model `kimi-k3.1-pro` and its `/embeddings` endpoint answers 404, so no cheap real embedding model is available to this chapter in this environment. The single swap point is `embed_text` / `MemoryStore(embedder=...)`; the production replacement is a real embedding model such as text-embedding-3-small (1,536 dimensions, $0.02 per 1M tokens). A hash is not an embedding, and it is never presented as one. |
| `token_budget.py` | Dynamic token budgeting per task type, three-stage compression, the MemGPT 70% warning / 100% flush rule in miniature, and the cost model. The only hard-coded price is the verified $0.02 per 1M tokens for text-embedding-3-small; the large language model price is a parameter you supply. Stage 2 of `compress` takes an optional `summariser` callable for real model summaries; without one it keeps a head excerpt labelled as such. |
| `compaction.py` | Agentic compaction (trigger at the 70% pressure point, with a load-bearing-facts audit), tool-result pruning (summarise, fields, pointer, expiry), a context-rot detector (attention dilution plus stale-instruction contamination), and lazy loading. `compact()` and the default `summarise` strategy are deterministic and offline — the head excerpt they keep is labelled as an excerpt, not a summary. `compact_with_llm()` and `prune_tool_result_with_llm()` are the real model versions. Run the deterministic demo with `python compaction.py`, the live demo with `LLM_BACKEND=opencheap-connector python compaction.py --live`. |
| `test_compaction.py` | Tests for `compaction.py`, standard library only for the deterministic tests. Run with `python3 test_compaction.py` (live tests skip without a backend) or `LLM_BACKEND=opencheap-connector python3 test_compaction.py` for the full run including the live tests. |
| `demo_support_copilot.py` | The running support-copilot demo: the March-versus-June refund policy, a low-authority challenger, a refused payment detail, tenant-scoped retrieval, and a worked cost-per-task calculation. Run it with `python demo_support_copilot.py`; add `--live` (with a configured backend) for the real model compaction and tool-result summary section. |
| `pgvector_schema.sql` | Postgres with pgvector schema for the Exercise 1 stretch goal, including the HNSW index and the tenant-scoped retrieval query. Not executed in this environment (no Postgres here); it is a schema to run against your own database, not a claim about a run that happened. |
| `requirements.txt` | Empty by design — standard library only. |

Check the code compiles with:

```bash
python -m py_compile memory_store.py token_budget.py demo_support_copilot.py compaction.py test_compaction.py llm_client.py llm_summariser.py
```

## What deliberately stays a stand-in, and why

One stand-in remains in this chapter, by design: the embedding
function in `memory_store.py`. There is no cheap real embedding model
available to this chapter — the OpenCheap router this course runs
against exposes only the chat model `kimi-k3.1-pro` (verified:
`list-models` returns one model, kind `chat`, and a call to
`/embeddings` answers 404), and no local embedding model is
installed here. So `fake_embed` stays as the offline default, named
for what it is, with `embed_text` as the single swap point and
text-embedding-3-small named as the production replacement. The
similarity thresholds tuned for it (notably the 0.60 conflict
threshold, versus ~0.85 for a real embedding model) say so in their
comments. Everything that used to stand in for a language model —
transcript summarisation in compaction and the `summarise` pruning
strategy — is now a real model call, and the offline fallbacks label
their output as head excerpts, not summaries.
