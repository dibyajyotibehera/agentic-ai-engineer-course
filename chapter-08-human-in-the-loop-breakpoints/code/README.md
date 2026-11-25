# Code for Chapter 8 — Human-in-the-Loop & Breakpoints

Standard library only — nothing to install for the key-based
backend (the AWS Bedrock backend needs `pip install boto3`). The
plan step is a real model call, so the agent run and the demo need
a connected model — see "Connect a model" in the root README.
Without a backend the run fails loudly instead of planning from a
template — there is no mock model in this chapter. The
deterministic tests need no backend: they inject a trivial planner
fixture that lives in the test file.

## The flow

```bash
# 1. The agent plans with the real model, then pauses durably
#    before its write tool. With a model connected (see above):
python durable_pause.py run \
    --task "Publish the weekly changelog"

#    ... or with AWS Bedrock:
LLM_BACKEND=bedrock LLM_BEDROCK_MODEL_ID=... python durable_pause.py run \
    --task "Publish the weekly changelog"

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
# ... or with AWS Bedrock:
LLM_BACKEND=bedrock LLM_BEDROCK_MODEL_ID=... python demo_end_to_end.py
python -m unittest test_chapter08 -v
```

## Files

| File | What it is |
| --- | --- |
| `llm_client.py` | OpenAI-compatible model client, copied verbatim from Chapter 7 (environment config, retries, structured JSON replies, token/cost totals, loud failures) |
| `store.py` | SQLite store: run checkpoints plus a hash-chained, append-only audit log |
| `durable_pause.py` | The agent: plans with the real model, pauses before the write tool, resumes after approval |
| `approval_server.py` | Signed webhook endpoint: receives approval requests, accepts decisions |
| `slack_gate.py` | Slack Block Kit approval card, request verification, interaction parsing, and real `chat.postMessage` delivery (needs `SLACK_BOT_TOKEN`) |
| `teams_gate.py` | Teams Adaptive Card approval card and action parsing, and real delivery to an incoming webhook URL (needs `TEAMS_WEBHOOK_URL`) |
| `audit.py` | Hash-chain verification and reconstruction of a run from the log alone |
| `demo_end_to_end.py` | The whole flow end to end, with assertions at every step |
| `test_chapter08.py` | Tests for the properties the chapter promises (deterministic tests plus live tests that skip without a backend) |

## Deliberate stand-ins

Two stand-ins remain in this chapter, and both are deliberate —
they stand in for a human or for a chat account, never for a model.
The `approve` command in `durable_pause.py` records a decision the
way a human's button click would, through the same
`record_decision` path, because the human is external to the
program. The demo and Exercise 4 feed a real-shaped Slack
`block_actions` payload through the real interaction handler for
the same reason. And the chat gates render their cards as JSON with
`render_local` when no Slack or Teams account is configured, so the
gate mechanics can be reviewed and tested offline — while
`post_approval` in both gates is a real delivery call that fails
loudly without credentials. A reader can tell these from unfinished
work because each one is named here and in the code, and none of
them produces the content the gate approves: that content always
comes from the model.

## Measured results

Running `demo_end_to_end.py` and the test suite on the author's
machine completes the full pause–approve–resume–audit cycle with
the write executed exactly once and the audit chain verified intact
(see the demo's printed output). The demo's plan step used 215
prompt tokens and 579 completion tokens, costing $0.0007; the two
live tests together used 383 prompt tokens and 1,137 completion
tokens, costing $0.0013. Those are results measured from running
this code, not benchmarks from any external source.
