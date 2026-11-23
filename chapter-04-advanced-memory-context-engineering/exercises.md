# Exercises — Chapter 4: Advanced Memory and Context Engineering

Work in order. Exercises 1–2 build the store; Exercises 3–4 break it in the ways production breaks it; Exercise 5 turns the whole thing into money; Exercise 6 keeps a long run honest, working compaction, pruning, rot and lazy loading. Exercise 7 applies progressive disclosure to the project's own documentation. The deterministic core of every exercise runs on the standard library only, using the deterministic stand-in embeddings in `code/memory_store.py` — no API key and no paid service is needed for those. The live paths in Exercise 6 — real model compaction with `compact_with_llm()` and real model tool-result summarisation with `prune_tool_result_with_llm()` — need a configured model backend (`LLM_API_KEY`, or `LLM_BACKEND=opencheap-connector` where the OpenCheap connector is registered); run them with `python code/compaction.py --live` and `LLM_BACKEND=opencheap-connector python3 code/test_compaction.py`, and note the token and cost totals the live runs print. Where an exercise has a stretch goal that needs a real embedding model or a running Postgres, the stretch goal says so explicitly; the pgvector schema in `code/pgvector_schema.sql` was not executed in this environment (no Postgres here), so treat it as a schema to run against your own database.

The running example throughout is a support copilot for a fictional software-as-a-service product: it answers refund, billing and outage questions for many tenants, and its memory must remember each tenant's plan and policies without ever mixing tenants up. The demo in `code/demo_support_copilot.py` wires the whole example together; read it before starting Exercise 1.

---

## Exercise 1 — Build the three-tier memory store

**Difficulty: Foundation**

Build a memory store with three tiers — working, episodic and semantic — backed by SQLite, following the skeleton in `code/memory_store.py`. The store must support: appending a turn to working memory for a session; promoting an episode (a summarised task outcome) to the episodic tier with an embedding; writing a fact to the semantic tier with a version, a source and an authority level; and retrieving, for a given query and tenant, the top results from the episodic and semantic tiers.

**Acceptance criteria**

- `python code/demo_support_copilot.py` runs end to end and prints a retrieval for a returning tenant that includes that tenant's plan fact and no other tenant's data.
- A memory written for tenant `acme` is never returned by a query scoped to tenant `globex`, verified by a test you write that asserts an empty result (not an error) for the cross-tenant query.
- Every stored memory carries `tenant_id`, `source`, `created_at` and, for semantic memories, `version` and `authority`.
- The store survives a process restart: close it, reopen the same SQLite file, and retrieve a memory written before the restart.

**Stretch goal (needs a running Postgres):** re-implement the episodic tier on Postgres with pgvector, using the schema in `code/pgvector_schema.sql`, and show the same retrieval returning the same top result. The `vector` column, the HNSW index and the tenant filter are all in the schema file; you only need to wire the queries.

---

## Exercise 2 — The write gate: decide what earns a write

**Difficulty: Intermediate**

Implement the write gate in `code/memory_store.py` (`WriteGate.evaluate`). The gate must apply, in order, the five questions from the lesson: is the candidate durable; is it new or changed relative to what is already stored; is it safe to store (no credential, secret or payment detail — use the blocklist in the skeleton and extend it); is it attributable (source, tenant and timestamp present); and is it worth its tokens (score = importance × expected reuse, decaying with age — the skeleton gives you the formula, you choose the threshold).

Feed the gate this candidate set, all from the same support ticket, and record its verdict on each:

1. "The customer prefers email over chat for follow-ups." (a durable preference)
2. "The customer's card ends in 4242 and expires 09/28." (payment detail)
3. "The agent checked the billing page at 14:02." (a transient action)
4. "Refunds within 30 days need no approval." (a policy fact — assume it is March, before the policy change in Exercise 3)
5. "The customer said the outage started at 09:15." (a one-off timestamp from a single ticket)
6. A duplicate of candidate 1, worded slightly differently.

**Acceptance criteria**

- Candidates 1 and 4 are admitted; candidates 2, 3 and 5 are refused, each with a named reason (`unsafe`, `transient`, `transient` respectively — candidate 5 is a timestamped observation about one incident, not a durable fact).
- Candidate 6 is refused as a duplicate, and the store still holds exactly one copy of the preference.
- Each verdict is logged with the candidate text, the decision and the reason, so the gate's behaviour is auditable after the fact.
- You write down, in a short paragraph in your exercise notes, what threshold you chose for "worth its tokens" and what would change your mind — a gate with no stated threshold is not a gate, it is a mood.

---

## Exercise 3 — Eviction and conflict: the March policy versus the June policy

**Difficulty: Advanced**

This is the exercise the whole chapter is built around. Set up the semantic tier with the March policy as a versioned fact: "Refunds within 30 days need no approval", source `policy-handbook-v3`, authority `high`, dated March. Then introduce the June change: "Refunds within 14 days need manager approval", source `policy-handbook-v4`, authority `high`, dated June. Then introduce a third claim: a support agent's chat message saying "refunds are fine up to 45 days, no approval needed", authority `low`, dated July.

**Acceptance criteria**

- After the June write, a query about the refund policy returns the June rule as current, and the March rule is retained with status `superseded` and a pointer to its replacement — not deleted.
- After the July chat message, the current policy is still the June rule: a low-authority claim never silently overwrites a high-authority fact, and the pair is flagged `contested` with both versions visible.
- You can answer, from the store alone and without re-reading the ticket history, the audit question: "What did the agent believe the refund policy was on 1 May?" (Answer: the March rule.)
- Eviction: fill the episodic tier to its cap (set the cap to 20 episodes in the demo), then add 10 more episodes and show that (a) no episode with importance 0.9 or above was evicted under the importance-decay policy, and (b) at least one low-importance, never-retrieved episode was evicted. Re-run the same fill with the least-recently-used policy and write down, in two sentences, which policy lost the more valuable memory and why.

---

## Exercise 4 — Token budgeting: spend the budget, then defend it

**Difficulty: Intermediate**

Using `code/token_budget.py`, build a token budget for the support copilot with a 4,000-token memory budget per task. Start from the worked allocation in the lesson — 1,600 tokens for working memory, 1,400 for episodic retrieval, 600 for semantic facts and 400 held in reserve — then re-derive it from measurements: run the demo's retrieval over 20 synthetic tasks, record how many tokens each tier actually consumed and how often each tier's memories changed the final answer (the demo logs a `used_in_answer` flag per retrieved memory), and re-allocate the budget toward the tier with the highest tokens-per-decisive-memory ratio.

**Acceptance criteria**

- Your re-derived allocation sums to 4,000 tokens, reserves at least 10% as headroom, and is justified by the measurements you recorded — quote the numbers.
- You demonstrate compression under pressure: take a task whose retrieved material totals more than the budget, and show the three-stage degradation working in order — drop the lowest-ranked items, then summarise the survivors, then truncate the oldest working-memory turns with a recursive summary — with the token count after each stage printed.
- You implement the MemGPT pressure rule in miniature: at 70% of the budget, emit a warning record; at 100%, flush the oldest working-memory turns and keep the summary. Show both triggers firing in a test.

---


## Exercise 5 — Cost per task: the arithmetic of each tier

**Difficulty: Advanced**

Using the `CostModel` in `code/token_budget.py` and only the verified figures from the lesson, compute the cost per task for the support copilot at a volume of 10,000 tasks per day, and answer the four questions below in writing. Do not invent a model price: pass your model's actual per-1M-token input price in as a parameter and report the answer as a formula in that price, plus the parts of the cost that do not depend on it.

1. **Embedding cost.** Each task writes one 1,000-token episode summary. At text-embedding-3-small's verified $0.02 per 1M tokens, what is the embedding cost per day, and per task? (Work it out; the lesson's $0.20-per-day figure is the answer to check against, not the answer to copy.)
2. **Tenant layout.** Each tenant holds 1 GB and there are 100 tenants. Using Pinecone's verified 1 read unit per 1 GB of namespace searched, what is the daily read-unit total for 10,000 tenant-scoped queries under a namespace-per-tenant layout, and what would it be under a single shared 100 GB namespace with metadata filtering? Express the difference as a multiple, and state in one sentence what that multiple means for the layout decision.
3. **Semantic cache.** The copilot's traffic is repetitive. Using the GPT Semantic Cache paper's verified 61.6–68.8% hit-rate range and up-to-68.8% reduction in API calls (arXiv:2411.05276 — the paper, not the GPTCache library), estimate the API calls remaining from 10,000 daily tasks at the low end and the high end of the range, and state the assumption that would make the real figure lower than both.
4. **The tier that costs most.** Combine your Exercise 4 measurements with the parametric model price to state, per task, what the working, episodic and semantic tiers each cost in model tokens, and name the single change — to the gate, the budget or the cache — that would cut the total by the largest amount, with the arithmetic that supports it.

**Acceptance criteria**

- All four answers are shown with the arithmetic, not just the result, and every price or rate used traces to a source in `references.md` or is labelled as your model's actual price passed in as a parameter.
- The semantic-cache answer correctly attributes the 68.8% figure to the arXiv paper and does not attribute it to the GPTCache library.
- Your answer to question 4 names a change that is implementable in the code you wrote (a gate threshold, a budget re-allocation or a cache threshold), not a change to the model or the vendor.

---

## Exercise 6 — Compaction, pruning, rot, and lazy loading: keeping a long run honest

**Difficulty: Advanced**

This exercise works the long-run defences from the last section of the lesson, using `code/compaction.py` and its tests in `code/test_compaction.py`. Build a long support case in the demo's world: a ticket that runs for 30 turns, in which the agent consults the billing record three times (a large tool output each time), retrieves the March refund policy early on, and then — in turn 20 — learns the June policy change. The transcript you build is the material; the four parts below are the work.

**Part A — Compact it.** Run the case until the token count crosses the 70% compaction point of a 4,000-token budget, then compact with `compact()`, naming as load-bearing facts: the June refund rule, the customer's plan and renewal date, and one approach already tried that failed. Check the compacted summary against the acceptance criteria below. Then run the same case through the live path, `compact_with_llm()` (needs a configured model backend), and compare the two summaries: the live one summarises the non-load-bearing turns with a real model call while the load-bearing turns and facts stay verbatim in both.

**Part B — Prune the tool results.** Take one of the three billing-record outputs (build a record of at least 2,000 tokens, with the fields `plan`, `renewal date`, `seats`, and a long invoice history) and prune it four ways — summarise, fields-only (`plan` and `renewal date`), pointer, and expiry after its single use — recording the token count before and after each strategy. State, in one sentence per strategy, what the next step loses under it. For the summarise strategy, run it twice: once offline, where the output is a head excerpt labelled as such, and once live with `prune_tool_result_with_llm()` (needs a configured model backend), and check that the live summary keeps the plan, renewal date and invoice figures exact.

**Part C — Find the rot, then load lazily.** Run `detect_rot()` on the 30-turn transcript twice: once as built (with the March rule still sitting in the context as an instruction), and once after you supersede it. Then re-run the case with lazy loading: load eagerly only the current goal and the decisions already taken, and defer the past episodes and the full billing record behind pointers, using `lazy_load()`. Record how many tokens the lazy run loads before the first tool call, versus the eager run.

**Explain-back (answer in writing, as if to a reviewer):** your compacted agent re-decided something it had already decided — in this case it told the customer refunds need no approval, a month after the June policy had settled the question. What went wrong, and what would you change? A strong answer names the mechanism (a decision dropped in compaction, so the transcript no longer contradicted the re-decision), the check that would have caught it (the load-bearing-facts audit in `compact()`, with the June rule named as a fact that must survive verbatim), and the change you would make (name decisions explicitly before compacting, and verify the summary against that list before restarting the loop) — and it says plainly which of the two rot mechanisms was at work (behavioural contamination, not attention dilution, because the agent followed a stale instruction rather than merely forgetting the right one).

**Acceptance criteria**

- The compaction trigger fires at or above 70% of the budget and not below it, shown by a test.
- The compacted summary is complete: every load-bearing fact you named appears verbatim in it, and the summary is smaller in tokens than the transcript it replaced.
- Each pruning strategy reduces the token count, and the pointer strategy keeps the full result available out of context (in `stored_full_text`) while the prompt carries only the reference.
- The rot detector fires on the transcript with the March rule still in it, and stops firing for contamination once the rule is superseded (dilution may still fire on volume alone — say which signal fired in each run).
- The lazy run loads fewer tokens before the first tool call than the eager run, and you can name what was deferred and what would trigger retrieving it.

**Stretch goal (design discussion, no code):** retrieval is returning the right memories and the model still ignores them or quotes the wrong one. That is the symptom Retrieval-Augmented Fine-Tuning (RAFT) is for (arXiv:2403.10131) — a training run, not a prompt change. Write one paragraph: what evidence from your own retrieval logs would you need before spending a training run on it, and what would you fix first if that evidence showed the retriever, not the model, was at fault?

---

## Exercise 7 — Progressive disclosure: write the map, then measure what it saves

**Difficulty: Intermediate**

Write an [AGENTS.md](../templates/AGENTS.md) index for the support copilot project, following the template in this course, and layer the project's documentation behind it: the index at the root, one short document per topic beneath it (architecture, memory tiers and budgets, tenant isolation, deployment), each no longer than a screen. Then measure the difference it makes. Build two prompts for the same task, fixing a tenant-isolation bug in the episodic tier: the first loads the whole documentation set into context, and the second loads only the index plus the one document the task touches, and count the tokens in each prompt with the token counter in `code/token_budget.py`.

**Acceptance criteria**

- The index fits on one screen and names, for every document behind it, what lives there and when to open it, with the build, test and lint commands the agent must use.
- The layered prompt for the bug-fix task carries fewer than half the documentation tokens of the flat prompt, and the task still has everything it needs: the index, the tenant-isolation document, and a pointer to the episodic-tier document, with nothing else loaded.
- You write down, in a short paragraph in your exercise notes, which document the task would have loaded next if the first fix attempt had failed, and what in the index would have told the agent to open it — an index that cannot answer that question is a table of contents, not a map.
- No document behind the index is loaded into the prompt until a step names it: show the loading order in your notes, index first, detail on demand.

**Stretch goal (design discussion, no code):** the same layering applies to tool schemas. Sketch, in a paragraph, how the support copilot would expose its tools if the agent first saw a one-line description of each tool and loaded the full schema only for the tool it was about to call, and name one task where that would save more than the documentation layering above, and one task where it would cost more.

---
