# Chapter 12 — Code

Standard library only; no dependencies to install. Python 3.11 or later.

| File | What it is |
| --- | --- |
| `cost_per_task.py` | Prices one agent task across four lines — model, tools, memory, evaluation — and prints the two verified reference figures for the chapter (judge economics from the LangChain study; computer-use token arithmetic from Anthropic's documentation). Also implements the attribution rollup: per-step costs attributed to a task, rolled up to a per-conversation total (`attribute_steps()`, `attribute_task()`, `rollup_conversation()`). Run it: `python cost_per_task.py`. The mock trace uses illustrative token counts; replace them with measured traces before publishing a teardown. |
| `rescue_fixture.py` | Generates a deterministic failing-agent trace bundle for Exercise 4 (cost per task roughly tripled, evaluation pass rate barely moved): baseline and failing weeks with a cost table, a latency profile, and evaluation scores, written as `rescue_bundle.json`. Run it: `python rescue_fixture.py`. Standard library only; no randomness, so repeated runs are byte-identical. |
| `latency_profiler.py` | Wraps an agent run, records every step and tool call, and prints a flame-graph-style breakdown plus the mean, median, 95th percentile, and maximum across runs. Run it: `python latency_profiler.py`. The demo profiles a mock copilot ticket with `time.sleep` standing in for real work, so it runs with no model API key. |
| `adr_template.md` | Architecture decision record template in the standard format, with the three agent-specific fields this chapter adds: effect on evaluation, effect on cost per task, effect on latency. |
| `adr-001-react-loop-over-dag.md` | Worked example: the support copilot's decision to use a free-form ReAct loop rather than a fixed directed acyclic graph, with its stopping rules and its revisit triggers. |
