# References — Chapter 5: Multi-Agent Swarms and Agent-to-Agent Handoffs

Every link below was verified live on 18 November 2026. Links are given in full; no URL was constructed from a domain name.

## The Agent-to-Agent protocol

- **Agent2Agent (A2A) Protocol Specification, version 1.0.0** — the normative specification: the three-layer model (canonical data model, abstract operations, protocol bindings), the six core objects (Task, Message, AgentCard, Part, Artifact, Extension), the task lifecycle, and the JSON-RPC 2.0, gRPC and HTTP+JSON bindings. The specification states that `spec/a2a.proto` is the single authoritative normative definition of all protocol data objects.
  <https://a2a-protocol.org/latest/specification/>
- **A2A Protocol project site** — overview of the protocol, its key features, and the official software development kits in Python, TypeScript, Java, Go and .NET.
  <https://google.github.io/A2A/>
- **A2A Protocol specification repository** — the specification source, architecture decision records, and release notes, under the Linux Foundation, Apache Licence 2.0.
  <https://github.com/a2aproject/A2A>
- **A2A Python SDK (`a2a-sdk`)** — the official Python software development kit for the Agent2Agent protocol; implements specification version 1.0 with a compatibility mode for version 0.3; install with `pip install a2a-sdk`; Python 3.10 or later.
  <https://github.com/a2aproject/a2a-python>

## The Agent Development Kit and A2A in practice

- **Build a cross-language multi-agent team with Google's Agent Development Kit and A2A** (Google Developers Blog, 22 June 2026) — a Python agent extracts contract terms and a Go agent validates them against policy, connected only by the Agent-to-Agent protocol and orchestrated by the Agent Development Kit; shows `RemoteA2aAgent` turning a remote A2A-compliant service into a local sub-agent.
  <https://developers.googleblog.com/build-cross-language-multi-agent-team-with-google-agent-development-kit-and-a2a/>
- **Unlock AI agent collaboration: convert Agent Development Kit agents for A2A** (Google Cloud Blog) — step-by-step conversion of a standalone Agent Development Kit agent that uses a Model Context Protocol tool into a fully Agent-to-Agent-compatible component, including the agent card, the agent executor, and an orchestrator that calls remote agents through the A2A client.
  <https://cloud.google.com/blog/products/ai-machine-learning/unlock-ai-agent-collaboration-convert-adk-agents-for-a2a>
- **Agent2Agent protocol (A2A) is getting an upgrade** (Google Cloud Blog, 31 July 2025) — the version 0.3 release, including gRPC support and signed agent cards, and the announcement of native Agent-to-Agent support in the Agent Development Kit.
  <https://cloud.google.com/blog/products/ai-machine-learning/agent2agent-protocol-is-getting-an-upgrade>
- **Google Agent Development Kit, Java application programming interface reference: `com.google.adk.a2a.agent`** — the `RemoteA2AAgent` class, which communicates with a remote Agent-to-Agent agent through an Agent-to-Agent client, and its `AgentCardResolutionError` and `TypeError` exceptions.
  <https://google.github.io/adk-docs/api-reference/java/com/google/adk/a2a/agent/package-summary.html>

## The syllabus inspiration (proprietary — structure only, no content copied)

- **A2A: The Agent2Agent Protocol — DeepLearning.AI** (published 11 February 2026; instructors Holt Skinner, Ivan Nardini and Sandi Besen; built in partnership with Google Cloud and IBM Research) — a healthcare multi-agent system built from three remote agents in different frameworks, orchestrated through sequential and hierarchical workflows, with a final lesson composing them through the BeeAI framework. Used as syllabus inspiration only, per the course conversion plan.
  <https://learn.deeplearning.ai/courses/a2a-the-agent2agent-protocol>

## Frameworks: LangGraph and BeeAI

- **LangGraph multi-agent systems** (LangGraph JavaScript documentation, mirrored from the Python documentation) — the supervisor and swarm architectures, and handoffs as a primitive with a destination and a payload; the supervisor controls all communication flow and task delegation, while a swarm lets agents hand off control to one another dynamically.
  <https://github.com/langchain-ai/langgraphjs/blob/HEAD/docs/docs/agents/multi-agent.md>
- **LangGraph supervisor (Python)** — the `langgraph-supervisor` library, which builds a supervisor agent whose handoff tools delegate to worker agents; the repository documents how to customise the handoff tools.
  <https://github.com/langchain-ai/langgraph-supervisor-py>
- **BeeAI framework** (IBM Research, Linux Foundation, Apache Licence 2.0) — an open-source Python and TypeScript framework for production-grade multi-agent systems, with an `A2AServer` adapter for exposing agents as Agent-to-Agent servers and an `A2AAgent` adapter for consuming remote Agent-to-Agent agents, plus rule-based requirement agents, event-driven middleware and OpenTelemetry observability.
  <https://github.com/i-am-bee/beeai-framework>

## When a swarm earns its complexity

- **Building effective agents** (Anthropic Engineering, December 2024) — the five workflow patterns: chaining, routing, parallelisation, orchestrator-workers and evaluator-optimiser; the orchestrator-workers pattern is defined as a central language model that dynamically breaks down tasks, delegates them to worker language models, and synthesises their results.
  <https://www.anthropic.com/engineering/building-effective-agents>
- **How we built our multi-agent research system** (Anthropic Engineering, 13 June 2025) — the orchestrator-worker architecture behind Claude Research, the eight prompting principles, and the measured trade-off: a Claude Opus 4 lead agent with Claude Sonnet 4 subagents outperformed a single Claude Opus 4 agent by 90.2% on Anthropic's internal research evaluation, while agents typically use about 4× more tokens than chat interactions and multi-agent systems about 15× more. The post also records the early failure mode of spawning 50 subagents for simple queries, and the effort ladder that fixed it (1 agent for fact-finding, 2–4 subagents for comparisons, more than 10 only for complex research).
  <https://www.anthropic.com/engineering/multi-agent-research-system>
- **Don't Build Multi-Agents** (Walden Yan, Cognition, 12 June 2025) — the counter-argument: share context and full agent traces rather than individual messages, and remember that actions carry implicit decisions, so conflicting decisions carry bad results; the worked example is two subagents building incompatible parts of the same game in parallel.
  <https://cognition.ai/blog/dont-build-multi-agents>

## The neighbouring protocol

- **Model Context Protocol specification** — the open protocol that connects an agent to its tools and data, as distinct from the Agent-to-Agent protocol, which connects an agent to other agents; the specification is defined by a TypeScript schema and uses JSON-RPC 2.0 messages with JSON Schema 2020-12 for validation.
  <https://modelcontextprotocol.io/specification/2025-11-25/basic>

## Further reading

- **Build with Claude Code** (ByteByteGo) — a paid live course; its public outline was used as a gap checklist for this chapter.
  <https://live.bytebytego.com/courses/claude-code>

## Style model for the course

- **The LLM course** — the style model for this whole repository: chapter shape, hands-on notebooks, and references.
  <https://github.com/mlabonne/llm-course>
