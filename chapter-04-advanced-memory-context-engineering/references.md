# References — Chapter 4: Advanced Memory and Context Engineering

Every source this chapter draws on, with a real link. Links were verified live on 18 November 2026; where a figure is quoted, the source of the figure is named in the note. Nothing in this file is invented: if a link could not be verified, it is not listed.

## Provenance: what this chapter adapts, and what it does not copy

This chapter adapts, with attribution, Microsoft Lessons 12–13 of [AI Agents for Beginners](https://github.com/microsoft/ai-agents-for-beginners) (MIT Licence): the three-tier working, short-term and long-term memory split, episodic, persona and entity memory, summarisation, and the Mem0 and Cognee notebooks. The shape of the DeepLearning.AI courses listed below was used as syllabus inspiration only — their content is proprietary and none of it was copied. The sections on vector eviction policies, context compression under a budget, dynamic token budgeting and the cost-per-task arithmetic of each memory tier were written originally for this course.

## Adapted source (MIT Licence)

- Microsoft, *AI Agents for Beginners*, Lesson 12 — Context Engineering for AI Agents. The context-engineering strategies (write, select, compress, isolate) and the common context failures this chapter builds on.
  - https://github.com/microsoft/ai-agents-for-beginners/tree/main/12-context-engineering
- Microsoft, *AI Agents for Beginners*, Lesson 13 — Managing Agentic Memory. The three-tier working, short-term and long-term memory split; the Mem0 notebook (`13-agent-memory.ipynb`) and the Cognee knowledge-graph notebook (`13-agent-memory-cognee.ipynb`).
  - https://microsoft.github.io/ai-agents-for-beginners/13-agent-memory/
  - https://github.com/microsoft/ai-agents-for-beginners (repository home; Lesson 13 lives under `13-agent-memory/`)
- Microsoft, *AI Agents for Beginners* (course home).
  - https://github.com/microsoft/ai-agents-for-beginners

## Syllabus inspiration only (proprietary courses; no content copied)

- DeepLearning.AI, *Context Engineering* (built with Chroma). Shape only: right information, right format, right time; pruning, compaction and scratchpads; agentic memory as a module.
  - https://learn.deeplearning.ai/courses/context-engineering/lesson/fwe3dxjr/a-conversation-between-jeff-huber-and-andrew-ng
- DeepLearning.AI, *Long-Term Agentic Memory with LangGraph*. Shape only: semantic, episodic and procedural memory, built into an email agent with LangMem.
  - https://www.deeplearning.ai/short-courses/long-term-agentic-memory-with-langgraph/
- DeepLearning.AI and Oracle, *Agent Memory: Building Memory-Aware Agents*. Shape only: memory-first architecture, a memory manager, semantic tool retrieval, write-back loops.
  - https://www.deeplearning.ai/courses/agent-memory-building-memory-aware-agents
- DeepLearning.AI and Letta, *LLMs as Operating Systems: Agent Memory*. Shape only: self-editing memory, core and archival memory in Letta.
  - https://www.deeplearning.ai/courses/llms-as-operating-systems-agent-memory
- ByteByteGo, *Build with Claude Code*. A paid live course; its public outline was used as a gap checklist for this chapter, and none of its content was copied.
  - https://live.bytebytego.com/courses/claude-code

## Papers (the grounded numbers)

- *MemGPT: Towards LLMs as Operating Systems*, arXiv:2310.08560 (2023). Source of the main-context versus external-context split, the memory-pressure warning at about 70% of the context window, the flush at 100% evicting about 50%, and the Deep Memory Retrieval accuracies (GPT-4 32.1% → 92.5%; GPT-4 Turbo 35.3% → 93.4%; GPT-3.5 Turbo 38.7% → 66.9%).
  - https://arxiv.org/abs/2310.08560
- *From Local to Global: A Graph RAG Approach to Query-Focused Summarization*, arXiv:2404.16130 (Microsoft Research, 2024). Source of the two-stage GraphRAG index (entity knowledge graph, then Leiden community detection and pre-generated community summaries), the map-reduce query over community summaries, and the comprehensiveness win rates of 72–83% (podcast transcripts) and 72–80% (news articles), diversity 75–82% / 62–71%, p < .001, with the root-level variant retaining 72% comprehensiveness and 62% diversity win rates at a fraction of the cost.
  - https://arxiv.org/abs/2404.16130
- *GPT Semantic Cache: Reducing LLM Costs and Latency via Semantic Embedding Caching*, arXiv:2411.05276 (2024). Source of the up-to-68.8% reduction in API calls, the 61.6–68.8% hit rates, the positive hit rate above 97% and the 0.8 cosine threshold optimum (0.6–0.9 sweep). Note: this 68.8% figure is from this arXiv paper, not from the GPTCache library — do not conflate them.
  - https://arxiv.org/abs/2411.05276
- *Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory*, arXiv:2504.19413 (2025). Source of the 91% lower p95 latency and more-than-90% lower token cost versus a full-context baseline, and the 26% relative improvement in the LLM-as-a-judge metric over OpenAI's memory on the LOCOMO benchmark.
  - https://arxiv.org/abs/2504.19413
- *RAFT: Adapting Language Model to Domain Specific RAG*, arXiv:2403.10131 (2024). Source of the RAFT note in the lesson: fine-tuning with retrieved documents in context, including distractor documents that do not help answer the question, so the model learns to use the relevant evidence, cite it, and ignore the rest.
  - https://arxiv.org/abs/2403.10131

## Vendor documentation and pricing (verified figures)

- Pinecone, *Design for multitenancy*. Source of the 1 read unit per 1 GB of namespace searched (minimum 0.25 read units), the 100-tenants × 1 GB worked example (1 read unit for a namespace-per-tenant query versus 100 read units for metadata filtering across a shared 100 GB namespace), and the 10,000-value cap on `$in` / `$nin` filters.
  - https://docs.pinecone.io/guides/data/design-for-multitenancy
- Pinecone, *Implement multitenancy*. Namespace-per-tenant implementation, physical isolation of namespaces, and tenant offboarding by namespace deletion.
  - https://docs.pinecone.io/guides/data/implement-multitenancy
- OpenAI, API pricing. Source of the text-embedding-3-small price of $0.02 per 1M tokens used in the chapter's cost arithmetic. Prices change; re-check the live page before budgeting.
  - https://platform.openai.com/docs/pricing

## Memory systems and libraries (for the build, not for the numbers)

- Mem0 — the memory layer for AI agents: extraction, deduplication and retrieval of durable facts, scoped by user, agent and run identifiers. Documentation of how `add` and `search` work.
  - https://docs.mem0.ai/core-concepts/how-it-works
- Cognee — open-source memory layer for AI agents that builds a knowledge graph over vector embeddings; `remember`, `recall`, `improve` and session-to-graph promotion.
  - https://docs.cognee.ai/getting-started/introduction
- Letta (formerly MemGPT) — memory blocks (core memory) and the context hierarchy: always-visible blocks, archival memory and external stores.
  - https://docs.letta.com/v1-sdk/memory/memory-blocks
  - https://docs.letta.com/v1-sdk/memory/context-hierarchy
- LangMem (LangChain) — memory tools, background memory management and prompt refinement on top of LangGraph's long-term memory store; semantic, episodic and procedural memory kinds.
  - https://github.com/langchain-ai/langmem/blob/HEAD/README.md
  - https://langchain-ai.github.io/langmem/hot_path_quickstart/
- pgvector — open-source vector similarity search for PostgreSQL; the `vector` column type, distance operators and HNSW / IVFFlat indexes used in the Postgres upgrade path in the exercises.
  - https://github.com/pgvector/pgvector

## Style model for the course

- *LLM course* (Apache 2.0) — the style model for this whole course: chapter shape, hands-on notebooks, references.
  - https://github.com/mlabonne/llm-course
