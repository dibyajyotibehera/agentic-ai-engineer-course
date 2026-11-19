# Code — Chapter 4: Advanced Memory and Context Engineering

Standard library only; Python 3.11 or later. No API key and no paid service
is needed for the core exercises.

| File | What it is |
| --- | --- |
| `memory_store.py` | The three-tier store (working / episodic / semantic) over SQLite, with the write gate, versioned supersession and contested conflicts, composable eviction policies, and structural tenant scoping. Embeddings are a deterministic stand-in — swap `fake_embed` for a real embedding call without changing anything else. |
| `token_budget.py` | Dynamic token budgeting per task type, three-stage compression, the MemGPT 70% warning / 100% flush rule in miniature, and the cost model. The only hard-coded price is the verified $0.02 per 1M tokens for text-embedding-3-small; the large language model price is a parameter you supply. |
| `compaction.py` | Agentic compaction (trigger at the 70% pressure point, with a load-bearing-facts audit), tool-result pruning (summarise, fields, pointer, expiry), a context-rot detector (attention dilution plus stale-instruction contamination), and lazy loading. Run the demo with `python compaction.py`. |
| `test_compaction.py` | Tests for `compaction.py`, standard library only. Run with `python3 test_compaction.py`. |
| `demo_support_copilot.py` | The running support-copilot demo: the March-versus-June refund policy, a low-authority challenger, a refused payment detail, tenant-scoped retrieval, and a worked cost-per-task calculation. Run it with `python demo_support_copilot.py`. |
| `pgvector_schema.sql` | Postgres with pgvector schema for the Exercise 1 stretch goal, including the HNSW index and the tenant-scoped retrieval query. |
| `requirements.txt` | Empty by design — standard library only. |

Check the code compiles with:

```bash
python -m py_compile memory_store.py token_budget.py demo_support_copilot.py compaction.py test_compaction.py
```
