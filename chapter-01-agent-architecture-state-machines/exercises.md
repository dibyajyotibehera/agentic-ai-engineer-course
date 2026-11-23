# Chapter 01 — Exercises

Work in `code/` for every exercise. No exercise needs a model API key:
the demos run in a deterministic mode by default — drafts are templates
and the reviewer is a rule — so what you are testing is the
architecture, not a vendor. Every demo also has a `--live` mode that
replaces those stand-ins with real model calls (see `code/README.md`);
the live runs produce the same outcomes, and trying one is a good way
to see that the architecture does not depend on the reviewer being
deterministic. Where an exercise has a Temporal variant that
needs a server, the local simulator in `code/04_temporal_workflow.py` is an
accepted substitute, and the exercise says so.

## Exercise 1 — See the three shapes side by side

**Difficulty:** Foundation

Run `code/01_dag_vs_cycle.py` and read the output, then change the
deterministic reviewer (`review()` in the file) so that ticket T-102 needs
*two* redrafts instead of one (it fails attempts 1 and 2 and passes on
attempt 3), keeping `MAX_ATTEMPTS = 2`.

**Acceptance criteria**

- You can state, in one sentence each, why the DAG escalates T-102, why the
  cyclic graph resolves it in the original code, and what changes when the
  ticket needs two redrafts.
- After your change, the cyclic run escalates T-102 (attempts exhausted) and
  the free-form run does the same — and you can explain why raising
  `MAX_ATTEMPTS` to 3 is a product decision about cost and latency, not just
  a constant change.

## Exercise 2 — Kill a LangGraph run and resume it (the core exercise)

**Difficulty:** Intermediate

Using `code/02_langgraph_checkpoint_resume.py`, with a fresh thread each
time (delete `checkpoints.db` first or pick a new `--thread` name):

```bash
pip install -r code/requirements.txt
python code/02_langgraph_checkpoint_resume.py crash --thread ex2 --after 2
python code/02_langgraph_checkpoint_resume.py resume --thread ex2
python code/02_langgraph_checkpoint_resume.py history --thread ex2
```

**Acceptance criteria**

- The `crash` command exits with code 99 and leaves a `checkpoints.db`
  behind; the `resume` command exits with code 0.
- After resume, all four tickets are resolved (`resolved=['T-101',
  'T-102', 'T-103', 'T-104']`), every ticket has exactly one receipt, and
  no ticket appears twice in the resolved list — i.e. no completed work was
  reprocessed.
- `history` shows at least 20 checkpoints for the thread, and you can point
  to the checkpoint at which the crash happened (the newest checkpoint
  written before the kill) and the one the resume continued from.
- Repeat with `--after 5` and `--after 12`. The resumed outcome is identical
  in all three cases; only the number of steps the resume has left differs.

## Exercise 3 — Break the receipt, then fix it

**Difficulty:** Intermediate

This is the failure mode that matters most in production, so do it
deliberately. Edit `send_receipt` in `code/02_langgraph_checkpoint_resume.py`
so it is *not* idempotent: bypass the ledger's duplicate check — for
example by appending a receipt line to `receipts.jsonl` directly instead
of calling `record_receipt()` — so every execution appends a receipt
unconditionally. Then simulate a kill *inside* the node:
the simplest honest way is to make `send_receipt` raise an exception the
first time it runs for T-102 (for example by checking a marker file), run
`crash`/`resume`, and observe the result.

**Acceptance criteria**

- You can demonstrate a run in which T-102 ends up with two receipts after
  a resume — the double side effect, reproduced on demand. Count the
  lines for T-102 in `code/receipts.jsonl`, or call `receipt_count()` in
  `code/receipt_ledger.py`; the ledger is a real file, so the double
  receipt is a real record, not a counter in memory.
- You restore idempotency (by key, upsert, or read-before-write) and the
  same kill-and-resume sequence then produces exactly one receipt per
  ticket.
- In your own words, in the exercise notes or a comment: why checkpointing
  at super-step boundaries can never fix this on its own, and what has to
  be true of a node for a resume to be safe.

## Exercise 4 — Time travel: fork a run from an old checkpoint

**Difficulty:** Intermediate

Using the thread from Exercise 2, pick a checkpoint from the middle of the
history (for example the checkpoint where ticket index is 1), and resume
from it with a *changed* state: mark T-102 as already resolved before
resuming, using `graph.update_state()` on that checkpoint's config, then
invoke with `None`.

**Acceptance criteria**

- The forked run completes without reprocessing T-101.
- The original thread's history is unchanged — the fork is a new branch,
  and you can show both the original latest checkpoint and the fork's
  checkpoints in `history` output (use a different `--thread` for the fork
  if the shared history confuses the listing; the point is the mechanism,
  and you should note which you did).
- You can explain when you would use this in production (debugging a bad
  run, replaying with a fixed prompt) and when you would not (it rewrites
  what "happened", so it must never be used to silently alter an audit
  trail).

## Exercise 5 — Version your state, then break the version

**Difficulty:** Intermediate

Run `code/03_state_serialisation.py` and read the demo, then extend it:

1. Add a `StateV3` that adds a `priority: str` field to each ticket, with a
   `migrate_v2_to_v3` function, and bump `CURRENT_VERSION` to 3.
2. Write a version-2 snapshot to a file, load it with the version-3 code,
   and confirm the migration runs and the priority defaults sensibly.
3. Write a snapshot with version 4 and confirm loading it fails loudly.

**Acceptance criteria**

- A version-2 snapshot loads under the version-3 code with no data lost and
  every ticket carrying a priority.
- A version-4 snapshot is rejected with an error that names the version,
  rather than being silently misread.
- A snapshot containing a field named `api_key` anywhere in its payload is
  refused at write time (this already works — demonstrate it, do not just
  assert it).

## Exercise 6 — The same job in Temporal (or its simulator)

**Difficulty:** Advanced

Run the simulator first, twice, and watch the second run replay recorded
results instead of re-executing activities:

```bash
rm -f code/temporal_event_log.json
python code/04_temporal_workflow.py simulate
python code/04_temporal_workflow.py simulate
```

Try the live variant too: `python code/04_temporal_workflow.py simulate
--live` records real model drafts and verdicts in the event log on the
first run, and the second run replays them with no new model calls —
which is the same property the real server gives you, visible in the
usage totals (the second run adds zero tokens).

Then, if you have Docker or a local Temporal server available, run the real
thing: `temporal server start-dev` in one terminal, `python
code/04_temporal_workflow.py worker` in a second, and `python
code/04_temporal_workflow.py start --ticket T-102` in a third. While the
workflow runs, kill the worker process and restart it, and watch the run
complete without re-running the completed activities. (If no server is
available, the simulator run plus a written account of what you would
expect the worker kill to do is an accepted substitute — say so in your
notes.)

**Acceptance criteria**

- The simulator's second run prints `replay:` lines for every activity and
  `executed:` for none.
- With the real server (or in your written account): killing the worker
  mid-run does not re-execute any completed activity, and the workflow
  completes with the same result as an uninterrupted run.
- You can state the one rule that makes this possible — workflow code must
  be deterministic — and give one concrete example of code that would break
  it (for instance calling `datetime.now()` or `random.random()` in the
  workflow instead of `workflow.now()` / `workflow.random()`).

## Exercise 7 — Crash-recovery failure-mode drill

**Difficulty:** Advanced

This is a design exercise, not a coding one, and it is the one to do in a design review. Take the triage system from Exercises 2 and 6 and
write a one-page failure-mode analysis covering these five scenarios, each
with the failure, the defence, and what the operator sees:

1. The process is killed *between* a side effect (receipt sent) and the
   checkpoint that records it.
2. A deploy ships new workflow code while a run is suspended at a human
   approval that will arrive in two days.
3. A runaway cycle: the reviewer keeps failing a draft and the attempt guard
   is accidentally removed in a refactor.
4. The checkpoint (or event history) grows without bound because the full
   conversation transcript is stored in state on every step.
5. A resume loads a checkpoint written by a tenant's run into a different
   tenant's run.

**Acceptance criteria**

- Every scenario names a concrete defence from this chapter (idempotency
  key, state versioning plus stable node identities, a code-owned guard and
  hard cap, claim checks and compaction, separating execution state from
  memory with tenant-scoped keys) — not a vague "add monitoring".
- For scenario 2 you state which mechanism you would choose — LangGraph
  checkpoint rehydration with stable identities, or Temporal Worker
  Versioning — and why, for this system.
- For scenario 4 you give the size arithmetic: how many steps at what state
  size reach Temporal's 50 MB history cap, using the documented limits
  (51,200 events / 50 MB, warnings from 10,240 events / 10 MB), and what
  Continue-As-New changes about that arithmetic.

## Exercise 8 — Apply the decision framework to your own system

**Difficulty:** Advanced

Pick a real agentic workflow from your own work — the reader's Mastra
workflow on Amazon Web Services Elastic Container Service with Amazon
Bedrock is the obvious candidate — and write a one-page architecture
decision record that answers, in order: what is the unit of progress you
refuse to lose; who owns the next-step decision, and why that shape was
chosen over the other two; what is in state, what is a claim check, and
what the schema version is; which recovery mechanism you use and what its
kill window is; and the three failure modes you are most worried about,
with a defence for each.

**Acceptance criteria**

- The record names the shape (DAG, cyclic, or free-form loop) and gives the
  evidence that would make you change it.
- It states the kill window precisely: what, exactly, is lost if the
  process dies at the worst moment, and what the operator does next.
- It maps each part of your system to this chapter's vocabulary (node,
  checkpoint, super-step, activity, event history, idempotency key) without
  forcing a mapping that does not fit — and says where the mapping breaks.

## Exercise 9 — Drive a run from a feature list

**Difficulty:** Intermediate

This exercise makes the feature list concrete: you will write one by hand
and drive a small run from it, with the harness — your code, not the
"model" — owning every state transition. Start from the course's
`templates/feature_list.json` (or copy its shape) and write a list with
three features for the triage system: one that already passes, one that
needs a code change, and one whose verification command fails for a reason
outside the agent's control. Each feature needs the triple: a behaviour
description, a verification command, and a state, starting from
`not_started`. Then write a short driver script that loops over the list,
runs each feature's verification command, and moves the feature to
`passing` only when the command exits with code 0.

**Acceptance criteria**

- The driver never lets the agent side write state: the only code that
  changes a feature's state is the harness, and you can point to the line
  that enforces it.
- A feature whose verification command fails stays `active` (or moves to
  `blocked` if the failure is environmental, such as a missing tool), and
  never reaches `passing` on a claim alone — demonstrate this by making the
  mock agent assert the feature is done while the command still fails.
- The driver prints the back-pressure count — the number of features not
  yet `passing` — after each step, and the run ends when the count reaches
  zero, which happens only after every verification command has passed.
- You can state, in one sentence, which of the four harness consumers
  (scheduler, verifier, handoff reporter, progress tracker) would break
  first if each kept its own copy of the list instead of reading the
  shared file, and why.
