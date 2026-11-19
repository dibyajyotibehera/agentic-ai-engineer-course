# Chapter 11 — Exercises

Work through these in order: each one builds on the last, and the final exercise is the drill that proves the whole ladder. All exercises run with the Python standard library only, against the mock providers and tools in `code/` — no API key and no paid service is needed for any core exercise. Where a threshold is given as a number (five failures, thirty seconds, a thousand queued tasks), treat it as a chosen default for the exercise, not a measured result; part of the work is justifying the number you would use in production.

## Exercise 1 — A circuit breaker that trips and recovers

**Difficulty:** Foundation.

Build a circuit breaker around a flaky model provider, starting from `code/circuit_breaker.py`, and make it trip and recover on demand.

1. Configure the breaker with a failure threshold of 5, a reset timeout of 30 seconds, and a rule that only retryable failures trip it: HTTP status codes 429, 500, 502, 503 and 504, connection failures, and timeouts. A 400, an authentication failure and a validation error must **not** count towards the threshold.
2. Drive it with the mock provider in `code/circuit_breaker.py`, which you can set to fail a fixed number of times and then recover. Send calls until the breaker opens, and confirm that the next call fails immediately without the mock provider being invoked at all — count the mock's invocations to prove it.
3. Advance the clock past the reset timeout (the breaker accepts an injectable clock, so use a fake clock rather than waiting 30 real seconds) and send a trial call. Confirm the breaker is half-open, that a successful trial closes it and resets the failure count, and that a failed trial reopens it and restarts the timeout.
4. Add logging of every state transition, with the cause, and a method that reports the breaker's current state for monitoring.

**Acceptance criteria:** the breaker opens after exactly 5 retryable failures and not before; a 400 does not move the failure count; while open, zero calls reach the mock provider; after the reset timeout a successful probe closes the breaker and a failed probe reopens it; every transition is logged with its cause. `python -m py_compile code/circuit_breaker.py` passes, and `python code/circuit_breaker.py` prints a trip-and-recover trace.

**Stretch:** trip on a failure *rate* (for example 50% over the last 20 calls, with a minimum of 10 calls before the rate is trusted) instead of a count, and show a traffic pattern where the count version and the rate version disagree about whether to trip — then write down which one you would ship and why.

## Exercise 2 — Model cascade: frontier fails, cheaper answers, cache answers, refusal

**Difficulty:** Intermediate.

Build the full fallback ladder in `code/model_cascade.py`: frontier model → cheaper model from the same provider → model from a different provider → cached answer → refusal, with a cost tally per path.

1. Give each rung a price per call in abstract cost units (not real money — use 10 units for the frontier model, 2 for the cheaper model, 8 for the different provider, 0 for the cache, 0 for the refusal) and a canned answer quality label. Run the cascade against a mock frontier provider that fails with a 503, and record which rung served the request and what it cost.
2. Repeat with each rung failing in turn, so that every path through the ladder is exercised: frontier only, frontier + cheaper, all the way down to refusal. For each path, record the rung that served it, the total cost, and the number of calls made.
3. Add the evaluation gate: give each rung a small evaluation set of five questions with known-good answers, and only let a rung serve a request if it passes at least 4 of 5. Show what happens when the cheaper model passes the gate and when it fails it — the cascade must skip a rung that fails its gate rather than serving its ungraded output.
4. Add the classification rule from the lesson: a 400 from the frontier model must **not** trigger the cascade at all — it must surface immediately as a caller bug, with no fallback call made.

**Acceptance criteria:** every path through the ladder produces a result that names the serving rung and its cost; the cost tally is exact for each path (for example the refusal path costs 0 after all model rungs have failed, and the tally includes every attempted call, not just the successful one); a rung that fails its evaluation gate is skipped; a 400 surfaces immediately with no fallback attempted. `python code/model_cascade.py` prints a table of all paths with rung, cost and call count.

**Stretch:** reorder the second and third rungs (different provider before cheaper same-provider) and argue, in a short paragraph, which order fits a provider-wide outage and which fits a rate limit — then make the order configurable per failure type, so a 429 goes one way and a 503 goes the other.

## Exercise 3 — A cost kill-switch that stops an agent mid-run

**Difficulty:** Intermediate.

Build a metered agent loop in `code/cost_kill_switch.py` that halts safely when a budget is exceeded.

1. Implement the meter: every model call reports its input and output token counts, and the meter converts them to cost using a price table you supply (use abstract cost units per thousand tokens, not real prices). The meter must count retries, fallback calls and sub-agent calls — every call the run makes, not just the primary model's.
2. Set three budgets: a per-run budget of 100 cost units, a per-user budget of 500, and a global budget of 2,000. Give each a warning threshold at 80% that logs a warning, and a hard stop at 100% that halts the run.
3. Drive the loop with a mock model that returns a fixed token count per call, and a task that needs more calls than the per-run budget allows. Confirm the loop halts mid-run: no further model calls are made after the hard stop, the partial work completed so far is written to a run log, and the user-facing message says plainly that the run stopped because of cost, not because of an error in their request.
4. Add the pre-flight check: before each call, estimate the next call's cost from its input size and a cap on its output, and refuse the call if the estimate alone would breach the budget — then show a case where the post-call check alone would have let one oversized call through.

**Acceptance criteria:** the run halts at or before the per-run budget with no model call made after the hard stop; the warning fires at 80% before the stop; the run log records completed steps, the step that was refused, and the cost at halt; the user message names cost as the reason; the pre-flight check refuses an oversized call that the post-call check would have missed. `python code/cost_kill_switch.py` prints the halt trace and the final tally.

**Stretch:** derive the per-run budget from a measured cost per task instead of choosing it: run the same task ten times against the mock, take the median cost, and set the budget to three times the median — then write down why a multiple of the median is safer than a multiple of the mean when the cost distribution has a long tail.

## Exercise 4 — Offline queue: tasks queue during an outage and replay in order

**Difficulty:** Intermediate.

Build a durable offline queue in `code/offline_queue.py` that accepts work while every provider is down and replays it when connectivity returns.

1. Implement the queue on top of SQLite (standard library `sqlite3`), with one row per task: a unique identifier, an idempotency key, the payload, a timestamp, an attempt count and a status. The enqueue write must be committed before the agent tells the user the task is accepted.
2. Simulate an outage: with the mock provider down, enqueue five tasks in order, then confirm they are all on disk — kill and restart the process, and show the five tasks are still there.
3. Restore connectivity and replay. Tasks must replay in the order they were accepted, each task's downstream handler must receive its idempotency key, and a handler that has already seen a key must not execute the side effect a second time — simulate a crash halfway through replay and show the second replay does not duplicate any side effect.
4. Add the failure handling: a task that fails on replay is retried with backoff up to three attempts, and after the third failure it moves to a dead-letter table and the queue continues with the next task instead of blocking behind it. Add the bounds: a maximum queue size of 1,000 tasks and a maximum task age of 24 hours, after which a task expires instead of replaying.

**Acceptance criteria:** five tasks queued during the outage survive a process restart; replay is in acceptance order; a crash mid-replay causes zero duplicated side effects; a task that fails three times lands in the dead-letter table and does not block later tasks; a task older than 24 hours expires rather than replaying; the queue refuses new tasks with a clear message when it is full. `python code/offline_queue.py` prints the enqueue, restart, replay and dead-letter trace.

**Stretch:** decide which tasks are safe to queue and which are not. Write the rule down as a function — read-only tasks and idempotent writes may queue; non-idempotent writes may not — and add a task type (for example a payment) that the queue refuses at enqueue time, with a message explaining why, rather than accepting work it cannot safely replay.

## Exercise 5 — API-to-browser fallback against a local page

**Difficulty:** Advanced.

Build the narrow browser fallback described in the lesson, without depending on any live website: serve a small page from the code in this chapter, and fall back to it when the API fails.

1. Run the local page server in `code/api_to_browser_fallback.py` (standard library `http.server` only): a page listing three products with a name, a price and a rating each. Write a typed extractor — a dataclass with those three fields and a validator that rejects a row with a missing price or a price that is not a number.
2. Implement the API path as a mock that returns the same three products as structured data, and the browser path as a plain HTTP fetch of the local page followed by the same extractor, so that both paths return the identical typed structure and downstream code cannot tell which path produced the answer.
3. Make the API mock fail with a 503, and confirm the agent falls back to the page, extracts the three products, validates them, and returns the cheapest — with a log entry naming the fallback and its cause.
4. Now break the page: remove the price from one product and confirm the extractor rejects that row, the agent refuses that product rather than guessing its price, and the run is logged as degraded rather than successful.

**Acceptance criteria:** with the API healthy, the browser path is never used; with the API returning 503, the browser path serves the request and returns the same typed structure as the API path; a malformed row is rejected, not guessed at; the log distinguishes a fully successful run from a degraded one. No live website, no browser automation library and no API key is needed — the page is served locally.

**Stretch:** add the policy boundary as code, not as a comment: the browser path must refuse to run when the task requires authentication or when the page contains a bot-check marker, and it must refuse to do through the page what the API refused for a policy reason — implement both refusals and test them.

## Exercise 6 — The full drill: take the primary offline and watch the ladder work

**Difficulty:** Advanced.

Combine Exercises 1–4 into the degraded agent in `code/degraded_agent.py`, and run the drill that proves the ladder works before you need it.

1. Wire the pieces together: budgets checked before each step, calls through a circuit breaker per provider, the model cascade, the cached-answer rung, the offline queue for tasks that can wait, and a refusal as the final rung. Every transition must be logged with its cause, the rung that served the request, and its cost.
2. Run the drill in staging (all mocks, no real providers): take the primary provider offline, send ten requests of mixed types, and record for each one which rung served it, what it cost, and how long it took. Then restore the primary and send ten more requests, and confirm the breaker closes, the primary serves again, and the fallback rate returns to zero.
3. Write the drill report: fallback rate during the outage, time from the first failure to the breaker opening, cost per task at each rung, refusal rate, and the number of tasks that went to the queue and were replayed. Compare the cost per task at each rung against the baseline you measured with the primary healthy, and name the rung where quality loss would have been unacceptable for a task that touches money or a hard-to-reverse action.
4. Set the production alerts you would ship, using the thresholds from the lesson as a starting point: 95th percentile latency above 2 seconds, error rate above 5%, memory above 90%, plus one alert on fallback rate — and write down, for each alert, what the on-call engineer does when it fires.

**Acceptance criteria:** the drill runs end to end against mocks in under a minute; the report names, for every request, the serving rung, its cost and its cause; the breaker opens, the cascade steps down, the queue replays and the breaker closes again, all visible in the log; the report includes the cost-per-task comparison and the alert list with a named response for each alert. `python code/degraded_agent.py` prints the drill report.

**Stretch:** run the drill a second time with the primary provider *slow* rather than down — every call takes 25 seconds and then succeeds — and show whether your timeout and breaker settings treat slowness as a failure. If they do not, fix them, and write down the timeout you chose and why.

## Exercise 7 — Cost-aware routing: cheapest that clears the bar, per step

**Difficulty:** Intermediate.

Build the cascade router in `code/cascade_router.py` and prove it saves money without buying worse answers.

1. Work the expected-cost formula by hand first, using the illustrative prices in the module (frontier $15 in / $60 out, mid $3 in / $12 out, small $0.50 in / $2 out per 1M tokens — worked-example prices, not real vendor prices): for a call of 2,000 input and 500 output tokens, show that the frontier model costs $0.0600, the mid-size model $0.0120 and the small model $0.0020, and that a traffic mix of 60% small, 30% mid and 10% frontier gives an expected cost of $0.0108 per call — 82% cheaper than always-frontier. Check your hand calculation against `expected_cost()` in the code.
2. Route the golden set in `code/cascade_router.py` with the predictive router and with the fixed-rules router, and compare the two with the replay matrix: pass rate and average cost per task for each, on the same tasks. Then route a multi-step agent run — a planning step, an extraction step and a formatting step — and confirm each step is routed independently, with the planning step going to the frontier model and the formatting step to the small model.
3. Add the quality constraint as a gate, not a hope: raise the minimum quality bar to 0.95 and show which tasks the small model can no longer serve; lower it to 0.70 and show which tasks it now serves that it should not. Write down, for your own workload, what the bar should be and what evaluation evidence would justify it.
4. Run the new router in shadow mode for the golden set: confirm the shadow log records what the router *would* choose while the enforced model still serves every request, and write down the rule you would use to promote the router from shadow to canary (for example: agreement with the enforced model on 95% of tasks, and no golden-set failure, over a week of traffic).

**Acceptance criteria:** the hand-worked expected cost matches `expected_cost()` to four decimal places; the replay matrix shows both router versions passing the golden set, with the predictive router no dearer than the fixed one; the per-step run routes the planning step to the frontier model and the formatting step to the small model; moving the quality bar changes which model serves which task; shadow mode logs the router's choice without changing what is served. `python code/cascade_router.py` prints the expected-cost comparison, the replay matrix and the per-step routing, and `python code/test_cascade_router.py` passes.

**Stretch:** build the bad router — one that always routes to the small model — and show the golden-set gate failing it, with the failing tasks named. Then argue, in a short paragraph, whether you would rather ship a router that is sometimes too expensive or one that is sometimes wrong, and what in your product makes that the right trade.

## Exercise 8 — Semantic-cache hardening: tune the threshold, scope the key, verify the hit

**Difficulty:** Intermediate.

Harden the semantic cache that the lesson's 68.8% figure describes — that figure is from the GPT Semantic Cache paper (arXiv:2411.05276), not from the GPTCache library, and the threshold of 0.8 is the paper's optimum on its own traffic, not a universal constant.

1. Build a small labelled set of your own: twenty query pairs, half of them true near-duplicates (the cached answer would be correct for the new query) and half of them near-misses (similar wording, different meaning — "cancel my order" against "cancel my subscription" is the shape to copy). Sweep a similarity threshold from 0.6 to 0.9 in steps of 0.05 and, for each value, count false hits and misses; pick the threshold where a false hit costs you most, and write down why that threshold is right for *your* traffic rather than the paper's.
2. Write the scoped cache key as a function — tenant + version + task type + query — and show, with a concrete example, an entry that a query-only key would have served to the wrong tenant or after a policy change, and that the scoped key correctly refuses. Add a version bump (a policy change) and confirm the old entries stop matching.
3. Add a verifier: a cheap check, using the small simulated model, that the cached answer still fits the new query before it is served. Show a false hit from step 1 that the verifier catches, and a true hit that it lets through. Then write the cache-busting anti-patterns as a checklist your code enforces: no caching without versioning, no personalised answer under a shared key, no similarity-only serving for a high-stakes answer (anything touching money or a hard-to-reverse action), and entries written while degraded flagged with a short expiry.

**Acceptance criteria:** the threshold sweep is reported with false hits and misses at each value, and the chosen threshold is justified from those counts, not borrowed from the paper; the scoped key refuses the cross-tenant and post-policy-change examples; the verifier catches at least one false hit and passes at least one true hit; the anti-pattern checklist is enforced in code, not written as comments. No live embedding service or API key is needed — use a small hand-built similarity stand-in and label it as such.

**Stretch:** poison the cache deliberately — write in an answer produced while the fallback model was degraded — and show how long it keeps being served before anyone notices under your current design. Then add the defence (a degraded-mode flag with a short expiry, or a re-verification pass) and show the poisoned entry expiring instead.

## Exercise 9 — Explain-back: defend a routing decision to a reviewer who only sees the cost saving

**Difficulty:** Advanced.

A reviewer has seen your routing numbers — 82% cheaper than always-frontier on the worked example — and asks the only question that matters: *did you make the product worse to get there?* Answer them, out loud, in one connected explanation, not as a list.

1. State the routing decision as a claim with its condition attached: the saving, the traffic mix it assumes, and the quality bar every routed model had to clear, with the golden-set pass rate and the average cost per task from the replay matrix in Exercise 7 as the evidence.
2. Anticipate the three challenges the reviewer will raise, and answer each before they ask it: the task the router gets wrong most often, and how the replay matrix shows it; the step in a multi-step run where routing down would poison the steps after it, and why that step routes up; and what happens the week traffic changes, and how shadow scoring and the canary rollout would catch it before users do.
3. Close with the refusal case: name a task type, or a single task, that you would *not* route cheaply even though the numbers say you could, and explain why — in terms of what is at stake for the user, not in terms of the model's capability score.

**Acceptance criteria:** the explanation is delivered as one flowing story, step by step, with the cost number, the quality number and the condition that links them all present; it names a concrete router failure mode with its evidence, a concrete per-step poisoning risk, and a concrete task that stays on the frontier model for reasons of stakes rather than capability. Record it (audio or written) and grade yourself against the lesson's interview angle: would a principal engineer hearing this trust you to own the router in production?
