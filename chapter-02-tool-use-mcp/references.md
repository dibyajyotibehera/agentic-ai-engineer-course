# References — Chapter 2: Tool Use & MCP

Every link below was verified live on 18 November 2026, by web search or by
opening the page directly. Where a source could only be verified through
search results rather than a direct open, that is noted.

## The protocol

- **Model Context Protocol specification, revision 2026-07-28** — the
  authoritative specification, and the revision this chapter implements.
  Verified by opening the page directly.
  <https://modelcontextprotocol.io/specification/2026-07-28>
- **JSON-RPC 2.0 specification** — the message format the protocol is built
  on: requests, responses, notifications and the standard error codes.
  <https://www.jsonrpc.org/historical/json-rpc-2-0.html>
- **MCP 2026 spec changes: stateless, extensions and direct discovery** —
  the Agentic AI Foundation's account of the 2026-07-28 revision, useful
  for the reasoning behind the removal of the handshake and sessions.
  <https://aaif.io/blog/mcp-2026-changes-the-rules>

## Frameworks and SDKs

- **FastMCP documentation** — the standard Python framework for building
  Model Context Protocol servers and clients; the FastMCP 1.0 application
  programming interface was incorporated into the official MCP Python
  software development kit in 2024.
  <https://gofastmcp.com/>
- **FastMCP: getting started** — the welcome page of the FastMCP
  documentation, with installation and the quickstart server build.
  <https://gofastmcp.com/docs/getting-started/welcome>

## Adapted material and syllabus inspiration

- **Microsoft AI Agents for Beginners, Lesson 11: Using Agentic Protocols
  (MCP, A2A and NLWeb)** — the skeleton this chapter adapts, under the
  MIT licence, with attribution. The lesson covers the Model Context
  Protocol alongside the Agent-to-Agent protocol and Natural Language Web.
  <https://microsoft.github.io/ai-agents-for-beginners/11-agentic-protocols/>
- **Microsoft MCP for Beginners** — Microsoft's open-source Model Context
  Protocol curriculum, updated for the 2026-07-28 specification, with
  hands-on server and client examples in Python and other languages.
  MIT licence.
  <https://github.com/microsoft/mcp-for-beginners/>
- **Microsoft AI Agents for Beginners (repository)** — the source
  repository for the adapted lesson above, MIT licence.
  <https://github.com/microsoft/ai-agents-for-beginners>
- **DeepLearning.AI, "MCP: Build Rich-Context AI Apps with Anthropic"** —
  syllabus inspiration only. DeepLearning.AI courses are proprietary: no
  content — text, code or diagrams — was copied. The course builds a
  FastMCP server and an MCP-compatible chatbot; it runs 1 hour 58 minutes
  at intermediate level and was published on 14 May 2025, so it predates
  the stateless 2026-07-28 revision.
  <https://learn.deeplearning.ai/courses/mcp-build-rich-context-ai-apps-with-anthropic/lesson/hg6oi/chatbot-example>

- **ByteByteGo, "Build with Claude Code"** — a paid live course by John
  Kim, listed here as further reading only. Its public outline was used
  as a gap checklist for this chapter; no content — text, code or
  diagrams — was copied.
  <https://live.bytebytego.com/courses/claude-code>

## Agent skills

- **Agent Skills specification** — the open standard for packaging
  reusable procedures as `SKILL.md` files, with YAML frontmatter
  (`name`, `description`) and progressive disclosure: name and
  description at discovery, the full body on activation, reference
  files only when needed.
  <https://agentskills.io>

## Gateways and observability

Project home pages for the reference shelf in the lesson. Check current
pricing and documentation on each before adopting it.

- **LiteLLM** — open-source proxy unifying providers behind one
  OpenAI-compatible endpoint.
  <https://github.com/BerriAI/litellm>
- **Portkey** — AI gateway with guardrails, fallbacks and budgets.
  <https://portkey.ai>
- **Kong AI Gateway** — artificial-intelligence plugins on the Kong API
  gateway.
  <https://konghq.com>
- **Cloudflare AI Gateway** — managed edge logging, caching, rate
  limits and fallbacks for model calls.
  <https://developers.cloudflare.com/ai-gateway/>
- **OpenRouter** — hosted marketplace for model access through one key.
  <https://openrouter.ai>
- **Langfuse** — open-source tracing, prompt management and evaluations.
  <https://langfuse.com>
- **LangSmith** — the LangChain team's hosted tracing and evaluation
  platform.
  <https://www.langchain.com/langsmith>
- **Helicone** — open-source proxy logging for prompts, cost and
  latency.
  <https://www.helicone.ai>
- **Arize Phoenix** — OpenTelemetry-native tracing and evaluations.
  <https://github.com/Arize-ai/phoenix>
- **Weights & Biases Weave** — tracing from Weights & Biases.
  <https://wandb.ai>
- **Braintrust** — evaluation-first observability.
  <https://www.braintrust.dev>

## Tool design

- **Anthropic, "Building effective agents"** — the patterns paper this
  course treats as background: start simple, use workflows before agents,
  and design tools carefully. The current page notes that much of the
  tooling described has changed since December 2024.
  <https://www.anthropic.com/research/building-effective-agents>
- **Anthropic, "Writing tools for agents"** — how to write high-quality
  tools and evaluate them, including namespacing tools, returning
  meaningful context, and optimising tool responses for token efficiency.
  Link verified through search results; a direct open was rate-limited
  at verification time.
  <https://www.anthropic.com/engineering/writing-tools-for-agents>

## Validation

- **Pydantic documentation: BaseModel** — the validation library used in
  `code/schemas.py` for strict tool schemas.
  <https://pydantic.dev/docs/validation/2.7/api/pydantic/base_model/>

## Mapping to the production stack (Mastra and Amazon Bedrock)

- **Mastra documentation: MCP connections** — connecting to MCP servers
  with `MCPClient`, static versus dynamic tool configuration, and
  exposing Mastra tools through `MCPServer`. States that both speak the
  MCP 2026-07-28 revision over standard input/output and Streamable HTTP.
  <https://mastra.ai/docs/connections/mcp>
- **Mastra reference: MCPClient** — the `MCPClient` reference, including
  `requireToolApproval`, typed client contracts, and the warning to treat
  tool responses as untrusted input.
  <https://mastra.ai/reference/tools/mcp-client>
- **Mastra documentation: built-in MCP server** — the server that exposes
  agents, workflows and tools at `/api/mcp`, where each MCP tool runs
  through the same validation, authentication, role-based access control
  and ownership checks as the equivalent HTTP request.
  <https://mastra.ai/docs/server/mcp>

## Style model for the course

- **The LLM course** — the style model for the whole
  repository: chapter shape, hands-on notebooks and references. Apache 2.0
  licence; cited as a model, not copied from.
  <https://github.com/mlabonne/llm-course>
```

