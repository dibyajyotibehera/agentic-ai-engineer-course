# Chapter 10 — References

All links below were verified live on 18 November 2026. Where a link
could not be verified, that is stated rather than worked around.

## Provenance

This chapter's shape draws on three sources as syllabus inspiration
only — no prose, code, or diagrams were copied from any of them.
Microsoft Lessons 10 and 16 are adapted with attribution under
their MIT licence. The golden directed acyclic graphs (DAGs), the
trajectory-distance metrics, and the continuous-integration (CI)
regression gate as a release gate are original to this chapter; no
course teaches them. See the repository's
[CONVERSION_PLAN.md](../CONVERSION_PLAN.md) for the full
per-chapter provenance record.

## Adapted with attribution (MIT licence)

- Microsoft, AI Agents for Beginners, Lesson 10 — AI Agents in
  Production: Observability & Evaluation (traces and spans,
  OpenTelemetry, offline evaluation as the minimum step before
  deployment, evaluation in CI/CD pipelines, LLM-as-a-judge):
  https://microsoft.github.io/ai-agents-for-beginners/10-ai-agents-production/
- Microsoft, AI Agents for Beginners, Lesson 16 — Deploying Scalable
  Agents (evaluation gates, smoke tests, model routing, and the
  CI pipeline diagram in which offline evaluation gates the release):
  directory `16-deploying-scalable-agents/` in
  https://github.com/microsoft/ai-agents-for-beginners
  — the lesson's own documentation page could not be verified live
  in this pass, so the repository directory is cited instead of a
  documentation URL.

## Syllabus inspiration only (proprietary or unlicensed prose — nothing copied)

- DeepLearning.AI, Evaluating and Improving Agentic Applications
  (Arize AI; code and LLM judges, judge reliability, step and
  tool-call-order checks, regression tests):
  https://www.deeplearning.ai/courses/evaluating-and-improving-agentic-applications
- LangChain Academy, Building Reliable Agents with LangSmith
  (datasets, code-based evals, LLM-as-a-judge, pairwise evals,
  online evals, automations):
  https://www.beri.net/learning/langchain-academy-building-reliable-agents
  — the course page itself is on LangChain Academy; the link above is
  a verified catalogue entry describing it.

## Evaluation platforms and documentation

- LangSmith, Evaluation concepts (datasets, offline and online
  evaluation, code, LLM-as-a-judge, and pairwise evaluators):
  https://docs.langchain.com/langsmith/evaluation-concepts
- Ragas, List of available metrics (faithfulness, context recall,
  tool-call accuracy, agent goal accuracy, and the rest):
  https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/
- Ragas, Faithfulness (the formula, the worked example scoring
  1 / 2 = 0.5, and faithfulness with HHEM-2.1-Open):
  https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/
- Vectara, HHEM-2.1-Open model card (free, small, open-source T5
  classifier for the faithfulness claim-check step):
  https://huggingface.co/vectara/hallucination_evaluation_model

## Judge cost and reliability

- LangChain, Jev is now available in LangSmith Evals (the
  head-to-head study: Jev matched the human pass/fail label on all
  500 repeated decisions at $0.34 total against $28.17 for Claude
  Sonnet 4.6, about 83 times cheaper, with 92 to 913 times lower
  variance; one weather agent, five cases, one human reviewer):
  https://www.langchain.com/blog/jev-is-now-available-in-langsmith-evals
- The study above was surfaced in the course's research via a
  LinkedIn post on LangChain's agent-evals work;
  the post's own URL could not be verified live in this pass, so the
  LangChain blog post is the cited source for the numbers.

## Datasets, regression gates, and rollout

- LLMOps.si, How to build your first eval dataset (mine cases from
  real traces, 20 to 50 cases to start, about 100 to 200 once the set
  gates releases, wire it into CI):
  https://www.llmops.si/articles/how-to-build-your-first-eval-dataset/
- LLMOps.si, LLM Deployment: CI/CD, staging and one-step rollback
  (CI with an eval gate, canary from 5% of traffic, the verdict
  function with its 2,000-request minimum and four thresholds,
  rollback by configuration flip):
  https://www.llmops.si/articles/llm-deployment-ci-cd-staging-rollback/
- GitHub Docs, Workflow syntax for GitHub Actions (workflow files,
  triggers, jobs, and steps):
  https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions

## Harness engineering — verification as the completion rule

- OpenAI, Harness engineering (the Codex harness article):
  https://openai.com/index/harness-engineering/
- Anthropic, Effective harnesses for long-running agents:
  https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Anthropic, Harness design for long-running apps:
  https://www.anthropic.com/engineering/harness-design-long-running-apps

## Background reading

- Anthropic, Building effective agents (the evaluator–optimiser
  pattern and the case for small, verifiable evals before scaling
  agent complexity):
  https://www.anthropic.com/engineering/building-effective-agents
- LLM course (the style model for this whole course
  — chapter shape, hands-on notebooks, references; Apache 2.0):
  https://github.com/mlabonne/llm-course

## A note on the chapter's worked-example figures

The regression-gate figures used in the lesson — a criterion score
falling from 0.92 to 0.71, a suite of 100 to 300 cases running in
about two minutes, and a two-to-three point tolerance — were supplied
as verified facts in the course brief (checked November 2026) and
are used as the chapter's worked example, not attributed to any
single article above. The dataset-sizing figures (10 to 20 examples
of one behaviour drawn from real traces to start) come from the same
verified set, and the LLMOps.si dataset guide cited above gives the
corroborating progression of 20 to 50 cases to start and about 100
to 200 once the set gates releases.
