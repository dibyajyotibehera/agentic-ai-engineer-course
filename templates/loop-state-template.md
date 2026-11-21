# Loop State: [goal name]

This file carries a loop between sessions, so the agent can stop at any point and resume later without losing its place. A session here means one stretch of work, and the loop state records where that stretch ended, which means the next session starts by reading this file, not by reconstructing the work from memory. Update this file at the end of every session, and update it again whenever a verification result changes.

## Current goal

[Copy the one-sentence goal from the goal file, so this file stays readable on its own.]

Goal file: [path to the goal file, for example, `goals/search-endpoint.md`]

Loop type: [goal / timed / maker-checker]

## Current step

[Describe the step the loop is on, in one or two sentences. For example: The search endpoint is written and the first test passes, and the empty-query behaviour is being added next.]

## Features

These lists mirror `feature_list.json`, which is the machine-readable record of the same features. Keep the two files in step with each other, and treat `feature_list.json` as the source of truth when they disagree.

Done (passing):
- [Feature id, with the date it passed and the verification command that proved it.]

In progress (active):
- [Feature id, with what is done so far and what remains.]

Blocked:
- [Feature id, with what is blocking it and what is needed to unblock it.]

Not started:
- [Feature id.]

## Last verification result

[Record the most recent verification run here, so the next session can see what was already proved and what still fails.]

- Command run: [for example, `pytest tests/test_search.py -q`]
- When it ran: [date and time]
- Result: [pass or fail]
- Output summary: [for example, 2 passed and 1 failed, with the failing test name and the error in one line]
- What changed since: [for example, nothing, or the empty-query branch was added after this run]

## Next action

[Write the single next action, so the next session starts with one clear step instead of a list of options. For example: Add the empty-query branch to the search endpoint, then run `pytest tests/test_search.py -q` and record the result above.]

## Notes

[Anything the next session needs to know, such as a decision that was made, a workaround that was tried, or a question waiting for an answer.]
