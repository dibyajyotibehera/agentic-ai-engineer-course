# Chapter 12 — References

Every source this chapter draws on, with a real link. All links were verified live on 18 November 2026. Figures in the lesson trace to the sources named here or to the course's own verified facts; nothing in this chapter invents a benchmark, a price, or a paper claim.

## The teardown's borrowed fragments (provenance)

- Hugging Face Agents Course — the final project is the model for this chapter's benchmark section: build an agent, evaluate it on a subset of the GAIA benchmark, score 30% or higher to earn the certificate, and publish the result on a student leaderboard. Repository (Apache License 2.0): https://github.com/huggingface/agents-course
- Hugging Face Agents Course, Unit 4 — final project introduction, including the 30% pass mark and the student leaderboard: https://huggingface.co/learn/agents-course/unit4/introduction
- GAIA benchmark leaderboard — the public leaderboard for the benchmark the Hugging Face course uses as its final project (a Hugging Face Space; the page renders client-side): https://huggingface.co/spaces/gaia-benchmark/leaderboard
- Claude Cookbook (Anthropic) — MIT-licensed collection of notebooks and recipes; the cost-optimisation recipe measures pass rate and cost per task to find the Pareto-optimal configuration, and the latency-optimisation recipe measures time to first token, task time, and accuracy to pick the fastest configuration that clears the quality bar. The two recipe directories are `cost_optimization` and `latency_optimization` inside the repository: https://github.com/anthropics/claude-cookbooks
- Microsoft AI Agents for Beginners, Lesson 10 — AI Agents in Production: Observability and Evaluation (MIT licence). The source of this chapter's metric vocabulary: latency and cost per run, request errors, accuracy, and user feedback, tracked through traces and spans, with OpenTelemetry as the observability standard. Lesson page: https://microsoft.github.io/ai-agents-for-beginners/10-ai-agents-production/
- Microsoft AI Agents for Beginners — repository (MIT licence), 18 lessons: https://github.com/microsoft/ai-agents-for-beginners

## Architecture decision records

- "Documenting Architecture Decisions" (2011) — the original architecture decision record format: context, decision, status, consequences; records numbered sequentially, never edited after acceptance, marked superseded rather than deleted when reversed. The text is released under CC0: https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions
- architecture-decision-record — the standard template and example collection, including the MADR template: https://github.com/joelparkerhenderson/architecture-decision-record

## Cost, latency, and evaluation evidence

- Daniel Shea and Seán Roche, "Can Jev Be a Better Agent Evaluator?", LangChain Blog, 20 September 2026 — the judge study cited in this chapter. Jev, a decision model from TypeSafe AI, matched a human reviewer's pass-or-fail labels on all 500 repeated decisions across five captured agent runs, at a total cost of $0.34; Claude Sonnet 4.6 as judge matched the human labels on 80.0% of the same decisions and cost $28.17. The authors call the experiment narrow and the results promising but early: five cases from a single weather agent, language-model judges run at provider defaults, and the Jev service version unrecorded. Read the caveats alongside the numbers: https://www.langchain.com/blog/jev-agent-evals-langsmith
- Anthropic, "Computer use tool" — platform documentation. Declaring the `computer_toolset_20260801` toolset adds about 4,500 input tokens per request (about 4,590 on Claude Sonnet 5); each screenshot is billed as image input. The worked 30-step, ~783,000-input-token, ~$1.61 figure in the lesson is the course's arithmetic on these published token counts at Claude Sonnet 5.5 list prices: https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
- Anthropic, "Building effective agents" (December 2024) — the workflows-versus-agents distinction, the five workflow patterns (prompt chaining, routing, parallelisation, orchestrator-workers, evaluator-optimiser), the advice that agentic systems trade latency and cost for better task performance, and the customer-support agent as the canonical example: https://www.anthropic.com/engineering/building-effective-agents
- OpenTelemetry GenAI semantic conventions — the agreed names for telemetry from model calls, agents, and tools: agent-invocation, chat, and tool-execution spans carrying model names, token counts, and operation and provider names. Cited by name without a link: the conventions have moved from the OpenTelemetry documentation site into their own repository and are still marked as in development, so no single documentation URL is stable enough to cite. Pin the conventions version next to your instrumentation version.

## Further reading — paid live courses used only as a gap checklist

- ByteByteGo, "AI Cost Optimization" — a paid live course; its public outline was used as a gap checklist for this chapter's cost-observability material: https://live.bytebytego.com/courses/ai-cost
- ByteByteGo, "Build with Claude Code" — a paid live course; its public outline was used as a gap checklist for this chapter's operating-at-scale framing: https://live.bytebytego.com/courses/claude-code

## Course context

- LLM course — the style model for this whole course (chapter shape, hands-on notebooks, references), Apache License 2.0: https://github.com/mlabonne/llm-course
- Agentic AI Engineer Course, CONVERSION_PLAN.md — the per-chapter provenance record for this repository, including the licensing rules applied to adapted material: ../CONVERSION_PLAN.md

## Course-internal verified facts used in this chapter

The following figures were verified during the course build (November 2026) and are stated in the lesson without an external link because they come from the course's own earlier chapters and checks, not from a public page. They are listed here so the reader can see exactly which numbers are course-internal:

- Serving alert thresholds (Chapter 11): 95th-percentile latency above 2 seconds, error rate above 5%, memory usage above 90%.
- Canary rollout verdict (Chapter 11): canary at 5% of traffic; verdict needs at least 2,000 canary requests; error rate at most stable × 1.2 + 0.001; 95th-percentile latency at most stable × 1.15; evaluation pass rate at least stable − 0.02; cost per request at most stable × 1.10.
