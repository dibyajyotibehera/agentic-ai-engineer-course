# Chapter 9 — References

Every link below was verified live on 18 November 2026. Where a number in
the lesson comes from a paper, it is the paper's own reported figure unless
the lesson says otherwise.

## Adapted material (with attribution)

- Microsoft, *AI Agents for Beginners*, Lesson 9: Metacognition —
  planning, self-correction, corrective retrieval, and the travel-agent
  worked example this chapter's metacognition framing is adapted from.
  MIT licence.
  https://microsoft.github.io/ai-agents-for-beginners/09-metacognition/
- Microsoft, *AI Agents for Beginners* (repository).
  https://github.com/microsoft/ai-agents-for-beginners

## The evaluator–optimiser pattern

- Anthropic, *Building effective agents* — the evaluator–optimiser
  workflow (one model generates, another evaluates in a loop), the
  guidance that it fits when evaluation criteria are clear and iterative
  refinement provides measurable value, and the stopping-condition advice
  for agents (a maximum number of iterations).
  https://www.anthropic.com/engineering/building-effective-agents

## The research foundations of self-correction

- *Reflexion:
  Language Agents with Verbal Reinforcement Learning*, NeurIPS 2023 —
  verbal self-reflection stored in episodic memory, no weight updates;
  reported 91.0% pass@1 on HumanEval against 80.1% for GPT-4 alone, and
  130 of 134 ALFWorld tasks completed.
  https://arxiv.org/abs/2303.11366
- *Self-Refine: Iterative Refinement with Self-Feedback*,
  NeurIPS 2023 — generate, self-feedback, refine with a single model;
  reported an average improvement of about 20 percentage points across
  seven tasks.
  https://arxiv.org/abs/2303.17651
- *CRITIC: Large Language Models Can Self-Correct with
  Tool-Interactive Critiquing*, ICLR 2024 — verify-then-correct against
  external tools (code interpreter, search engine); reports consistent
  improvements across free-form question answering, mathematical program
  synthesis and toxicity reduction.
  https://arxiv.org/abs/2305.11738
- *Large Language Models Cannot Self-Correct Reasoning Yet*,
  ICLR 2024 — the negative result this chapter is built around: intrinsic
  self-correction, with no external feedback, fails to improve accuracy
  and often degrades it.
  https://arxiv.org/abs/2310.01798
- *Corrective Retrieval Augmented Generation*,
  2024 — a lightweight retrieval evaluator grades documents as correct,
  ambiguous or incorrect and triggers query rewriting or web-search
  fallback; reported improvements of 7.0% on PopQA, 14.9% on the FactScore
  biography measure and 36.6% on PubHealth.
  https://arxiv.org/abs/2401.15884

## Self-healing coding agents in the wild

- *SWE-bench: Can Language Models Resolve Real-World
  GitHub Issues?*, ICLR 2024 — 2,294 real GitHub issues across 12 Python
  repositories; the best model at launch (Claude 2) resolved 1.96%.
  https://arxiv.org/abs/2310.06770
- *SWE-agent: Agent-Computer Interfaces Enable Automated
  Software Engineering*, NeurIPS 2024 — the agent–computer interface,
  bounded steps and execution feedback; reached 12.47% on SWE-bench with
  GPT-4 Turbo. Repository:
  https://github.com/SWE-agent/SWE-agent
  Paper: https://arxiv.org/abs/2405.15793

## Planning patterns that change the failure surface

- *ReWOO: Decoupling Reasoning
  from Observations for Efficient Augmented Language Models*, 2023 —
  plan first (planner, worker, solver), then execute; reported about
  5x token efficiency and a 4% accuracy improvement on HotpotQA
  against interleaved ReAct.
  https://arxiv.org/abs/2305.18323
- *Tree of
  Thoughts: Deliberate Problem Solving with Large Language Models*,
  NeurIPS 2023 — branch, evaluate partial states, backtrack; GPT-4
  on Game of 24 rose from 4% with chain-of-thought prompting to 74%.
  https://arxiv.org/abs/2305.10601

## Reward models and inference-time scaling

- *Let's Verify Step by Step*, ICLR 2024 — process
  supervision (a reward per reasoning step) against outcome
  supervision (a reward for the final answer only); the
  process-supervised verifier solved 78.2% of a representative MATH
  test subset under best-of-N search against 72.4% for the strongest
  outcome-supervised verifier; released PRM800K, 800,000 step-level
  human labels.
  https://arxiv.org/abs/2305.20050
- *Self-Consistency Improves Chain of Thought Reasoning
  in Language Models*, ICLR 2023 — sample many reasoning paths and
  take the majority answer; with PaLM 540B and 40 samples, GSM8K
  rose from 56.5% with greedy chain-of-thought to 74.4%.
  https://arxiv.org/abs/2203.11171
- *Scaling LLM Test-Time Compute Optimally
  can be More Effective than Scaling Model Parameters*, ICLR 2025 —
  the systematic study of spending inference-time compute: a
  compute-optimal allocation was more than 4x as efficient as naive
  best-of-N sampling, and a smaller model with test-time compute could
  outperform a model 14x larger where the small model already had a
  non-trivial chance of solving the question.
  https://arxiv.org/abs/2408.03314

## Tooling used by the code

- Python documentation, [`traceback` — Print or retrieve a stack
  traceback](https://docs.python.org/3/library/traceback.html) — the
  standard-library module `code/traceback_parser.py` is built on, including
  `TracebackException`, `StackSummary` and `FrameSummary`.

## Course context

- ByteByteGo, *Build with Claude Code* — a paid live course; its
  public outline was used as a gap checklist for this chapter, nothing
  more.
  https://live.bytebytego.com/courses/claude-code
- *LLM course* — the style model for this course (chapter
  shape, hands-on notebooks, references).
  https://github.com/mlabonne/llm-course
- This repository's [CONVERSION_PLAN.md](../CONVERSION_PLAN.md) — the full
  per-chapter provenance record, including what is adapted and what is
  original.
