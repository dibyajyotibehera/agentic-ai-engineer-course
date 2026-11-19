# Chapter 11 — code

Standard library only. No API key and no paid service is needed for
any file: every model, provider and tool is a mock you can inspect.

| File | What it does |
| --- | --- |
| `circuit_breaker.py` | Circuit breaker with closed / open / half-open states, a fake clock, and a flaky mock provider (Exercise 1) |
| `model_cascade.py` | Model cascade: frontier → cheaper same-provider → different provider → cached answer → refusal, with a cost tally and an evaluation gate (Exercise 2) |
| `cost_kill_switch.py` | Metered agent loop with per-run, per-user and global budgets, warning at 80% and a hard stop at 100% (Exercise 3) |
| `offline_queue.py` | Durable offline queue on SQLite, with idempotency keys, ordered replay, dead-letter handling and expiry (Exercise 4) |
| `api_to_browser_fallback.py` | API-to-browser fallback against a page served locally by the standard library, with typed extraction and validation (Exercise 5) |
| `degraded_agent.py` | The full ladder wired together, with the drill that proves it works (Exercise 6) |
| `cascade_router.py` | Cost-aware cascade router over a simulated model pool: expected-cost computation, predictive vs fixed routing, per-step routing, and an eval harness with golden set, replay matrix and shadow scoring (Exercise 7) |
| `test_cascade_router.py` | Standard-library tests for `cascade_router.py` — run with `python test_cascade_router.py` |

Run each file on its own to see its demo, for example:

```bash
python circuit_breaker.py
python model_cascade.py
python cost_kill_switch.py
python offline_queue.py
python api_to_browser_fallback.py
python degraded_agent.py
```

Check that everything compiles:

```bash
python -m py_compile *.py
```
