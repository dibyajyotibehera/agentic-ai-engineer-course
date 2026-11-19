# Exercises — Chapter 8: Human-in-the-Loop & Breakpoints

All exercises run from `code/` with the standard library only — no
API key, no paid service, no Slack or Teams account. The Slack and
Teams posting functions are stubs by design (see their docstrings);
every exercise below is completable without them. Run the demo first
so you know what "working" looks like:

```bash
cd code
python demo_end_to_end.py
python -m unittest test_chapter08 -v
```

## Exercise 1 — Pause before a write, survive a restart, resume (Foundation)

Build on `durable_pause.py`: run an agent that plans a write, pauses
for approval, and is resumed by a *different* process after the first
one has exited.

1. Start a run: `python durable_pause.py run --task "Publish the
   weekly changelog" --approver approver@example.com`. Note the run
   identifier it prints.
2. Kill the shell's Python process if it is still running (it should
   already have exited — check that it has), then delete nothing and
   restart nothing yet: confirm with `python durable_pause.py status
   --run-id <run-id>` that the paused run is still there.
3. Record an approval:
   `python durable_pause.py approve --run-id <run-id> --decision
   approved --by approver@example.com --note "Looks right"`.
4. Resume it: `python durable_pause.py resume --run-id <run-id>`.
5. Resume it a second time and confirm the write did not run twice.

**Acceptance criteria.** The status command in step 2 reports
`awaiting_approval` with the full proposed write intact; the first
resume reports `completed` with the write applied; the second resume
reports `already completed` and the output file in `code/out/`
contains exactly one copy of the content with one run-identifier
marker. Then repeat the whole flow with `--decision rejected` on a
fresh run and confirm the output file for that run is never created.

**Stretch.** Kill the process *between* the checkpoint write and the
webhook dispatch — add a `sys.exit(1)` between those two lines in
`run_agent`, run it, remove the exit, and resume. The run must still
complete, because the checkpoint was written before the announcement
went out.

## Exercise 2 — A webhook approval endpoint with a timeout and a safe default (Intermediate)

Build on `approval_server.py`: run the endpoint, route an approval
through it, and prove each of its three protections.

1. Start the server: `HITL_WEBHOOK_SECRET=<a long random value>
   python approval_server.py --port 8765`.
2. Start a run with the webhook pointed at it:
   `APPROVAL_WEBHOOK_URL=http://127.0.0.1:8765/hooks/approval
   HITL_WEBHOOK_SECRET=<the same value> python durable_pause.py run
   --task "Publish the weekly changelog"`. Confirm the run reports
   `webhook_delivery: {"delivered": true}`.
3. Decide over HTTP: sign a decision payload exactly as
   `approval_server.compute_signature` does and POST it to
   `/approvals/<run-id>/decision` with an `idempotency_key`. Then
   POST the identical request again.
4. POST the same decision with no signature headers at all, and
   with a signature computed over a tampered body.
5. Start a second run with `--timeout-seconds 5`, wait ten seconds,
   then POST an `approved` decision for it and resume the run.

**Acceptance criteria.** The first decision returns 200 with
`duplicate: false`; the identical replay returns 200 with
`duplicate: true` and records no second audit event for the
decision; the unsigned request returns 401; the tampered request
returns 401; the late decision for the second run is recorded as
`expired`, the resume reports the run rejected, and no write is
performed — silence and lateness both fail closed. The audit trail
(`python audit.py --db hitl.db --run-id <run-id>`) shows the
`approval_webhook_delivered` and `approval_webhook_received` events
for the first run.

**Stretch.** Change the decision endpoint so a *different* second
decision (approve, then reject) returns 409 instead of overwriting
— then check whether `record_decision` already enforces this and
write a test proving it either way.

## Exercise 3 — Reconstruct who approved what from the log alone (Intermediate)

Build on `audit.py` and `store.py`: you are handed a database file
with no run rows and no live process — only the audit log — and you
must answer for an incident review.

1. Run the demo flow once to produce a populated database, then
   copy `hitl.db` to `incident.db` and delete every row from its
   `runs` table (`DELETE FROM runs;`), leaving `audit_events`
   untouched.
2. Run `python audit.py --db incident.db --verify-only` and confirm
   the chain is intact.
3. Using only `audit.reconstruct` (or the `audit` command with a run
   identifier taken from the events), write down: the task, the exact
   file the agent proposed to write, who approved it, when they
   approved it, and whether the write ran.
4. Now tamper with the copy: change one event's `actor` to a
   different name with an SQL `UPDATE`, and run the verification
   again.

**Acceptance criteria.** Step 2 reports the chain intact; step 3
produces all five answers with timestamps, and every answer matches
what the demo printed; step 4 reports the chain broken and names the
sequence number of the first altered entry. Write a one-paragraph
incident note stating what the log proves and what it does not prove
— it proves the log is unaltered, not that the events themselves are
true, and not that the approver was who they claimed to be.

**Stretch.** Add a `run_abandoned` case to the reconstruction: reject
a run, then confirm `reconstruct` reports the decision, the
abandonment, no write, and no anomalies.

## Exercise 4 — Route the approval through Slack and Teams (Intermediate)

Build on `slack_gate.py` and `teams_gate.py`: no chat account is
needed — the gates render JSON locally and the interaction parsing
runs offline.

1. Take a pending approval from Exercise 1 and render both cards:
   call `render_local` for the Slack Block Kit message and for the
   Teams Adaptive Card, and inspect the JSON.
2. Simulate the human clicking Approve in Slack: build a
   `block_actions` payload with `action_id` `approve_write` and the
   approval identifier as `value`, pass it to
   `handle_interaction_payload`, and feed the resulting decision
   into `record_decision`.
3. Do the same for Teams with an `Action.Execute` submission and
   `handle_card_action`.
4. Feed both gates a forged payload each: an unknown `action_id` in
   Slack, and a decision of `"maybe"` in Teams.

**Acceptance criteria.** Both cards contain the tool, the exact
file, the run identifier, the expiry, and both an Approve and a
Reject control; both simulated clicks produce a decision that
resumes the run to `completed`; both forged payloads raise
`ValueError` and record no decision. Write down, for each platform,
where the signature verification would run before the payload is
trusted — Slack's `verify_slack_signature` in `slack_gate.py` — and
why the card JSON alone must never be trusted as proof of who
clicked.

**Stretch.** Finish `post_approval` in `slack_gate.py` for real:
create a Slack app, enable interactivity pointed at your approval
endpoint, set `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET`, and send
one card with `chat.postMessage` using `slack_sdk`. Keep the
signature verification in front of the interaction handler.

## Exercise 5 — Static versus dynamic breakpoints (Advanced)

Extend `durable_pause.py` so the same agent has two kinds of pause,
and show when each is the right tool.

1. Add a *static* breakpoint: a `--break-before-write` mode that
   pauses before the write node on every run, regardless of the
   task, declared at graph-construction time (the analogue of
   LangGraph's `interrupt_before`).
2. Add a *dynamic* interrupt: pause only when the proposed write
   meets a condition evaluated at run time — for example, when the
   content exceeds 500 characters or the filename is outside an
   allow-list — and let small, allow-listed writes run without
   approval (the analogue of LangGraph's `interrupt()`).
3. Demonstrate both with three runs: a small allow-listed write
   (no pause), a large write (dynamic pause), and any write with the
   static breakpoint on (pause regardless).

**Acceptance criteria.** The small write completes with no approval
event in its audit trail; the large write pauses and needs an
approval before it completes; the static-breakpoint run pauses even
for the small write. For each run, state in one sentence why that
pause is static or dynamic and what would go wrong if you used the
other kind — the static gate cannot adapt to the content, and the
dynamic gate can be bypassed if its condition is wrong, so the
condition itself needs a test.

**Stretch.** Add a test that the dynamic condition is evaluated on
the *proposed* write before any side effect runs, and that resuming
the node re-evaluates it identically — if the condition reads the
clock or a random value, the resume can take a different branch
from the pause, which is the re-execution trap LangGraph's rules
warn about.

## Exercise 6 — The approval that should not exist (Advanced)

Design the failure this chapter warns about, then prove your gate
stops it.

1. Write a variant agent whose write tool decides for itself whether
   to seek approval, based on a prompt-style rule ("ask first if the
   write looks risky"), and drive it with three tasks: a plainly
   risky write, a write whose risk is hidden in the task text (for
   example, a filename that looks harmless but resolves outside the
   allow-list), and a write where the task text explicitly instructs
   the agent *not* to ask.
2. Run all three against the Chapter 8 gate (the tool pauses
   unconditionally) and against your variant.
3. Write the incident note for each run: what ran, what was
   approved, and what the audit trail shows.

**Acceptance criteria.** The Chapter 8 gate pauses on all three
runs; the variant pauses on at most the first, and you can show from
its audit trail exactly which runs executed a write with no approval
event. Your note states the design rule this demonstrates — the gate
must live underneath the model, in code the model cannot see or
edit, and a prompt is not a control — and names the risk tier you
would assign to each of the three writes in production, with a
reason for each tier.
