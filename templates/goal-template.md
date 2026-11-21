# Goal: [short goal name]

This file holds one goal for one loop, and a loop here means a repeated cycle in which an agent works on a task, checks its own result, and then decides what to do next. Keeping the goal in its own file means the agent can pick the work up again in a later session without guessing what it was trying to achieve, because the goal, the finish line, and the stopping rules are all written down in one place.

## Goal

[State the goal in one sentence. For example: Add a search endpoint to the catalogue service that returns matching items for a word query.]

## Definition of done

The definition of done is the list of verifiable conditions that must all be true before the goal counts as finished. A verifiable condition here means a condition that can be checked by running a command or reading an output, rather than by judging whether the work feels complete.

- [Condition 1, with its check. For example: The search endpoint returns matching items, verified by `pytest tests/test_search.py -q`.]
- [Condition 2, with its check. For example: An empty query returns the full item list, verified by the same test run.]
- [Condition 3, with its check. For example: Every verification command in the feature list passes, with the results recorded in the loop state file.]

## Loop type

Choose one loop type and delete the other two, then keep the choice in step with the work below.

- **Goal loop:** the loop runs until the definition of done is met, and then it stops. Use this type when the finish line is a clear outcome, such as a feature that works.
- **Timed loop:** the loop runs until a time limit is reached, and then it stops, whether or not the goal is finished. Use this type when the work must fit inside a fixed window, such as [for example, 60 minutes].
- **Maker-checker loop:** the loop runs in turns between a maker, which is the agent that produces the work, and a checker, which is the agent or the person that reviews it. The maker revises the work after each review, and the loop stops when the checker accepts it. Use this type when a second pair of eyes must approve the result before it counts as done.

Loop type: [goal / timed / maker-checker]

Time limit, if timed: [for example, 60 minutes, or not applicable]

Checker, if maker-checker: [for example, a second agent with the review instructions in AGENTS.md, or not applicable]

## Stop conditions

The loop stops as soon as any one of these conditions is true, so the agent does not keep working past the point where stopping is the right call.

- Every condition in the definition of done is true, and the verification results are recorded.
- The time limit is reached, if this is a timed loop.
- The checker accepts the work, if this is a maker-checker loop.
- The work is blocked, which means a missing dependency, a broken environment, or an unclear requirement is stopping progress, and the block is recorded in the loop state file with what is needed to unblock it.
- The same failure has appeared [for example, three] times in a row without progress, so the agent stops and asks for help instead of repeating the same attempt.
- The goal turns out to be wrong, out of scope, or no longer needed, so the agent stops and records why.

## Features in scope

List the features from `feature_list.json` that belong to this goal, so the scope stays visible and nothing extra drifts in.

- [Feature id 1]
- [Feature id 2]
