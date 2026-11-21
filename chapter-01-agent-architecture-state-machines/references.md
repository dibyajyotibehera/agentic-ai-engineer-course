# Chapter 01 — References

Every link below was verified live on 18 November 2026, by opening the page or by a web search returning the exact URL. No URL was invented. Where a source is used as syllabus inspiration only, that is stated.

## Adapted material (MIT licence, with attribution)

- Microsoft, AI Agents for Beginners, Lesson 14, "Microsoft Agent Framework" — the skeleton this chapter adapts: workflows, checkpointing, thread serialisation, durable pause, resume and recovery. MIT licence.
  <https://microsoft.github.io/ai-agents-for-beginners/14-microsoft-agent-framework/>
- Microsoft Learn, "Workflows — Checkpoints" (Microsoft Agent Framework) — checkpoints are created at the end of each superstep; in-memory, file and Azure Cosmos DB checkpoint storage; rehydrating a workflow from a checkpoint requires the same topology and executor identities.
  <https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints>

## Syllabus inspiration only (no content copied)

- LangChain Academy, "Introduction to LangGraph" (Python) — free course, 55 lessons, about 6 hours of video. Its module shape (graphs, state schemas and reducers, checkpointing, time travel, sub-graphs, map-reduce) is the syllabus inspiration for the LangGraph material in this chapter; the course prose carries no open licence and was not copied.
  <https://academy.langchain.com/courses/intro-to-langgraph>
- LangGraph reference, "Checkpoints" — checkpoint saver implementations (`InMemorySaver` for debugging and testing only, `SqliteSaver` for lightweight demos, `PostgresSaver` for production) and the checkpoint model.
  <https://langchain-ai.github.io/langgraph/reference/checkpoints/>

## The decision framework

- Anthropic, "Building effective agents", December 2024 — workflows versus agents, the five workflow patterns (prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer), and the guidance to start with the simplest solution and add complexity only when it demonstrably improves outcomes.
  <https://www.anthropic.com/engineering/building-effective-agents>

## Harness engineering

- OpenAI, "Harness engineering" — the harness as the code around the model: instructions, state, verification, scope and lifecycle; the million-line codebase experiment with roughly 1,500 pull requests over five months.
  <https://openai.com/index/harness-engineering/>
- Anthropic, "Effective harnesses for long-running agents" — the model as the reasoning engine and everything else as engineering around it; harness design for agents that run for hours.
  <https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents>
- Anthropic, "Harness design for long-running apps" — the Opus 4.5 retro-game comparison: 20 minutes and $9 bare versus 6 hours and $200 harnessed.
  <https://www.anthropic.com/engineering/harness-design-long-running-apps>

## Durable execution (Temporal.io)

- Temporal, "Understanding Temporal" — durable execution, workflows, activities, workers, task queues, the Temporal Service, and the event history as the durable log behind recovery by replay.
  <https://docs.temporal.io/evaluate/understanding-temporal>
- Temporal, "System limits" (Temporal Cloud) — event history capped at 51,200 events or 50 MB per workflow execution, warnings from 10,240 events or 10 MB; single payload 2 MB; event-history transaction 4 MB; per-message gRPC limit 4 MB.
  <https://docs.temporal.io/cloud/limits>

## The reader's stack (Mastra and Amazon Bedrock)

- Mastra, "What are durable AI agents? How state persistence and checkpoints survive a restart" — the durable record as the unit of a long-running agent; turn-level, step-level and mid-step persistence; the suspended order-workflow snapshot serialising to 1,059 bytes of JSON; execution state versus memory.
  <https://mastra.ai/blog/what-are-durable-ai-agents>
- Mastra, "Suspend and resume" (Workflows documentation) — `suspend()` saves a snapshot to the configured storage provider; `resume()` restarts from the suspended step with typed resume data; recovering suspended runs with `getWorkflowRunById()` and `createWorkflowStateReader()`.
  <https://mastra.ai/docs/workflows/suspend-and-resume>
- Amazon Web Services, "Create and configure agent manually" (Amazon Bedrock) — the components of an Amazon Bedrock agent: foundation model, instructions, action groups, knowledge bases, guardrails.
  <https://docs.aws.amazon.com/bedrock/latest/userguide/agents-create.html>
- Model Context Protocol, "Specification" (revision 2026-07-28) — the open protocol for connecting language-model applications to external data sources and tools over JSON-RPC 2.0.
  <https://modelcontextprotocol.io/specification/2026-07-28>

## Course style and background

- "LLM course" — the style model for this course (chapter shape, hands-on notebooks, references). Apache 2.0.
  <https://github.com/mlabonne/llm-course>
- This course's conversion plan, recording per-chapter provenance and licence rules.
  <../CONVERSION_PLAN.md>
