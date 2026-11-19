# Code — Chapter 7: Inter-Agent Security & Guardrails

Python 3.11 or later. Only the Python standard library is used, so there are no dependencies to install and no model API key is needed.

## Files

| File | What it is |
| --- | --- |
| `guardrails.py` | The guardrail system: provenance tagging (`ContentItem`), the sanitisation proxy, least-privilege tool scoping with pinned descriptions (`ToolRegistry`), approval gates (`ApprovalGate`) and the audit log (`AuditLog`) |
| `demo_agent.py` | The poisoned-ticket scenario run against a vulnerable agent and a guarded agent, in the same process |
| `test_guardrails.py` | 13 tests, each named for the attack it covers |
| `07_guardrails.ipynb` | The same material as an interactive notebook |
| `requirements.txt` | Empty by design — standard library only |

## Run it

```bash
python demo_agent.py
python -m unittest test_guardrails -v
```

Expected output from the demo: the vulnerable agent exfiltrates the customer record to the attacker's address, the guarded agent blocks it and records the block in its audit log, and the guarded agent still summarises a clean ticket normally. Expected test result: 13 tests, all passing.

## A note on the stand-in model

`demo_agent.py` uses a small deterministic rule in place of a language model, and labels it as such everywhere it appears. A real model is non-deterministic, and a security demo that only sometimes blocks the attack teaches nothing. The stand-in makes the model's behaviour reproducible so the lesson is about the harness, which is deterministic: what the model is allowed to see, which tools it is allowed to call, and which calls need a human. Swap the stand-in for a real model call and the harness behaves the same way.
