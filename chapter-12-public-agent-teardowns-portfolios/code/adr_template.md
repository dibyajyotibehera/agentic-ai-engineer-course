# ADR-NNN: <Short noun phrase naming the decision>

**Status:** Proposed | Accepted | Superseded by ADR-XXX | Deprecated
**Date:** YYYY-MM-DD
**Author:** <name>
**Deciders:** <names of the people who agreed the decision>

## Context

Describe the forces at play, in full sentences and in value-neutral language:
what the system has to do, what constrains it, and where the forces are in
tension with each other. A reader who was not in the room should be able to
reconstruct why a decision was needed at all. If a previous architecture
decision record (ADR) created the context for this one, link to it here.

## Decision

State the decision in the active voice, in full sentences: "We will …".
Name the option chosen and the options rejected, with the reason for each
rejection. A decision that does not name what was rejected is not a decision,
it is a preference.

## Consequences

List all consequences of the decision, positive, negative, and neutral. Do
not hide the downsides: a decision whose costs are hidden cannot be revisited
honestly. If a consequence becomes the context for a later ADR, say so.

## Effect on evaluation

How does this decision change what the agent costs to grade? Does it add a
model call the judge has to read, make trajectories longer or harder to
compare, or make a golden trajectory easier to write? Name the concrete
effect; "no effect" is an acceptable answer if it is true.

## Effect on cost per task

How does this decision change the cost of one task? Name the added or removed
model calls, tool calls, or tokens per task, and the direction of the change.
If the effect is not yet measured, say so and say how you will measure it.

## Effect on latency

How does this decision change the time one task takes? Does it add a
sequential step to the critical path, or run in parallel with something else?
Name the step and whether it is on the critical path.

## What would change this decision

State the trigger for revisiting the decision: a measured threshold, a new
capability, a price change, or a failure mode. "We will revisit this if the
95th-percentile latency of the refund-eligibility check exceeds 2 seconds for
a week" is a trigger; "if things change" is not.

---

*Format: "Documenting Architecture Decisions" (2011),
https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions,
with the three agent-specific fields (evaluation, cost per task, latency)
added for this course. Templates and examples:
https://github.com/joelparkerhenderson/architecture-decision-record*
