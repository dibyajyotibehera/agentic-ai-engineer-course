# Chapter 11 — References

Every source this chapter draws on, with a real link. Every link below was verified live on 18 November 2026 by fetching the page or its search record; no URL was invented. Where a source is used only as syllabus inspiration, that is stated, and its content was not copied.

## The pattern and the fallback rules

- "CircuitBreaker." The original description of the circuit breaker pattern: the three states (closed, open, half-open), the failure threshold, the reset timeout, and the warning that not all errors should trip the circuit. Verified live.
  - https://martinfowler.com/bliki/CircuitBreaker.html
- LLMOps.si. "LLM Deployment: CI/CD, staging and one-step rollback." The source of the fallback rule used throughout this chapter: a fallback chain retries only 429, 500, 502, 503 and 504 — a 400 is your bug, not a retry — with exponential backoff and jitter, an evaluated fallback chain, alerting on fallback events, and a circuit breaker that skips a provider that is clearly down instead of paying the timeout on every request.
  - https://www.llmops.si/articles/llm-deployment-ci-cd-staging-rollback/
- LiteLLM. "Reliability — Retries, Fallbacks." The reference implementation for model fallbacks in a gateway: `completion()` with a `fallbacks` list, context-window fallbacks, and a Router with cooldowns for failing deployments. Cited as a reference implementation; the chapter's code re-implements the pattern in standard-library Python rather than depending on it.
  - https://Docs.Litellm.ai/docs/completion/reliable_completions

## Adapted material (MIT licence, with attribution)

- Microsoft. *AI Agents for Beginners*, Lesson 10, "AI Agents in Production: Observability and Evaluation." The provider-fallback note adapted in this chapter — "if LLM provider A is down, you switch to LLM provider B as backup" — and the router-model idea of sending simple queries to smaller models and reserving expensive large models for complex reasoning. MIT licence.
  - https://microsoft.github.io/ai-agents-for-beginners/10-ai-agents-production/
- Microsoft. *AI Agents for Beginners*, Lesson 15, "Building Computer Use Agents (CUA)." The Browser-Use with Playwright and Chrome DevTools Protocol hybrid, and the use of Pydantic for structured extraction, adapted here as the API-to-browser fallback rung. MIT licence.
  - https://microsoft.github.io/ai-agents-for-beginners/15-browser-use/
- Anthropic. *Claude Cookbook*, "Cost Optimization on the Claude API." The cost checklist adapted in this chapter: get the task working on a capable model, build an evaluation, measure a baseline cost per task, then sweep cheaper configurations. MIT licence.
  - https://platform.claude.com/cookbook/cost-optimization-cost-optimization
- Anthropic. *Claude Cookbook*, "Latency Optimization on the Claude API." The latency checklist: time to first token, time to last token and output tokens per second, with levers for input-token management, agent-loop efficiency, output-token management, model and effort selection, and bounding the slowest tasks. MIT licence.
  - https://platform.claude.com/cookbook/latency-optimization-latency-optimization
- Anthropic. *Claude Cookbook* repository (the notebooks behind the two cookbooks above).
  - https://github.com/anthropics/claude-cookbooks

## Syllabus inspiration only (proprietary — no content copied)

- DeepLearning.AI. "Semantic Caching for AI Agents" (Tyler Hutcherson and Iliya Zhechev, with Redis). Used as syllabus inspiration only for the caching discussion. Its content is proprietary and was not copied. Note the chapter's distinction: caching is cost optimisation, not a fallback, except where a cached answer explicitly serves as a rung on the fallback ladder.
  - https://www.deeplearning.ai/courses/semantic-caching-for-ai-agents
- ByteByteGo. "AI Cost Optimization". A paid live course; its public outline was used as a gap checklist for this chapter's cost-aware routing and semantic-cache hardening sections, and no lesson text, diagram or wording from it was copied.
  - https://live.bytebytego.com/courses/ai-cost

## The grounded numbers

- "Efficient Memory Management for Large Language Model Serving with PagedAttention." *Proceedings of the Symposium on Operating Systems Principles (SOSP)*, 2023. vLLM improves throughput by 2–4× at the same latency compared with FasterTransformer and Orca, with gains larger for longer sequences, larger models and more complex decoding algorithms.
  - https://arxiv.org/abs/2309.06180
- "GPT Semantic Cache: Reducing LLM Costs and Latency via Semantic Embedding Caching." arXiv:2411.05276 (2024). Semantic caching reduces API calls by up to 68.8%, with cache hit rates from 61.6% to 68.8% across query categories; a cosine similarity threshold of 0.8 is optimal in the paper's 0.6–0.9 sweep. **Do not conflate this figure with the GPTCache library** — the 68.8% figure is from this arXiv paper, not from GPTCache.
  - https://arxiv.org/abs/2411.05276
- *LLM course*, Deployment section. The serving alert thresholds used in this chapter: alert when 95th percentile latency exceeds 2 seconds, when the error rate exceeds 5%, or when memory usage exceeds 90%. The course is the style model for this whole repository.
  - https://github.com/mlabonne/llm-course

## Background reading

- Anthropic. "Building Effective Agents." The routing workflow — classify an input and direct it to a specialised follow-up, including routing easy questions to smaller models and hard ones to more capable models — which underlies the model-cascade ordering in this chapter.
  - https://www.anthropic.com/engineering/building-effective-agents
- Microsoft. *AI Agents for Beginners* repository (the source of Lessons 10 and 15 above).
  - https://github.com/microsoft/ai-agents-for-beginners
- The course conversion plan, which records the provenance of every chapter, including this one.
  - ../CONVERSION_PLAN.md

## What was deliberately not cited

No benchmark, price or paper claim appears in this chapter without a source above or in the lesson's "The numbers that matter" section. Thresholds used in the code that are not in the list above — a breaker tripping after five failures, a thirty-second reset timeout, a queue holding a thousand tasks — are chosen defaults for the exercises, labelled as such in the code, not measured results.
