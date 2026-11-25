# Code — Chapter 7: Inter-Agent Security & Guardrails

Python 3.11 or later. Only the Python standard library is used, so there are no dependencies to install for the key-based backend. You do need a connected model to run the demo and the live tests: see "Connect a model" in the root README, then run the demo either key-based — `LLM_API_KEY=... LLM_BASE_URL=... LLM_MODEL=... python demo_agent.py` — or with AWS Bedrock — `LLM_BACKEND=bedrock LLM_BEDROCK_MODEL_ID=... python demo_agent.py`.

## Files

| File | What it is |
| --- | --- |
| `guardrails.py` | The guardrail system: provenance tagging (`ContentItem`), the sanitisation proxy, least-privilege tool scoping with pinned descriptions (`ToolRegistry`), approval gates (`ApprovalGate`) and the audit log (`AuditLog`) |
| `llm_client.py` | A small production-style client for a model — key-based OpenAI-compatible API or AWS Bedrock — with retries, structured JSON replies, token and cost totals |
| `injection_judge.py` | The language-model judge: a second sanitisation layer that reads untrusted content as data and returns a JSON verdict on whether it tries to instruct the agent |
| `demo_agent.py` | The poisoned-ticket scenario run against a vulnerable agent and a guarded agent, in the same process, against a real model |
| `debug_smtp_server.py` | A minimal debugging SMTP server: accepts mail on localhost:1025, prints it, delivers nothing |
| `tickets.json` | The tickets the demo reads: the poisoned ticket and the clean ticket |
| `customers.json` | The customer record the attack tries to exfiltrate |
| `test_guardrails.py` | The deterministic tests, which need no model, plus the live tests, which need a configured model backend and skip otherwise |
| `07_guardrails.ipynb` | The same material as an interactive notebook |
| `requirements.txt` | Empty by design — the key-based backend uses only the standard library; the AWS Bedrock backend needs `pip install boto3` |

## Run it

Start the debugging mail server first, in a second terminal, so the vulnerable agent's send has somewhere real to go:

```bash
python debug_smtp_server.py
```

Then, in the first terminal, with a model connected (see above):

```bash
# Key-based (any OpenAI-compatible API):
LLM_API_KEY=... LLM_BASE_URL=... LLM_MODEL=... python demo_agent.py

# ... or with AWS Bedrock:
LLM_BACKEND=bedrock LLM_BEDROCK_MODEL_ID=... python demo_agent.py

python -m unittest test_guardrails -v
```

Expected output from the demo, on the model it was run against (the configured model): the vulnerable agent reads the poisoned ticket and refuses the injected instruction, summarising the ticket instead — which is itself a finding, because refusal is model-dependent, and a different model, version, temperature or phrasing can flip it; the guarded agent blocks the injection by construction, records the block in its audit log, and still summarises the ticket; and the guarded agent still summarises a clean ticket normally. Those three outcomes, taken together, are the chapter in miniature: the attack is real, the defence does not depend on the model's goodwill, and the defence does not break the legitimate task. The demo also prints the model usage for the run. Expected test result: the deterministic tests pass, and the live tests pass when a model backend is configured (they skip otherwise) — including the canary test on the vulnerable agent, which passes while the model refuses and fails the moment a model complies.

## A note on determinism

A real model is non-deterministic, so the demo reports what the model actually did on this run instead of asserting a fixed script. The guardrails are deterministic — what the model is allowed to see, which tools it is allowed to call, and which calls need a human — and those are what the deterministic tests cover without a model. The live tests cover the model-facing behaviour: the judge's verdict on a poisoned and a clean ticket, and the two agents' outcomes against the real model.
