# Exercises — Chapter 5: Multi-Agent Swarms and Agent-to-Agent Handoffs

Work in `code/`. The deterministic demos and tests run with the Python standard library only; no model API key and no paid service is needed for them, because the researcher and the policy checker are deterministic on purpose — the topology and the handoff mechanics are the lesson — while the sentiment scorer and the drafter have real model-backed versions (`ModelSentimentWorker` and `ModelDrafterWorker` in `code/swarm.py`, run by `code/demo_live_swarm.py`), which need a configured model backend (`LLM_API_KEY`, or `LLM_BACKEND=opencheap-connector` where the OpenCheap connector is registered). Run the test suite with `python -m unittest discover -s code -v` from this chapter directory, and run each demo with `python code/<demo>.py`.

## Exercise 1 — Supervisor topology with three worker agents and typed state passing

**Difficulty:** Foundation.

**The task.** Build a support-ticket triage swarm: a supervisor and three worker agents — a researcher, a policy checker, and a drafter — coordinated exactly as in `code/demo_supervisor.py`. The supervisor receives a ticket, fans out to the researcher and the policy checker in parallel, then hands their typed results to the drafter, which produces the reply. Every handoff must travel as a `TaskEnvelope` carrying a typed payload (`TicketState`, `ResearchResult`, `PolicyResult`, `DraftResult`), a schema version, and a checksum; the receiver must validate the envelope before acting on it.

**Steps.**

1. Read `code/swarm.py` and trace one full run of `code/demo_supervisor.py`, noting each handoff in the structured trace.
2. Add a fourth worker — a sentiment scorer that returns a `SentimentResult` with a `score` in the range 0.0 to 1.0 and a `label` of `frustrated`, `neutral` or `pleased` — and wire it into the supervisor's fan-out so its result reaches the drafter.
3. Give the new worker's payload a schema version of 2 while the other workers stay on version 1, and make the supervisor accept both versions explicitly. A version the supervisor does not list must be rejected, not coerced.

**Acceptance criteria.**

- The full run completes with all three original workers plus your new worker, and the trace shows one handoff per worker plus the final synthesis, each with a valid checksum.
- The drafter's reply cites the researcher's account facts and the policy checker's verdict, and every factual claim in the reply carries a provenance note naming the worker it came from.
- Sending a `SentimentResult` with schema version 99 is rejected with a `SchemaVersionError`, and the run ends as partial with the rejection logged rather than crashing.
- `python -m unittest discover -s code -v` still passes after your change.

## Exercise 2 — Corrupted state payload: detect and reject

**Difficulty:** Intermediate.

**The task.** A handoff is only as trustworthy as its validation, so this exercise attacks it. Using `code/demo_corrupt_handoff.py` as the starting point, build a corruption harness that takes a valid `TaskEnvelope` and mutates it in each of the following ways, one at a time, and confirms the supervisor rejects each one: a flipped character in a text field, a priority value outside the allowed set, a missing required field, a schema version the receiver does not accept, a checksum that does not match the payload, and a payload addressed to a different recipient than the worker receiving it.

**Steps.**

1. Run `code/demo_corrupt_handoff.py` and read how `validate_envelope` distinguishes a checksum mismatch from a schema violation, and what the supervisor does in each case.
2. Extend the harness with the two mutations it does not yet cover — a wrong recipient and a stale replay, where a valid envelope from an earlier run is delivered a second time — and decide, for each, whether the correct response is rejection, a retry, or escalation. Write the decision down in a comment next to the code, with the reasoning.
3. Add a retry path: when a checksum mismatch is detected, the supervisor asks the sender once for a fresh envelope, and only escalates if the second envelope also fails. Count the retry in the run's message budget.

**Acceptance criteria.**

- All six single mutations are rejected before any worker acts on the payload, and each rejection names the exact check that failed.
- The wrong-recipient and stale-replay cases are handled, with the chosen response (reject, retry or escalate) justified in a code comment.
- The retry path succeeds when the sender's second envelope is valid, and escalates when it is not; the retry is visible in the trace and in the message count.
- No mutation can produce a drafter reply built from corrupted state: the test `test_corrupt_payload_never_reaches_drafter` in `code/test_swarm.py` passes, and you have added a test for your new mutation cases.

## Exercise 3 — Consensus voting: two agents disagree, the rule decides

**Difficulty:** Intermediate.

**The task.** Two workers disagree about a ticket's priority — the researcher says `high`, the policy checker says `low` — and the swarm must resolve the disagreement by a rule written before the run, not by asking a model to improvise one. Using `code/demo_consensus.py`, implement and compare three resolution rules over the same disagreement: majority vote with a third voter, weighted vote in which each voter's weight comes from its measured accuracy on a held-out set, and escalation, in which a tie or a factual disagreement stops the run and routes the decision to a human with both voters' evidence attached.

**Steps.**

1. Run `code/demo_consensus.py` and confirm that majority vote resolves the two-to-one case, and that the dissenting voter's evidence is preserved in the result rather than discarded.
2. Change the third voter's verdict so the vote ties, and confirm the run escalates instead of picking a winner arbitrarily. Read the escalation record: it must contain both verdicts, both evidence lists, and the rule that fired.
3. Add a factual disagreement case: two workers return different renewal dates for the same account. Show that voting does not resolve it — the correct path is to re-fetch the source of truth, and to escalate if the sources disagree — and encode that in the schema by marking the renewal date as a verifiable fact rather than a verdict.

**Acceptance criteria.**

- Majority vote, weighted vote and escalation are three separate, named rules in code, each selectable by configuration, and the demo prints which rule decided each case.
- A tied vote escalates; it never breaks the tie by voter order, by arrival order, or by any other arbitrary rule.
- The factual-disagreement case re-fetches the source of truth and escalates when the sources disagree; no vote is taken over a fact.
- The dissenting voter's evidence survives in the final result in every case, so a later reader can see what was overruled and why.

## Exercise 4 — Bound the swarm: fan-out, rounds, and the kill-switch

**Difficulty:** Advanced.

**The task.** Take the Exercise 1 swarm and remove its safety nets one at a time, then put them back as enforced limits in code rather than as instructions in a prompt. The limits to enforce are: maximum fan-out (no more than three workers per round), maximum rounds (no more than three supervisor rounds per run), a per-worker timeout (a worker that does not answer in time is skipped and the run continues partial), and a message budget (no more than twenty handoffs per run). Then demonstrate that each limit fires: build a misbehaving worker that loops forever, a supervisor prompt that keeps asking for one more round, and a worker that spawns sub-workers, and show the run terminating cleanly in each case with a partial result and a clear status.

**Steps.**

1. Read the `SwarmLimits` dataclass and the `Supervisor.run` loop in `code/swarm.py`, and identify where each limit is checked.
2. Write the three misbehaving workers described above as subclasses of `Worker`, register each in turn, and run the supervisor against it.
3. Move one limit from code into the supervisor's prompt text only — that is, delete the code check and ask the model nicely instead — and show that the misbehaving worker now escapes the limit. Restore the code check.

**Acceptance criteria.**

- Each of the four limits fires in a test, and each firing produces a run that ends with status `partial` (or `failed` for the timeout case, per the documented rule) rather than hanging or raising an unhandled exception.
- The run that hits a limit still returns a usable partial result: the work completed before the limit is preserved, and the trace says exactly which limit fired and in which round.
- The prompt-only limit demonstrably fails against the misbehaving worker, and the write-up in your test comment explains why enforcement belongs in code.
- The full test suite passes, including your new tests.

## Exercise 5 — Peer-to-peer handoff: draw the graph, then terminate it

**Difficulty:** Advanced.

**The task.** Rebuild the triage swarm as a peer-to-peer swarm with no supervisor: the researcher hands off directly to the policy checker, which hands off to the drafter, and any agent may hand off to any other. First draw the handoff graph on paper and mark every edge that could form a cycle; then implement the swarm with a shared state object carrying a hop counter, a visited set of agent names, and a maximum hop count, and prove that a worker that hands off back to its caller terminates instead of looping.

**Steps.**

1. Implement the peer-to-peer run on top of `TaskEnvelope` and `validate_envelope` from `code/swarm.py`, reusing the same typed payloads and the same validation.
2. Add a faulty worker that hands every task back to the agent that sent it, and confirm the hop counter ends the run.
3. Write a short comparison, no more than a page, of the supervisor version from Exercise 1 and this peer-to-peer version for the triage task: which is easier to debug, which enforces the message budget more naturally, and which you would ship, with reasons.

**Acceptance criteria.**

- The peer-to-peer run completes the triage task end to end with the same typed payloads and the same validation as the supervisor version.
- The faulty worker's loop terminates at the maximum hop count, with the trace showing each hop and the visited set at termination.
- The comparison names a concrete debugging scenario — for example, a wrong priority in the final reply — and says exactly where you would look in each version's trace to find the fault.
- The full test suite passes.

## Exercise 6 — Read a trace and find the fault

**Difficulty:** Intermediate.

**The task.** Debugging a swarm is reading a trace, not re-reading code. The file `code/traces/faulty_run.jsonl` contains the structured handoff trace of a triage run that produced a wrong reply: the customer was told their renewal date was 14 March 2027, but the account record says 14 March 2026. Work out, from the trace alone, which handoff introduced the error and what kind of failure it was — a corrupted payload, a misrouted handoff, a worker hallucinating a field, or a supervisor synthesising from a summary instead of the typed value — and write the diagnosis as a short incident note: what happened, which check should have caught it, and what you would change.

**Acceptance criteria.**

- The incident note names the exact handoff (run identifier, round, sender, recipient) and quotes the trace lines that prove it.
- The note classifies the failure correctly and says which validation or provenance check should have caught it, citing the relevant function in `code/swarm.py`.
- The note proposes a concrete change — a schema constraint, a validation step, or a provenance rule — rather than a general exhortation to be more careful.

## Exercise 7 — Explain-back: subagents versus an agent team

**Difficulty:** Intermediate.

**The task.** You have a budget for one topology — argue subagents vs a team for a triage swarm that sometimes needs billing, then engineering, then billing again.

**Steps.**

1. State your choice and name who owns the context, how long each agent lives, and what bounds the cost in your chosen shape.
2. Argue the other side fairly: what does the shape you rejected buy, and what would have to be true about the tickets for it to win?
3. Tie your argument to the chapter's design rule — parallelise reads and judgements, serialise writes — and say which shape keeps the reply's single writer intact.

**Acceptance criteria.**

- The argument names the cost shape as an intuition, not a measured number, and does not present the chapter's cited multi-agent token figure as new evidence.
- The argument says exactly what would change your mind — for example, a handoff route that no coordinator could have written in advance.
- The full test suite still passes (no code change is required for this exercise).
