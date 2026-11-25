# Chapter 12 — Exercises

Seven exercises. Do them in order: the first two build the two hardest parts of a teardown — an honest decision record and a measured latency profile — the third assembles them, with the cost analysis, into the capstone document, and the last four practise the skills the capstone does not cover on its own: rescuing an agent that has already broken, distilling a behaviour without letting it go stale, watching cost while the agent runs, and tearing down a harness. All seven use the support copilot from the lesson (a ReAct loop resolving tickets through four Model Context Protocol tools: account state, subscription status, knowledge-base search, and refund eligibility, with LLM-as-a-judge evaluations as the production gate), or your own agent if you have one. Exercises 2, 3, 5, and 6 run the real copilot against a real model, so they need a model backend configured (key-based or Bedrock — see "Connect a model" in the root README) and each run costs three model calls — a few cents per run at current provider prices, so keep live runs to a handful and run long profiles in the background. Exercises 1, 4, and 7 need no model: Exercise 4 works from a deliberately constructed trace bundle (see below), not a live run.

## Exercise 1 — Write an architecture decision record for a real past decision

**Difficulty:** Foundation.

Pick one real decision from an agent you have actually built — the support copilot if you worked through the course, or a system from your own work. Good candidates are a choice you argued about at the time: loop versus directed acyclic graph, one judge model versus another, a tool the agent may call versus one it may not. Copy `code/adr_template.md` to a new file and fill it in, in full sentences, following the architecture decision record format (context, decision, status, consequences) plus the three agent-specific fields in the template (effect on evaluation, effect on cost per task, effect on latency).

**Acceptance criteria:**

- The record names at least two options you genuinely considered, including one you rejected, with the reason for the rejection stated, not implied.
- The consequences section lists at least one negative consequence alongside the positive ones.
- Each of the three agent-specific fields is filled in with a concrete effect ("adds one sequential model call, about 1–2 seconds, to every ticket"), not a vague one ("may affect latency").
- A reader who was not in the room can reconstruct why the decision was made and what would have to change for it to be revisited — the record states that trigger explicitly.

**Stretch:** write the superseding record for a decision you have since reversed, mark the original superseded, and link the two. A portfolio that shows a reversal, explained well, is more convincing than one that pretends the first design was right.

## Exercise 2 — Profile the latency of one agent run, end to end

**Difficulty:** Intermediate.

Take one real agent run — a single ticket through the support copilot, or one run of your own agent — and profile it with `code/latency_profiler.py`, which profiles the real copilot in `code/copilot.py` (the profiler's `profile()` context manager and `run_copilot()` show the shape). Run it at least 20 times over a small fixed set of tickets so you have a distribution rather than a single anecdote, and produce the profiler's report: total time per run, a flame-graph-style breakdown showing each step's share of the total, and the mean, median, 95th percentile, and maximum. Budget for it: 20 runs is 60 model calls, roughly $0.15 at current provider prices and around 15 minutes of wall time at the model latencies measured in the lesson, so start with `--runs 3` to see the shape and run the full 20 in the background.

**Acceptance criteria:**

- The report covers at least 20 runs over at least 5 distinct tickets, so the distribution is real.
- The flame-graph-style breakdown names the single slowest step and its share of total time, and states whether that step is sequential or parallel with the others.
- You state one concrete fix for the slowest step (a timeout with fallback, a cache, parallelisation, a smaller model for that step) and re-profile after applying it, reporting the before-and-after 95th percentile.
- You compare the 95th percentile against the Chapter 11 serving threshold (above 2 seconds trips an alert) and say whether the run, as profiled, would trip it.

**Stretch:** profile the same run twice, once with the four Model Context Protocol tools fanning out in parallel and once sequentially, and report the wall-clock difference alongside the token cost, which should be unchanged — the point of the exercise is to feel the cost-versus-latency trade-off in your own numbers.

## Exercise 3 — Capstone: produce a complete public teardown

**Difficulty:** Advanced.

Produce a complete public teardown of the support copilot, or of your own agent, in the shape of this chapter, and publish it as a public repository. The document has four parts, and each part names the earlier chapter whose technique it uses.

1. **Decisions.** At least one architecture decision record, in the architecture decision record format with the three agent-specific fields, covering a decision that shapes the whole system — for the copilot, the ReAct loop over a directed acyclic graph, the four-tool fan-out, or the choice of judge. A worked example is in `code/adr-001-react-loop-over-dag.md`. The record must name a rejected alternative and a negative consequence.
2. **Cost per task.** A cost-per-task breakdown built with `code/cost_per_task.py`, with the four lines — model cost, tool cost, memory cost, evaluation cost — priced from measured traces produced by running the real copilot (see `trace_from_run()` in the calculator). It must include the verified judge arithmetic from the LangChain study (Jev at $0.34 total versus Claude Sonnet 4.6 at $28.17 for the same 500-decision run, with the study's caveats stated: five cases from a single weather agent, language-model judges at provider defaults) and the computer-use fallback arithmetic from Anthropic's documentation (about 4,500 input tokens per request for the toolset, roughly 1,000–1,800 input tokens per screenshot, about 783,000 input tokens and about $1.61 for a 30-step task on Claude Sonnet 5.5 at list prices). Every other price in the table traces to a provider price page or a measured trace; any figure that is an assumption is labelled as one.
3. **Latency profile.** A profile produced by `code/latency_profiler.py` over a fixed set of runs, reporting the mean, median, 95th percentile, and maximum, with a flame-graph-style breakdown that names the slowest step, states the fix you applied, and shows the before-and-after 95th percentile against the Chapter 11 alert threshold (95th percentile above 2 seconds).
4. **Benchmark table.** A table scored against three baselines — the previous version of the agent, a simpler system (a single model call with retrieval and no loop), and a human baseline on the same tickets — using the same ticket set, scoring rubric, and judge for all four systems. The pass mark is stated before the run, the ticket count and sampling method are stated next to the numbers, and the caveats (what the benchmark does not cover) are written in the table, not in a footnote. Close with the canary verdict that shipped the version, using the Chapter 11 numbers: canary at 5% of traffic, at least 2,000 canary requests, error rate at most stable × 1.2 + 0.001, 95th-percentile latency at most stable × 1.15, evaluation pass rate at least stable − 0.02, cost per request at most stable × 1.10.

**Acceptance criteria:**

- All four parts are present, each naming the earlier chapter or chapters it draws on.
- The repository contains the code, the measured traces and the code that produced them, and the benchmark harness, so a stranger can reproduce the numbers; the teardown links to them.
- No number in the document is invented: every price, token count, and score traces to a source named in the document, a measured trace, or a clearly labelled assumption.
- The document states at least two things the agent does badly and at least one thing the benchmark does not cover. A teardown with no admitted weakness is a demo, and it fails this exercise.
- The whole document reads as one connected argument, not four disconnected sections: each part refers back to the decisions in part one.

**Stretch:** send the published teardown to one peer and one hiring manager or senior engineer you trust, ask each of them for the single claim they find least convincing, and revise the document to answer it. Record the exchange as a short appendix: it is the cheapest design review you will ever get.

## Exercise 4 — Rescue a failing agent (capstone variant)

**Difficulty:** Advanced.

You are handed a failing support copilot, not a healthy one. Run `python code/rescue_fixture.py` to generate the trace bundle (`code/rescue_bundle.json`): a baseline week and a failing week, each with a cost table, a latency profile over 20 runs, and evaluation scores over a fixed set of 20 tickets. The bundle is the chapter's one deliberate stand-in: its figures are constructed worked-example numbers, labelled ILLUSTRATIVE in the bundle, because a rescue exercise needs a failing agent whose failure is known to the exercise and hidden from the student — diagnose from the shape of the change between the two weeks, not from the absolute dollars. The headline is that the mean cost per task has roughly tripled — about 2.9 times the baseline, on the bundle's illustrative figures — while the evaluation pass rate has barely moved, from 95% to 90%, so the agent is paying much more for work that is no better. Diagnose it with the instruments from this chapter, and write the rescue plan as architecture decision records.

Work in this order, and show your working at each step rather than jumping to a fix. First, price the change: run the bundle's cost tables through the four lines of `code/cost_per_task.py` and say, line by line, where the extra dollars went — model, tools, memory, or evaluation — and, for the model line, split the rise into its two possible causes, more tokens per task versus a higher price per token, using the token counts in the bundle. Second, profile the change: compare the two weeks' latency distributions (mean, median, 95th percentile, maximum) and name the step the extra time belongs to, using the profiler's flame-graph habit of asking which span grew. Third, check the benchmark: the evaluation pass rate barely moved, so say explicitly what that rules out — the agent did not get worse at the graded task — and what it does not rule out, since a flat pass rate on 20 tickets says nothing about the ticket shapes the benchmark does not cover. Fourth, name the causes: the bundle's shape points at three of them, a model swap to a larger model at higher per-token prices, a loop that now runs longer and retries more, and context that has grown between the two weeks, and your diagnosis must separate which of the three is doing most of the damage, with a number attached to each, rather than blaming all three equally. Fifth, write the rescue plan as at least three architecture decision records, in the architecture decision record format with the three agent-specific fields, one per fix — for example, reverting or routing around the model swap, restoring the loop's stopping rules, and capping the context — each naming the evidence that justifies it, the cost and latency effect you expect, and the revisit trigger that would reopen it.

**Acceptance criteria:**

- The diagnosis states, with numbers from the bundle, how much of the cost rise sits in each of the four lines, and splits the model line into a token-count effect and a price-per-token effect.
- The latency comparison names the 95th percentile for both weeks and says whether either week trips the Chapter 11 alert threshold (95th percentile above 2 seconds).
- The rescue plan is at least three architecture decision records, each with a rejected alternative, a negative consequence, the three agent-specific fields filled in concretely, and a revisit trigger.
- At least one record's fix is verified by re-running the numbers: re-price the task with the fix applied (for example, the old model prices on the failing week's token counts) and show the cost per task that fix would produce, labelled as a worked calculation on the bundle's illustrative figures.
- The plan says what you would measure in the week after the rescue to know it worked, naming the canary verdict thresholds from Chapter 11.

**Stretch:** write the incident as a one-page teardown appendix of its own — the way the failure would appear in the public teardown if the failing week had shipped — including the two things the benchmark failed to catch and why.

## Exercise 5 — Distil the router, then break it on purpose (explain-back)

**Difficulty:** Intermediate.

Take the copilot's routing decision — which of the four tools to call first on a ticket, or whether the ticket needs a refund check at all — and design a distillation of it into a lightweight student model, on paper first and in code only if you have a small model to hand. Build the training set from the big model's own decisions: collect at least 50 routing decisions from real traffic — run the copilot in `code/copilot.py` over the ticket set and log the plan call's routing decision and the judge's verdict on each run — label each with the route chosen and whether the ticket resolved, keep the decisions that resolved and drop the ones that did not, and write down the labelling rule you used, since the student can only be as honest as its labels. Then cost the distillation: the one-off price of generating the labels and training the student, plus the evaluation run on a held-out set of tickets that proves the student matches the teacher, set against the per-ticket saving multiplied by your monthly ticket volume, and state the payback point — how many tickets the student must serve before the build pays for itself — as a worked calculation with every figure labelled as an assumption or a measured trace. Then break it on purpose: change one routing rule, the way a refund-policy change would, and show what happens next — the prompted copilot picks the new rule up from the updated knowledge base, while the distilled router keeps routing the old way — and write the retrain trigger that catches it, tied to the same version stamp the retrieval system uses.

**Acceptance criteria:**

- The training-set design states the labelling rule, the keep/drop rule for failed decisions, and the size of the held-out evaluation set.
- The payback calculation is shown in full, with the one-off build cost, the per-ticket saving, and the monthly volume each labelled as an assumption or a trace.
- The staleness test is demonstrated, not asserted: the changed rule and the router's old behaviour are both written down, and the version-stamp check that would block the stale student from serving is stated as a concrete mechanism (what is stamped, where the check runs, what happens when it fails).

**Explain-back:** stand in front of a reviewer and explain this sentence — "your distilled router is cheaper and wrong after a policy change" — as one connected story, not a list: what the student learned and when it learned it, why the prompted model adapts and the distilled one does not, what the version stamp is and where it is checked, and what the retraining run needs before the student may serve again. Record yourself, play it back, and check that each idea leads into the next; if the explanation jumps, rewrite the trigger until it flows.

## Exercise 6 — Make cost observable: spans, attribution, and unit economics

**Difficulty:** Intermediate.

Extend the cost-per-task calculator's attribution rollup in `code/cost_per_task.py` into a small observability story for one conversation. Take the measured traces the calculator produces from live copilot runs, attribute each step's cost to its task with `attribute_steps()`, roll the tasks up to a per-conversation total with `rollup_conversation()`, and then answer three questions from the numbers. First, the spans question: sketch the span tree one conversation would produce under the OpenTelemetry GenAI semantic conventions — one agent-invocation span at the root, one chat span per model call named after the model, one tool-execution span per tool call — and list the attributes each span must carry for the cost to be computable later (model name, input and output token counts, operation and provider names), marking which attributes are metadata and which would be content you have chosen not to capture. Second, the attribution question: decide the hierarchy for your own agent — per call, per task, per user or team, per product — and write down, for each level, the tag that must be attached when the span is emitted and what breaks if it is missing, because a tag added after the bill arrives cannot split an aggregated total. Third, the amplifiers question: pick one amplifier from the lesson — tool schemas re-sent on every call, retrieved documents re-sent on every loop iteration, or multi-agent fan-out — and measure it on the measured traces, showing the multiplier as a number (for example, how many times the retrieved context is paid for across the loop's model calls) and the fix you would apply, priced before and after.

**Acceptance criteria:**

- The per-conversation rollup is printed from the extended calculator, with the four lines shown as totals and as mean cost per task, and the conversation, owner, and product tags stated.
- The span tree names the span for each model call and each tool call in one conversation and lists the cost-bearing attributes on each, with content attributes explicitly marked as not captured.
- The attribution hierarchy is written down level by level, with the tag for each level and the consequence of omitting it.
- One amplifier is measured as a multiplier on the measured traces, with a before-and-after price for the fix, each figure labelled as a measured trace or a worked calculation on it.

**Stretch:** add a second conversation to the rollup, owned by a different team, and show that the per-team totals add up to the product total — the check that catches double-counting or a dropped tag before the bill does.

## Exercise 7 — Tear down the harness of a tool you already use

**Difficulty:** Intermediate.

Pick one agent tool you already use every week — a coding agent in your editor, a command-line agent, or the support copilot from this course — and tear down its harness using the five-subsystem frame from the lesson: instructions, state, verification, scope, lifecycle. For each subsystem, write one paragraph stating what the harness lets the agent see, touch, and do, and what it forbids or hides, grounding each claim in one thing you have actually observed: a file the agent could not see, a rule it followed, a time it forgot earlier context, a check that caught its mistake, a task it refused. Then name the one design decision that defines the harness and what it trades away, the way the lesson does for the four production harnesses. Close by answering this: if this agent misbehaved tomorrow, which subsystem would you investigate first, and what evidence would confirm or rule it out?

**Acceptance criteria:**

- All five subsystems are covered, each with at least one observed behaviour, not a feature-list claim copied from documentation.
- The defining decision is stated as a decision with a trade-off, naming what is given up, not only what is gained.
- The closing answer names one subsystem to investigate first for a misbehaving agent, with the evidence that would confirm or rule it out.
- No claim about the tool's internals that you have not observed or that is not in its public documentation; anything inferred is labelled as inferred.

**Stretch:** repeat the teardown for a second tool and compare the two harnesses on one subsystem — for example, how each one handles state across sessions — and state which design you would borrow for your own agent and why.
