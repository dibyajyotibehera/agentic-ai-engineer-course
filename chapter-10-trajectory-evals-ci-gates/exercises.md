# Chapter 10 — Exercises

Work in `code/`. Everything except the live judge and claim checker
runs on the standard library, with no network and no API key. The
live path — `LLMJudge`, `LLMClaimChecker`, and `demo_live.py` —
needs a configured model backend: your own key (`LLM_API_KEY`) or
`LLM_BACKEND=opencheap-connector` on a machine where the OpenCheap
connector is registered. Run the tests first so you know what the
finished pieces promise:

```bash
cd code
python -m unittest test_trajectory -v
```

## Exercise 1 — A step-level grader that catches the ordering error

**Difficulty:** Foundation.

Take the support-copilot scenario from the lesson: a customer asks
for a refund, and the agent must fetch the order, check eligibility,
issue the refund, and confirm it, in that order. The trajectory in
`code/data/trajectories.json` with case id `refund-ordering-error`
issues the correct refund for the correct amount — but it calls the
refund tool *before* the eligibility check.

Write your own grader for this scenario from scratch (do not just
call `StepLevelGrader`): it should take a trajectory and return a
verdict per check, with the step index where each failure happened.
Then grade all four trajectories in the dataset with it.

**Acceptance criteria:**

- The ordering-error trajectory fails, and the failure names the
  ordering check and the step where the refund was issued.
- The same trajectory passes the outcome check, because the customer
  did get the correct refund — your grader must be able to hold both
  facts at once.
- The golden trajectory passes every check, and the denied-ineligible
  trajectory passes every check.
- A trajectory you construct yourself, in which the refund amount is
  changed to $1.00 while the eligibility check authorises $79.00,
  fails on the argument check and on nothing else.

## Exercise 2 — A golden DAG fixture for a new task

**Difficulty:** Foundation.

Design a golden directed acyclic graph (DAG) for a password-reset
agent. The task: a user asks to reset their password, and the agent
must verify the user's identity, check that the account is not
locked, generate a reset token, send it through the channel the user
chose, and confirm completion. Two complications belong in the
fixture: identity verification may use either an email code or a
security question, but never both in one run; and if the account is
locked, the agent must tell the user to contact support instead of
generating a token.

Write the fixture as JSON in the same shape as
`code/data/golden_refund_dag.json`, then write three trajectories
against it: one golden run using the email code, one golden run using
the security question, and one run that generates the token before
checking whether the account is locked.

**Acceptance criteria:**

- `GoldenDAG.validate` accepts both golden runs and reports no
  missing steps for either, even though each one skips the other
  verification method.
- The locked-account trajectory fails on a precedence violation, not
  on a missing step.
- A fourth trajectory that both verifies by email code *and* asks a
  security question fails on a branch violation.
- Write one paragraph explaining why a single golden sequence would
  have failed one of your two golden runs.

## Exercise 3 — A trajectory-distance metric from scratch

**Difficulty:** Intermediate.

Implement edit distance, longest-common-subsequence similarity, and
Jaccard similarity on lists of tool names, without importing any
existing implementation. (The reference implementation in
`code/trajectory.py` is there to check your work against, not to
copy.) Then use your metrics to answer three questions about the
chapter's dataset, with numbers:

1. How far is the ordering-error trajectory from the golden
   trajectory, on each of the three metrics?
2. A run that calls the tools in exactly the reverse order scores
   what on each metric — and which metric is misleading about it?
3. Build a trajectory that is missing the confirmation step and also
   calls an extra, unnecessary tool. Which metric notices the extra
   tool, and which metric notices the missing step?

**Acceptance criteria:**

- Your edit distance agrees with the reference on at least ten
  hand-written cases, including the empty-sequence cases and a case
  where the two sequences share no steps at all.
- Your answer to question 2 names the misleading metric and explains
  why, in terms of what that metric ignores.
- Your answer to question 3 gives the numbers for the constructed
  trajectory on all three metrics.

## Exercise 4 — Judge a judge: measure agreement with humans

**Difficulty:** Intermediate.

Take ten trajectories from the chapter's dataset plus six you write
yourself (three good runs, three runs with a tone problem in the
final message, such as apologising for an error that did not happen
or leaving a placeholder in the text). Label all sixteen by hand:
pass or fail, with one sentence each for why. Then grade the same
sixteen with the `MockJudge` in `code/judge.py`, and compute the
agreement with `measure_agreement`.

Now break the judge on purpose. Rewrite two of the failing final
messages to be twice as long without fixing the underlying problem,
and re-grade them. Then swap the presentation order of two
candidates in a pairwise comparison and see whether the verdict
changes. (The mock judge does not have position bias — so for the
second half, use the `LLMJudge` with a configured backend; see
`code/demo_live.py` for the setup, and `code/README.md` for the two
ways to configure it. The fixed `MockJudge` is the instrument you
measure agreement against, not the judge you test for bias: a bias
experiment needs a judge whose verdict can actually move.)

**Acceptance criteria:**

- A table of the sixteen cases with your label, the judge's label,
  and the agreement rate, false-pass count, and false-fail count.
- The verbosity experiment shows whether the longer failing messages
  changed the judge's verdict, with the before-and-after verdicts
  quoted.
- A written decision: would you let this judge block a release?
  The answer must rest on the false-pass rate, not on the agreement
  rate alone, and it must say what would have to change for the
  answer to become yes.

## Exercise 5 — The CI gate: make it fail, then make it pass

**Difficulty:** Advanced.

Copy `code/eval-gate.yml` to `.github/workflows/eval-gate.yml` in a
test repository of your own, or simulate the workflow locally by
running `python code/ci_gate.py` and checking its exit code. Then
work through the regression in three stages:

1. **Watch it fail.** Run the gate as shipped. It exits 1 because
   the critical ordering-error case fails. Read `eval_report.json`
   and confirm the breach names the critical case, not the
   aggregate.
2. **Plant a broad regression.** Lower the `ordering` criterion in
   `code/data/baseline.json` is the wrong move — instead, simulate a
   prompt change that drops a criterion score from 0.92 to 0.71, the
   lesson's worked example: write a small script that calls `gate`
   with those numbers and a tolerance of 0.03, and confirm the gate
   fails on the aggregate breach. Then show that the same drop with
   a tolerance of 0.25 would pass, and write one paragraph on why
   that tolerance would make the gate decoration.
3. **Fix the agent, not the gate.** Change the ordering-error
   trajectory so the eligibility check comes before the refund, as a
   fixed agent would produce, and update the baseline in the same
   change to reflect the improved score. Run the gate again: it must
   exit 0, and the baseline diff must show the improvement.

**Acceptance criteria:**

- The as-shipped gate fails with exit code 1, and you can quote the
  breach from the report.
- The planted 0.92 → 0.71 regression fails the gate at tolerance
  0.03 and passes at tolerance 0.25, with the paragraph explaining
  why the second tolerance is wrong.
- After the fix the gate exits 0, and no tolerance or threshold was
  changed to achieve it — only the trajectory and the baseline.
- The workflow file runs in your test repository, or you record the
  exact step where a real GitHub Actions run would need a secret or
  a runner you do not have.

## Exercise 6 — The canary verdict

**Difficulty:** Advanced.

Implement `canary_verdict` yourself, from the thresholds in the
lesson, without reading the reference implementation in
`code/ci_gate.py` until yours is written: a canary starts at 5% of
traffic; a verdict needs at least 2,000 canary requests; then error
rate at or below stable times 1.2 plus 0.001, 95th-percentile
latency at or below stable times 1.15, eval pass rate at or above
stable minus 0.02, and cost per request at or below stable times
1.10.

Then write the test cases that matter, using a stable version with
90,000 requests, error rate 0.004, p95 latency 900 milliseconds,
eval pass rate 0.93, and cost per request $0.012:

1. A canary with 1,500 requests: the verdict must be "wait".
2. A canary with 4,800 requests and every number inside its bound:
   the verdict must be "promote".
3. A canary with latency and errors fine but eval pass rate 0.85:
   the verdict must be "roll back", naming only the eval pass rate.
4. A canary that fails on cost alone: the verdict must name only
   the cost.
5. A boundary case of your own choosing that sits exactly on one
   threshold, with a note on whether the verdict should treat the
   boundary as passing or failing, and why.

**Acceptance criteria:**

- All five cases pass against your implementation, and your
  implementation agrees with the reference `canary_verdict` on all
  of them.
- Case 3 includes a one-paragraph explanation of why a classic
  service canary, watching only latency and errors, would have
  promoted the release that your verdict rolls back.
- The boundary note states a rule (inclusive or exclusive) and
  applies it consistently, rather than leaving the boundary
  undefined.

## Exercise 7 — Measure the verification gap, then close it

**Difficulty:** Intermediate.

Take five small tasks the agent can plausibly get wrong — five
short coding tasks with a test suite you wrote, or five
refund-style tasks with a checker that confirms the outcome
against the authorisation. Run the agent on each one, and for
every task record two facts in a table: whether the agent claimed
it was done, and whether the work was actually correct when you
verified it independently, using tests or checks the agent did not
write. Compute the verification gap as false completion claims
divided by total completion claims, and write it as a fraction and
a decimal.

Then close the gap. Change the harness so the agent may only
report completion after running a verification command that you
fixed in advance — the test suite, or the checker — and only when
that command succeeds. Record the command and its result in the
trace next to the claim. Run the same five tasks again and compute
the gap a second time.

**Acceptance criteria:**

- A table of the five tasks with a claimed column and a verified
  column, and the gap stated as a fraction and a decimal for both
  runs.
- The verification is independent: the checks are ones the agent
  did not write, and the verification command is fixed by the
  harness rather than chosen by the agent.
- The second run's gap is lower than the first, or a written
  explanation of which false completion survived the gate and why.
- One paragraph on what a high gap would mean for the CI gate:
  which number in the gate report it would move, and why a pass
  rate on its own would miss it.
