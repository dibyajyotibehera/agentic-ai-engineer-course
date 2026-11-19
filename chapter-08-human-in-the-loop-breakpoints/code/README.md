# Code for Chapter 8 — Human-in-the-Loop & Breakpoints

Standard library only — nothing to install, no API key, no paid
service. Run everything from this directory.

## The flow

```bash
# 1. The agent plans, then pauses durably before its write tool.
python durable_pause.py run --task "Publish the weekly changelog"

# 2. (Optional) start the webhook approval endpoint in another shell.
python approval_server.py --port 8765

# 3. Record the human's decision — the command-line stand-in for
#    the Slack/Teams button click or the webhook decision endpoint.
python durable_pause.py approve --run-id <run-id> \
    --decision approved --by approver@example.com --note "Looks right"

# 4. Resume, possibly after a restart: the run reloads from SQLite.
python durable_pause.py resume --run-id <run-id>

# 5. Reconstruct the story from the audit log alone.
python audit.py --db hitl.db --run-id <run-id>
```

Or run the whole flow, including both chat-gate payloads, in one go:

```bash
python demo_end_to_end.py
python -m unittest test_chapter08 -v
```

## Files

| File | What it is |
| --- | --- |
| `store.py` | SQLite store: run checkpoints plus a hash-chained, append-only audit log |
| `durable_pause.py` | The agent: plans, pauses before the write tool, resumes after approval |
| `approval_server.py` | Signed webhook endpoint: receives approval requests, accepts decisions |
| `slack_gate.py` | Slack Block Kit approval card, request verification, interaction parsing (posting is a stub — see its docstring) |
| `teams_gate.py` | Teams Adaptive Card approval card and action parsing (posting is a stub — see its docstring) |
| `audit.py` | Hash-chain verification and reconstruction of a run from the log alone |
| `demo_end_to_end.py` | The whole flow end to end, with assertions at every step |
| `test_chapter08.py` | Tests for the properties the chapter promises |

## Measured results

Running `demo_end_to_end.py` and the test suite on the author's
machine completes the full pause–approve–resume–audit cycle with
the write executed exactly once and the audit chain verified intact
(see the demo's printed output). Those are results measured from
running this code, not benchmarks from any external source.
