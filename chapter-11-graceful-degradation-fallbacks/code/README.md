# Chapter 11 — code

Standard library only. The deterministic demos run with no API key:
every failure is injected on a fixed, scripted schedule you can
inspect. The model tiers are real, though: each file ends with a
live check that calls the real model through the same code the demo
exercises, and reports real token counts and real dollars. A live
check runs only when a model backend is configured — `LLM_API_KEY`,
or `LLM_BACKEND=opencheap-connector` on the author's machine, where
the OpenCheap connector authenticates through the Secure Vault and
no key enters the process. Without a backend, each live check prints
a skip note and the deterministic demo still runs. The live checks
make a handful of short calls on the cheap (`:floor`) route, plus
two on the full model; the whole chapter's live verification cost
about $0.01.

| File | What it does |
| --- | --- |
| `llm_client.py` | The shared real-model client (copied verbatim from Chapter 7): environment configuration, retries on retryable statuses, structured JSON replies, token and cost totals, loud failures on a missing key or a bad response |
| `circuit_breaker.py` | Circuit breaker with closed / open / half-open states, a fake clock, and a scripted failing provider (Exercise 1); the live check passes one real model call through the breaker |
| `model_cascade.py` | Model cascade: frontier → cheaper same-provider → different provider → cached answer → refusal, with a cost tally and an evaluation gate (Exercise 2); `LiveRung` calls the real model and reports real dollars, and the live check steps down to it after a scripted frontier 503 |
| `cost_kill_switch.py` | Metered agent loop with per-run, per-user and global budgets, warning at 80% and a hard stop at 100% (Exercise 3); `LiveModel` drives the same meter with real provider-reported token counts |
| `offline_queue.py` | Durable offline queue on SQLite, with idempotency keys, ordered replay, dead-letter handling and expiry (Exercise 4) — real SQLite on disk, no model involved |
| `api_to_browser_fallback.py` | API-to-browser fallback against a page served locally by the standard library, with typed extraction and validation (Exercise 5) — real local HTTP on the fallback path |
| `degraded_agent.py` | The full ladder wired together, with the drill that proves it works (Exercise 6); with a client, the frontier and cheap rungs are real model calls |
| `cascade_router.py` | Cost-aware cascade router over a simulated model pool: expected-cost computation, predictive vs fixed routing, per-step routing, and an eval harness with golden set, replay matrix and shadow scoring (Exercise 7); `LiveTier` and `answer_routed` answer routed tasks with the real model |
| `test_cascade_router.py` | Standard-library tests for `cascade_router.py` — run with `python test_cascade_router.py`. The 11 deterministic tests need no model; the 3 live tests skip unless a backend is configured |

## What is still a stand-in, and why

A stand-in stays only where the subject itself requires one, and
each is labelled in the file where it appears:

- **Scripted failing providers and rungs** (`FlakyProvider`,
  `Rung.fail_with`): the failure being studied has to arrive on a
  known, repeatable schedule, which no real provider can supply on
  demand. The rung that serves the request is real wherever a live
  check exists.
- **Scripted quality scores in the router pool**: a stand-in for
  the measured pass rate you get by running your own evaluation
  set against each model. The routing decision is studied with
  them; the live tiers replace the serving side with real calls.
- **The rule-based complexity scorer**: a stand-in for a learned
  router, labelled as such in the lesson — it is the shape of the
  decision, replaced by a trained classifier once you have the
  labelled data.
- **The fixed-count model in the kill-switch demo** (`MockModel`):
  the kill-switch test needs a model whose spend is known in
  advance, so the loop can be driven to its hard stop without
  spending real money. The same loop driven by real token counts
  is `LiveModel`.
- **The product API in the browser fallback** (`ProductApi`): the
  dependency whose failure is injected. The fallback path it
  falls back to — a real HTTP fetch, typed extraction, validation
  — is real.
- **The different-provider rung in the live drill**: there is no
  second provider on this backend, so that rung stays scripted
  even in the live drill; a second provider would need its own
  key in production.
- **Abstract cost units** on the scripted rungs: the deterministic
  demos tally cost in abstract units, never mixed with the real
  dollars the live rungs report in `total_cost_usd`.

Run each file on its own to see its demo, for example:

```bash
python circuit_breaker.py
python model_cascade.py
python cost_kill_switch.py
python offline_queue.py
python api_to_browser_fallback.py
python degraded_agent.py
python cascade_router.py
```

Run the demos with the live checks enabled:

```bash
LLM_BACKEND=opencheap-connector python circuit_breaker.py
```

Run the tests, with and without a backend (the live tests skip
without one):

```bash
python test_cascade_router.py
LLM_BACKEND=opencheap-connector python test_cascade_router.py
```

Check that everything compiles:

```bash
python -m py_compile *.py
```
