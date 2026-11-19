# ADR-001: Use a free-form ReAct loop for the support copilot, not a fixed directed acyclic graph

**Status:** Accepted
**Date:** 2026-11-18
**Author:** Course example — written for the Chapter 12 support copilot
**Deciders:** Copilot team (illustrative)

## Context

The support copilot resolves tickets that arrive in unpredictable shapes. A
refund request needs the account state first, then the refund-eligibility
check; a "where is my invoice" ticket needs the knowledge base first and the
account state only if the article does not answer the question; a subscription
change needs the subscription status before anything else. No fixed ordering
of the four Model Context Protocol tools — account state, subscription status,
knowledge-base search, refund eligibility — is right for every ticket, and
forcing tickets through a fixed order means paying for tool calls the ticket
does not need. At the same time, the copilot runs in production against
paying customers, so an unbounded loop is a cost and safety incident waiting
to happen: a loop that retries a failing refund-eligibility check forever
bills the team and retries a write operation it should have escalated. The
forces in tension are flexibility against predictability, and the cost of a
tool call against the cost of a wrong or repeated one.

## Decision

We will implement the copilot as a free-form ReAct loop — the model reasons,
acts through a tool, observes the result, and reasons again — rather than as
a fixed directed acyclic graph of steps. We considered a fixed graph with one
branch per ticket type, and rejected it because the ticket classifier that
would route between branches is itself a model call that can be wrong, and a
misrouted ticket would then run the wrong branch to completion instead of
recovering. We also considered a hybrid in which the first step is always a
knowledge-base search, and rejected it because billing tickets pay for a
search they never use. The loop is bounded by three stopping rules, recorded
here as part of the decision: a maximum of eight steps per ticket, a token
budget per ticket enforced by the runner, and a hard stop on any tool call
repeated with identical arguments, which escalates the ticket to a human
instead of retrying.

## Consequences

Positive: tickets take only the steps they need, so easy tickets are cheap;
the model can recover from a failed tool call by trying a different tool
instead of failing the ticket; and new ticket types need no new branch, only
a tool the loop can already reach. Negative: the run is non-deterministic,
so two runs of the same ticket can take different paths, which makes
trajectory evaluation harder — the golden trajectory from Chapter 10 has to
be a set of acceptable paths, not one path; the loop can in principle run to
its step limit on a confusing ticket, so the worst-case cost per ticket is
eight steps rather than the three a fixed graph would guarantee; and
debugging a bad run means reading a trace, not reading a graph. Neutral: the
loop needs a runner that enforces the stopping rules, which is a small piece
of infrastructure the fixed graph would not have needed.

## Effect on evaluation

Harder to grade. A fixed graph has one path per ticket type, so a golden
trajectory is a single sequence; the loop's trajectories vary, so the judge
has to grade outcomes (was the ticket resolved correctly, was the refund
right) rather than exact step sequences. The judge prompt therefore scores
outcome and tool-call validity, not path equality, and the evaluation set
includes tickets designed to produce divergent but valid paths.

## Effect on cost per task

Variable, bounded above. A simple ticket costs two to three model calls; a
hard ticket costs up to eight. The mean is lower than the fixed graph's,
because the graph would pay for its full branch on every ticket, but the
maximum is higher, so the cost table in the teardown reports the mean, the
95th percentile, and the maximum per task, not the mean alone.

## Effect on latency

The loop adds no fixed sequential cost, but its latency is the sum of its
steps, so a ticket that takes eight steps is roughly three times slower than
one that takes three. The four tools fan out in parallel where the ticket
allows it, so the fan-out costs the slowest member — usually the
refund-eligibility check — rather than the sum of all four. Latency is
therefore dominated by step count, which is why the stopping rules are a
latency control as well as a cost control.

## What would change this decision

We will revisit this decision if either of two triggers fires: the
95th-percentile step count exceeds five for two consecutive weeks, which
would mean the loop is wandering rather than deciding; or the evaluation
pass rate of the loop falls more than 0.02 below a fixed-graph prototype on
the same ticket set, which would mean the flexibility is costing more
correctness than it buys. Either trigger would reopen the fixed-graph design,
and this record would be marked superseded by the record that replaces it.
