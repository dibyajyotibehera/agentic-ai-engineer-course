# Chapter 12 — Code

Standard library only; no dependencies to install. Python 3.11 or later.

Running the live code needs a model backend: see "Connect a model" in the
root README, which covers the two paths — key-based (any OpenAI-compatible
API) or AWS Bedrock. The deterministic tests need no backend; the live
tests skip when none is configured. Run the tests with
`python -m unittest test_chapter12 -v` from this directory.

| File | What it is |
| --- | --- |
| `llm_client.py` | The Chapter 7 client, copied verbatim so this chapter is self-contained: a standard-library OpenAI-compatible chat client with retries, structured JSON replies, and token/cost totals. It never invents a reply; a missing key or a bad response is a loud error. |
| `copilot.py` | The real support copilot the chapter tears down: a plan call and a draft call to a real model, four local tools (account state, subscription status, knowledge-base search, refund eligibility) reading local ticket and knowledge-base data, and a judge call on the configured judge model (`LLM_JUDGE_MODEL`, defaulting to the main model) grading the draft. Token counts are the provider's measured usage, differenced per call. One number is an estimate, labelled as such in the file: `memory_tokens`, the carried context's character share of the draft prompt applied to that call's measured prompt tokens, because there is no local tokenizer to count it exactly. |
| `cost_per_task.py` | Prices one agent task across four lines — model, tools, memory, evaluation — from measured traces produced by `copilot.py` (`trace_from_run()`), and prints the two verified reference figures for the chapter (judge economics from the LangChain study; computer-use token arithmetic from Anthropic's documentation). Also implements the attribution rollup: per-step costs attributed to a task, rolled up to a per-conversation total (`attribute_steps()`, `attribute_task()`, `rollup_conversation()`). Run it: `LLM_API_KEY=<key> LLM_BASE_URL=<base URL> LLM_MODEL=<model> python cost_per_task.py` (or the Bedrock form — see "Connect a model" in the root README). Pricing defaults to $2.00 input / $10.00 output per million tokens for the configured model (override with `LLM_INPUT_PRICE_PER_MILLION` / `LLM_OUTPUT_PRICE_PER_MILLION`); the judge line is priced at the same defaults, and the run also prints the provider-reported cost when the provider reports one. Measured on 22 November 2026 at the default prices: mean $0.0199 per ticket over three tickets, worst $0.0222, $199.46/month at an assumed 10,000 tickets/month. |
| `latency_profiler.py` | Wraps the real copilot run, records every step and tool call, and prints a flame-graph-style breakdown plus the mean, median, 95th percentile, and maximum across runs. Run totals are wall-clock time; the four tools really run concurrently under a thread pool (or sequentially with `--sequential`). Run it: `LLM_API_KEY=<key> LLM_BASE_URL=<base URL> LLM_MODEL=<model> python latency_profiler.py [--runs N]` (or the Bedrock form — see "Connect a model" in the root README). Measured on 22 November 2026 (3 runs, the configured model): mean 50.88s, median 50.73s, p95 59.69s, slowest step the draft-resolution model call at a mean of 26.24s, 3/3 drafts passing the judge — which trips the Chapter 11 alert threshold (p95 above 2 seconds) by a wide margin, because the latency is model latency, not tool latency: the local tools take about 0.00s each. |
| `test_chapter12.py` | Tests for the chapter: deterministic tests for the profiler maths, the cost arithmetic, the attribution rollup, the rescue bundle, and the copilot's local tools (no model needed), plus live tests that run the real copilot and skip without a backend. |
| `rescue_fixture.py` | Generates a deterministic failing-agent trace bundle for Exercise 4 (cost per task roughly tripled, evaluation pass rate barely moved): baseline and failing weeks with a cost table, a latency profile, and evaluation scores, written as `rescue_bundle.json`. Run it: `python rescue_fixture.py`. Standard library only; no randomness, so repeated runs are byte-identical. |
| `adr_template.md` | Architecture decision record template in the standard format, with the three agent-specific fields this chapter adds: effect on evaluation, effect on cost per task, effect on latency. |
| `adr-001-react-loop-over-dag.md` | Worked example: the support copilot's decision to use a free-form ReAct loop rather than a fixed directed acyclic graph, with its stopping rules and its revisit triggers. |

## The one deliberate stand-in

The rescue bundle (`rescue_fixture.py` → `rescue_bundle.json`) is the
only mock in this chapter, and it stays a mock on purpose: the
brokenness is the exercise. Exercise 4 hands the student a failing
agent to diagnose, and a diagnosis exercise needs a failing agent
whose failure is known to the exercise but hidden from the student, so
the bundle's figures are deliberately constructed worked-example
numbers — labelled ILLUSTRATIVE in the bundle itself — with the cost
rise, the longer loop, and the grown context built in by hand. They
are not measured runs and are never presented as teardown numbers;
the diagnosis is graded on the shape of the change between the two
weeks, not on the absolute dollars. Everything else in this chapter —
the copilot, its latency profile, and its cost table — is measured
from real model calls and real local execution.
