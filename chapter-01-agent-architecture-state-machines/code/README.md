# Code — Chapter 1: Agent Architecture & State Machines

Python 3.11 or later. The LangGraph demo needs `langgraph` and
`langgraph-checkpoint-sqlite` (`pip install -r requirements.txt`); the
other files run on the standard library only. The Temporal worker and
start commands additionally need `temporalio` and a Temporal server
(see `04_temporal_workflow.py`).

## Two engines: deterministic by default, real model with `--live`

Every demo in this chapter runs in two modes. The default mode is
deterministic: drafts are templates and the reviewer is a rule (a ticket
that needs rework fails its first draft and passes its second). That
determinism is deliberate, not a leftover shortcut: the thing being
studied in this chapter is control-flow topology, checkpointing, and
durable execution, and each of those is only testable if every run
faces the same reviewer with the same verdicts. A real model's verdicts
vary from run to run, so the deterministic engine is the controlled
experiment — and it is also what the exercises are written against, so
running the demos and the deterministic tests needs no model API key.

Pass `--live` to `01_dag_vs_cycle.py` (`run`, `crash`, `resume` in
`02_langgraph_checkpoint_resume.py`, `simulate` in
`04_temporal_workflow.py`) and every stand-in for a model is replaced
by a real model call: drafts are written by a real model, drafts are
judged by a real model acting as reviewer, and in the free-form loop the
next step is chosen by a real model at every step. The live run needs a
configured model backend — set `LLM_API_KEY`, with `LLM_BASE_URL` and
`LLM_MODEL` if your provider is not OpenCheap's router and Kimi K3.1
Pro. On the author's machine the demos also run through the registered
OpenCheap connector with `LLM_BACKEND=opencheap-connector`, which
authenticates through the Secure Vault without the key entering the
process. Each live run prints its token and cost totals at the end; the
full live run of `01` costs about a cent, and the whole chapter's live
tests cost a few cents. A live run takes minutes rather than seconds —
each model call takes several seconds — which is itself a property of
the shape being demonstrated, not an accident of the demo.

The one deliberate stand-in that remains is the local Temporal
simulator in `04_temporal_workflow.py`: it replays a JSON event log
instead of talking to a Temporal server, because the thing being
studied there is the replay model itself, and the simulator is the only
way to study it with no infrastructure. Its activities are real — real
model calls with `--live`, a real receipt ledger file in both modes —
and the real worker path against a server is in the same file, so the
simulator is a stand-in for the server, not for the work.

## Files

| File | What it is |
| --- | --- |
| `01_dag_vs_cycle.py` | The same triage job as a DAG, a cyclic graph, and a free-form loop. Deterministic by default; `--live` uses a real model for drafting, reviewing, and deciding |
| `02_langgraph_checkpoint_resume.py` | A cyclic LangGraph workflow with SQLite checkpointing: crash mid-run, resume the same thread in a fresh process, list the checkpoint history. `--live` uses a real model for drafting and reviewing |
| `03_state_serialisation.py` | Versioned, small, secret-free state: JSON round-trip, V1-to-V2 migration, the claim-check pattern against a real file-backed blob store |
| `04_temporal_workflow.py` | The same triage job as a Temporal workflow, plus a local event-log simulator. `simulate` is deterministic by default and `--live` records real model results; `worker` / `start` run against a real server and always use the real model |
| `llm_client.py` | A small production-style client for an OpenAI-compatible chat API, copied verbatim from Chapter 7 so this chapter is self-contained: environment configuration, retries, structured JSON replies, token and cost totals |
| `live_review.py` | The chapter's only model-facing module: drafting, reviewing, and next-step decisions as real model calls, shared by the three demos |
| `receipt_ledger.py` | The receipt ledger: a real JSON-lines file (`receipts.jsonl`) that makes sending a receipt idempotent by key |
| `test_chapter01.py` | Deterministic tests (no model, no network) plus live tests that skip without a configured model backend |
| `requirements.txt` | `langgraph` and `langgraph-checkpoint-sqlite` (pinned); `temporalio` optional |

## Run it

```bash
python 01_dag_vs_cycle.py            # deterministic, no key
python 01_dag_vs_cycle.py --live     # real model calls
python 03_state_serialisation.py

pip install -r requirements.txt
python 02_langgraph_checkpoint_resume.py crash --thread demo-1 --after 2
python 02_langgraph_checkpoint_resume.py resume --thread demo-1
python 02_langgraph_checkpoint_resume.py history --thread demo-1

rm -f temporal_event_log.json
python 04_temporal_workflow.py simulate
python 04_temporal_workflow.py simulate   # second run replays, executes nothing
python 04_temporal_workflow.py simulate --live   # real model results, recorded then replayed
```

Expected deterministic output from `01`: the DAG escalates T-102
because it cannot retry, while the cyclic graph and the free-form loop
both resolve it on the second draft. Expected output from `02`: the
crash exits with code 99, the resume exits with code 0 and resolves
all four tickets with exactly one receipt each, and `history` shows 25
checkpoints. Expected output from `04`: the first `simulate` executes
every activity, the second replays every activity and executes none,
and the ledger holds one receipt for T-102 either way. The live runs
produce the same outcomes with real drafts and real verdicts, and
print their model usage at the end.

## Tests

```bash
python -m unittest test_chapter01 -v
```

The deterministic tests need no model and no network (the LangGraph
tests need `langgraph` installed, and skip otherwise). The live tests
need a configured model backend and skip otherwise; they take a few
minutes and cost a few cents. Expected result: all deterministic tests
pass, and the live tests pass when a backend is configured.
