# Code — Chapter 5: Multi-Agent Swarms and Agent-to-Agent Handoffs

Python 3.11 or later. Only the Python standard library is used, so there are no dependencies to install. The deterministic demos need no model API key; the live demo and the live tests need a configured model backend — see the "Connect a model" section in the root README, which covers the key-based path (`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`) and the AWS Bedrock path (`LLM_BACKEND=bedrock`, `LLM_BEDROCK_MODEL_ID`).

## Files

| File | What it is |
| --- | --- |
| `swarm.py` | The core library: typed payloads and validators, the `TaskEnvelope` with checksum and schema version, the supervisor topology with enforced bounds, consensus voting and conflict resolution, and the workers |
| `llm_client.py` | The shared model client, copied verbatim from Chapter 7: environment configuration, retries, structured JSON replies, token and cost totals |
| `accounts.json` | The account database the researcher reads from disk — the source of truth for the re-fetch in consensus case 4 |
| `demo_supervisor.py` | The supervisor topology with deterministic workers: no model API key needed |
| `demo_live_swarm.py` | The same swarm with real model-backed workers (model sentiment scorer and model drafter); needs a configured model backend |
| `demo_consensus.py` | Consensus voting: majority, weighted, tie escalation, and a factual disagreement resolved by re-fetching the source of truth from `accounts.json` |
| `demo_corrupt_handoff.py` | A corrupted handoff is detected and rejected, with the exact check that failed |
| `test_swarm.py` | The deterministic tests, which need no model, plus the live tests, which need a configured model backend and skip otherwise |
| `traces/faulty_run.jsonl` | The faulty trace for Exercise 6 |
| `requirements.txt` | Empty by design — standard library only |

## Run it

```bash
python code/demo_supervisor.py
python code/demo_consensus.py
python code/demo_corrupt_handoff.py
python -m unittest discover -s code -v
```

For the live swarm, from the chapter directory, with a model connected as described above (key-based shown; for Bedrock, `LLM_BACKEND=bedrock` with `LLM_BEDROCK_MODEL_ID` instead):

```bash
LLM_API_KEY=<redacted> LLM_BASE_URL=<your provider's API base URL> LLM_MODEL=<your model name> python code/demo_live_swarm.py
```

Expected output from the live demo, on the configured model: the run completes, the sentiment scorer returns a real model verdict, the drafter writes a real reply that still states the verified renewal date of 2026-03-14, every claim carries a provenance of `researcher` or `policy-checker`, and the demo prints the model usage for the run (about 1,000–1,700 tokens on the model it was run against; the dollar cost depends on the configured model). Expected test result: the deterministic tests pass, and the live tests pass when a model backend is configured (they skip otherwise).

## A note on determinism — what stays deterministic, and why

The researcher and the policy checker are deterministic on purpose, and that is a deliberate stand-in, not an unfinished one: the lesson of this chapter is the topology and the handoff mechanics — typed envelopes, validation, consensus rules and bounds — and deterministic workers make those mechanics runnable, repeatable and testable with no model API key. The researcher reads the real `accounts.json` file on every call instead of an in-memory dictionary, and the policy checker applies a written rule table, because looking a fact up and applying a written rule are deterministic jobs, not model jobs. The two workers that did pretend to be a model — the sentiment scorer, which judges tone, and the drafter, which writes prose — are now real model calls in `ModelSentimentWorker` and `ModelDrafterWorker`, built on `LLMWorker` and the shared client; the live demo runs the swarm with them, so the swarm runs for real. A model failure is a loud error that the supervisor records as a rejection, so the run ends partial instead of crashing, and a model reply that invents a fact or a provenance is rejected by the typed validator before it can reach the customer.

A real model is non-deterministic, so the live demo reports what the model actually did on this run instead of asserting a fixed script. The token budgets for the model workers are generous on purpose: this model spends much of its budget on hidden reasoning tokens, and with a small budget it can spend it all on reasoning and return empty content — the same gotcha Chapter 7 documents.
