# Chapter 10 — Trajectory Evals & CI Gates

## The idea in plain words

An agent is not a function that returns an answer; it is a run that
does things in an order, and the order is often where the damage
lives. Consider a support copilot that handles a refund request. It
fetches the order, checks whether the customer is eligible, issues
the refund, and confirms it, and when you look at the end of that
run the customer has the right amount of money back, so every
evaluation you run on the final state says the run was a success.
Now imagine the same copilot on a slightly different day, when it
issues the refund first and checks eligibility afterwards. The
customer still ends up with the right amount, the final state is
identical, and every end-state evaluation still passes, yet the run
is a failure, because an agent that refunds before it checks
eligibility will eventually refund someone who is not eligible, and
on that day the money will be wrong and there will be no evaluation
left to catch it, since the evaluation only ever looked at the
ending.

That is the gap this chapter closes. A trajectory evaluation grades
the run step by step instead of grading only its ending, so it can
see the ordering error that the ending hides, and a continuous
integration (CI) gate turns that grading into a release rule, so a
prompt change or a model swap that introduces the error cannot merge
in the first place. The rest of the chapter is the machinery that
makes those two sentences real: a way to write down what a good run
looks like, a way to measure how far a real run drifted from it, a
way to use a language model as a judge for the parts code cannot
see, and a way to wire all of it into the build so that it blocks a
regression the way a failing unit test blocks a regression.

## Why the ending is not enough

Every evaluation method you met in earlier chapters has the same
blind spot, and it is worth naming before the machinery arrives,
because the machinery only makes sense as an answer to it. A
retrieval-augmented generation (RAG) metric such as faithfulness,
which the [Ragas](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)
library defines as the fraction of claims in a response that the
retrieved context supports, judges a finished answer against its
evidence, and a final-answer comparison judges a finished answer
against a reference answer, and both are useful, but neither of them
knows what the agent did to get there. An agent can call a write
tool before its guardrail check, skip a verification step and get
lucky, call the same tool three times when once would do, or take a
detour through a tool that leaks data, and if the final answer comes
out right, none of those evaluations will ever notice. The failure
modes that matter in production agents are mostly failures of
sequence and of side effect, not failures of prose, which is why the
evaluation has to follow the sequence.

There is a second reason the ending is not enough, and it is about
who is watching. In production, the trace is the only record of what
the agent did, and the trace is also the raw material for the next
round of evaluations, because the best eval cases are the failures
you have already seen in real traffic rather than the cases you
invented at your desk. That loop — observe a failure in a trace,
turn it into a case, grade future runs against it — is the working
method of the whole chapter, and it is the same loop that
[Microsoft's Lesson 10 on agents in production](https://microsoft.github.io/ai-agents-for-beginners/10-ai-agents-production/)
describes as evaluate offline, deploy, monitor online, collect new
failure cases, add them to the offline dataset, and repeat. The
offline half of that loop is what you are building here, and the
online half is what it feeds.

## Step-level grading: a verdict per step

A step-level grader works through a trajectory the way a careful
reviewer works through a pull request, checking each step in turn
and stopping at the first thing that is wrong, except that unlike a
reviewer it also keeps going, because you want the full list of
failures for the report, not just the first one. The grader in
`code/grader.py` runs five checks on a support-copilot refund run,
and each check answers a question that an end-state grader cannot
ask. The ordering check asks whether every tool call respects the
required order, which is the check that catches the refund issued
before the eligibility check. The required-steps check asks whether
every step that must happen did happen, and whether any tool was
called that should never have been called at all. The branching
check asks whether the run took exactly one of the mutually
exclusive paths — refunding and denying the same request in one run
is a contradiction, not a thorough job. The argument check reconciles
the refund against its authorisation, asking whether the amount, the
currency, and the order identifier match what the eligibility check
approved, because a correct outcome with the wrong arguments is a
failure that a final-state check would wave through. And the outcome
check asks whether the run ended where the case expected it to end,
which is the one place the end-state grader's question still
belongs.

The important property of these checks is that they are code, not
model calls, which means they are deterministic, free, and fast
enough to run on every pull request, and that is what makes them
suitable as a gate. A grader that costs money and gives a different
answer each time cannot be a release rule, because a release rule
has to give the same verdict on the same code twice. The model-based
grader arrives later in this chapter for the things code cannot see,
but it sits on top of the code grader, and it never replaces it.

## Golden DAGs: the fixture is a graph, not a script

To grade a run step by step you need something to grade it against,
and the natural way to write that something down is a golden
trajectory, a recorded run that did the task correctly. But a single
recorded run is too strict to be a fixture, because it freezes
accidents of the recording alongside the requirements: if the golden
run fetched the order before it greeted the customer, then a run
that greets first and fetches second is a different sequence, and a
sequence comparison will fail it even though nothing about the task
requires the fetch to come first. The fix is to write the fixture as
a directed acyclic graph (DAG) rather than as a sequence, so that
the fixture says which steps must come before which others, and
says nothing at all about steps that are independent of each other.

The golden DAG for the refund task, in
`code/data/golden_refund_dag.json`, has six nodes and one branch.
Receiving the request comes first, then fetching the order, then
checking eligibility, and those three edges are the spine of the
task, because each step needs the output of the one before it. After
the eligibility check the graph branches: the approved path issues
the refund and then sends the confirmation, while the denial path
sends a denial instead, and the two paths are mutually exclusive, so
a run that takes both is invalid by construction. Any topological
ordering of this graph — any order that respects the edges — is an
acceptable run, which is exactly the right amount of strictness: the
fixture forbids the refund before the eligibility check, and it
permits everything else.

That distinction, between what must be ordered and what may be
reordered, is the whole reason the fixture is a graph. A single
golden sequence would fail runs that did nothing wrong, and a team
that is failed for doing nothing wrong learns to distrust the
evaluation, and then to ignore it, and then the gate is dead. A DAG
fixture is also cheaper to maintain than a pile of sequences, because
one graph covers every valid ordering of the task, and when the task
changes — when a new verification step is added, say — you add one
node and its edges instead of rewriting every recorded run.

Building the fixture is the hard part, and the honest way to do it
is to start from real traces rather than from imagination. The
practice that the [LLMOps.si guide to building an eval dataset](https://www.llmops.si/articles/how-to-build-your-first-eval-dataset/)
recommends, and that this chapter follows, is to mine the first
cases out of production: pull recent traces and filter for
thumbs-down ratings, escalations, retries, and low-confidence
answers, take the handful of bug reports where someone said the
agent got it wrong, and ask the support team for the questions that
trip it up. Twenty real, painful cases beat two hundred imagined
ones, and the guide's progression is to start with 20 to 50 cases
covering the top intents and known failure modes, and grow to
roughly 100 to 200 once the set is gating releases. The dataset
shipped with this chapter is deliberately small — four trajectories
for one behaviour, the refund — because the point of the first
dataset is to prove the pipeline works, and a dataset that starts
small and grows from real failures stays representative, while a
dataset that starts large and invented stays noisy.

## Trajectory distance: how far did the run drift?

A grader tells you whether a run passed, and a distance metric
tells you how far a failing run drifted from the golden path, and
you need both, because a run that missed by one step and a run that
wandered off entirely are different problems that deserve different
responses. The metrics in `code/trajectory.py` are implemented from
scratch on plain lists of tool names, and there are three of them
because each one sees a different kind of drift. Edit distance, the
Levenshtein distance applied to tool names instead of characters,
counts the insertions, deletions, and substitutions needed to turn
the run's sequence into the golden sequence, so swapping two adjacent
steps costs two edits, and the normalised form divides by the length
of the longer sequence, which makes the number comparable across
runs of different lengths — the same swap scores 0.5 in a four-step
run and 0.1 in a twenty-step run, which matches the intuition that
the longer run drifted less. The longest-common-subsequence
similarity counts how much of the run survived intact while ignoring
order, so a run that reordered its steps but kept them all scores
well here while still paying an edit-distance penalty, and that
split is informative: it tells you the run did the right things in
the wrong order rather than doing the wrong things. And Jaccard
similarity compares the two step sets while ignoring order entirely,
which is a coarse first signal, and also a warning, because a run
that called all the right tools in the wrong order scores a perfect
1.0 on it, which is exactly why order-aware metrics have to sit
alongside it.

The ordering-error trajectory in the chapter's dataset makes the
point concretely. Against the golden tool sequence its edit distance
is 2, its normalised distance is 0.4, it is missing no steps and it
has no extra steps, and its longest-common-subsequence similarity is
above 0.5. Read together, those numbers say the run did everything
it was supposed to do and did it in nearly the right order, which is
precisely the profile of a failure that an end-state evaluation
misses and that a human reviewer would call a near miss — except
that the near miss is a refund issued before authorisation, so the
distance numbers are descriptive and the grader's verdict is
decisive, and the two are not in tension. Distance is for triage and
for trend-spotting across many runs; the gate is for the verdict,
and the gate uses the grader, not the distance.

There is one more thing distance is good for, and it is the reason
the metrics are in the chapter at all: they give you a way to watch
a whole fleet of runs rather than one run at a time. If the average
normalised edit distance across a week's traffic creeps upward after
a model change, something about the new model is wandering further
from the golden paths even though most individual runs still pass,
and that drift is the earliest warning you will get that the next
regression is coming. A gate catches the regression that already
happened; a distance trend catches the one that is forming.

## LLM-as-a-judge: the grader for what code cannot see

Some things about a run cannot be decided by code at all. Whether
the agent's final message is clear, whether its tone fits a customer
who has just been refused a refund, whether its explanation actually
answers the question that was asked — those are judgements, and the
standard way to automate them is to ask a second language model to
grade the run against a rubric, which is the
[LLM-as-a-judge](https://docs.langchain.com/langsmith/evaluation-concepts)
pattern that [LangSmith's evaluation documentation](https://docs.langchain.com/langsmith/evaluation-concepts)
describes alongside code-based and pairwise evaluators. The judge is
given the trajectory and the rubric, and it returns a verdict, and
the reason this works as well as it does is that grading is a much
easier task than doing: the judge does not have to solve the
customer's problem, only to recognise whether the solution on the
page matches the criteria in front of it.

But a judge is also the least trustworthy grader in this chapter,
and the reasons are structural rather than fixable by a better
prompt, so they are worth naming before you use one. Judges prefer
longer answers even when the extra words add nothing, which is
verbosity bias; they favour whichever of two candidates is presented
first, and swapping the order often flips the verdict, which is
position bias; they rate outputs from their own model family more
highly than identical outputs from another family, which is
self-preference; and they anchor on the first scores in a batch, so
a generous early grade lifts the rest of the batch. The defences are
unglamorous and they work: grade against a fixed rubric with named
criteria instead of an open-ended question about quality, keep the
judge's temperature at zero, run the same case more than once and
look at the variance, swap candidate order when you compare two
outputs, and — the one that matters most — measure the judge against
human labels on a held-out set before you trust it, and re-measure
it whenever the judge model or the rubric changes. The
`measure_agreement` function in `code/judge.py` does that
measurement, and it reports false passes and false fails separately,
because they mean different things: a false pass lets a bad run
ship, while a false fail wastes a human's time on re-review, and it
is the false-pass rate that decides whether a judge may block a
release.

Cost is the reason a judge is worth building despite all of that.
In [LangChain's September 2026 head-to-head study of judges for agent evaluations](https://www.langchain.com/blog/jev-is-now-available-in-langsmith-evals),
a specialised typed judge — TypeSafe AI's Jev, a model that returns
a typed answer and a probability instead of generating free text —
matched the human pass/fail label on all 500 repeated decisions, at
a total cost of $0.34 against $28.17 for Claude Sonnet 4.6 as judge,
which is roughly 83 times cheaper, and with 92 to 913 times lower
variance than the large-language-model judges. The study was narrow
— one weather agent, five cases, one human reviewer — so those
numbers are evidence that the shape of the solution matters, a
small calibrated typed decision instead of free-text reasoning, not
proof that any cheap judge is good. The practical consequence is
that the judge layer has its own cost curve to manage: a
large-language-model judge is the expensive way to grade, a small
classifier is the cheap way to grade a narrow question, and the
right choice depends on how much judgement the rubric actually
needs.

That same cost logic applies one level down, to the claim-check
inside a faithfulness score. [Ragas](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)
defines faithfulness as supported claims divided by total claims,
and its worked example makes the arithmetic concrete: an answer
that makes two claims, one of which the retrieved context supports,
scores 1 divided by 2, which is 0.5. The companion metric, context
recall, runs the fraction the other way, counting the claims in the
reference answer that the retrieved context supports, so a low
context recall blames retrieval for never fetching the evidence
rather than blaming the generator for ignoring it. And for the step
of checking each claim against the context, Ragas ships a variant
that uses [Vectara's HHEM-2.1-Open](https://huggingface.co/vectara/hallucination_evaluation_model),
a free, small, open-source T5 classifier trained to detect
hallucinations, instead of another language-model call — which is
exactly the trade the Jev study makes at the judge level, repeated
at the claim level: a small specialised model, running locally, at
zero cost per call and with the same answer every time, for the
narrow question it was trained to answer. `code/faithfulness.py`
implements the faithfulness and context-recall arithmetic with a
deliberately naive stand-in for the claim check, so the formula is
visible and the pipeline runs offline, and the HHEM classifier is a
marked stub where the real model plugs in.

## The CI gate: the evaluation becomes a release rule

Everything so far has been about grading one run, and the gate is
about what happens when the grading becomes a rule that a change
has to pass. The gate in `code/ci_gate.py` runs the whole eval
suite, aggregates the results per criterion, compares each aggregate
against a baseline that is committed in the repository next to the
evals, and exits non-zero when the build has regressed, and a
non-zero exit is what makes a gate a gate, because the GitHub
Actions workflow in `code/eval-gate.yml` fails the build on it, so a
bad prompt or model change cannot merge. The workflow runs on every
push and pull request that touches the agent, its prompts, or its
evals, and it uploads the eval report as an artifact, so a failed
build can show exactly which case and which criterion regressed
instead of failing silently.

Three design decisions are doing the work in that gate, and each of
them is a response to a way gates die in practice. First, the gate
compares aggregates, not single cases. One case can flip for a
silly reason — a flaky tool, a paraphrase the grader did not expect
— and blocking a release on it trains the team to ignore the gate,
so the gate compares each criterion's pass rate against its baseline
minus a tolerance of two to three points, and a real regression has
to move the aggregate to trip it. Second, the baseline is a file in
the repository, not a number in someone's head. A change that
improves a score updates the baseline in the same pull request, with
the improvement visible in the diff, and a change that lowers a
score has to explain itself, which turns the baseline into a record
of what the team agreed good looks like. Third, a small set of cases
is marked critical and bypasses the tolerance entirely, because some
failures are not a matter of degree. The refund-before-eligibility
case in this chapter is one: a refund issued before authorisation is
never acceptable at any pass rate, so it fails the build on its own,
and the dataset as shipped demonstrates exactly that, since running
`python ci_gate.py` exits 1 on the critical case.

The numbers that calibrate the gate come from the chapter's worked
regression example, and they are worth holding on to as a sense of
scale. A criterion score falling from 0.92 to 0.71 is a regression
of twenty-one points, far outside a two-to-three point tolerance,
and it is the kind of drop a gate exists to catch; a regression
suite of 100 to 300 cases runs in about two minutes, which is why
there is no excuse for running it only nightly; and the tolerance
exists because a suite that fails on noise gets disabled, while a
suite that tolerates a twenty-point drop is decoration. The gate is
not trying to prove the agent is good. It is trying to prove that
this change did not make it worse, and those are different claims
with different burdens of proof.

The gate is also only the first half of the release story, because
no offline suite, however large, can see what only live traffic
shows. The second half is the canary, and the canary has its own
verdict function, implemented in `code/ci_gate.py` from the
[LLMOps.si deployment guide](https://www.llmops.si/articles/llm-deployment-ci-cd-staging-rollback/).
A canary starts at 5% of traffic, and the verdict needs at least
2,000 canary requests before it says anything, with four checks:
error rate at or below the stable version's rate times 1.2 plus
0.001, 95th-percentile latency at or below the stable latency times
1.15, eval pass rate at or above the stable pass rate minus 0.02,
and cost per request at or below the stable cost times 1.10. The
instructive case is the canary that passes latency and errors but
fails eval pass rate or cost, because a classic service canary
would have promoted it, and it is exactly the failure that only a
language-model deployment can have — the quality of the answers got
worse, or each answer got more expensive, while the service stayed
healthy. Rollback is a configuration flip, not a redeploy, which is
what makes the verdict cheap to act on. The continuous-integration
gate and the canary verdict are two halves of the same idea: the
gate stops a regression before it ships, and the canary catches the
regression the gate could not see.

## The grounded numbers

| Number | What it means | Source |
| --- | --- | --- |
| $0.34 vs $28.17, about 83× cheaper | A typed judge (Jev) against Claude Sonnet 4.6 as judge, same 500 decisions | [LangChain, Jev in LangSmith Evals](https://www.langchain.com/blog/jev-is-now-available-in-langsmith-evals) |
| 500 of 500 | The typed judge matched the human pass/fail label on every repeated decision (five cases, one hundred repeats each) | [LangChain, Jev in LangSmith Evals](https://www.langchain.com/blog/jev-is-now-available-in-langsmith-evals) |
| 92–913× lower variance | The typed judge's per-case variance against the three large-language-model judges | [LangChain, Jev in LangSmith Evals](https://www.langchain.com/blog/jev-is-now-available-in-langsmith-evals) |
| 1 ÷ 2 = 0.5 | Faithfulness on the Ragas worked example: two claims, one supported by the retrieved context | [Ragas, Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/) |
| 20–50, then about 100–200 | Cases to start an eval dataset with, then the size to grow to once it gates releases | [LLMOps.si, How to build your first eval dataset](https://www.llmops.si/articles/how-to-build-your-first-eval-dataset/) |
| 100–300 cases in about 2 minutes | The size and run time of a regression suite that can gate every change | Chapter worked example (verified facts, November 2026) |
| 0.92 → 0.71 | A criterion score falling twenty-one points: a regression the gate must catch | Chapter worked example (verified facts, November 2026) |
| 2–3 points | The tolerance on an aggregate before the gate trips | Chapter worked example (verified facts, November 2026) |
| 5% of traffic, at least 2,000 requests | Where a canary starts, and how much traffic a verdict needs | [LLMOps.si, LLM Deployment: CI/CD, staging and one-step rollback](https://www.llmops.si/articles/llm-deployment-ci-cd-staging-rollback/) |
| Error ≤ stable × 1.2 + 0.001; p95 latency ≤ stable × 1.15; eval pass ≥ stable − 0.02; cost ≤ stable × 1.10 | The four canary checks | [LLMOps.si, LLM Deployment: CI/CD, staging and one-step rollback](https://www.llmops.si/articles/llm-deployment-ci-cd-staging-rollback/) |

## The principal-level interview angle

The question this chapter prepares you for is not "how do you
evaluate an agent", because any engineer who has read the
documentation can answer that, but "what does your regression gate
actually prove, and what can it not prove", and the difference
between a good answer and a weak one is whether you can draw the
line between the two precisely. A regression gate proves that this
change did not make the agent worse *on the behaviours the dataset
covers*, measured by the graders you have, against the baseline you
committed. That is a narrow claim, and its narrowness is its
strength: it is checkable, it is repeatable, and it fails loudly
when it is false. What it cannot prove is that the agent is good,
because the dataset covers the failures you have already seen and
not the ones you have not, and a model change can introduce a new
failure mode that no existing case exercises, which is why the
dataset has to keep growing from production traces, and why the
canary exists as the second half of the release story. It cannot
prove that the graders are right, because a code grader encodes
your assumptions about the task and a language-model judge carries
the biases named above, and both need their own validation — the
judge against human labels, the code grader against the cases it was
built to catch. And it cannot prove that a passing aggregate means
every case is fine, which is why the critical-case list exists: the
aggregate is a summary, and summaries hide the one failure that
matters, so the failures that are never acceptable bypass the
summary entirely.

The follow-up question is usually about what you do when the gate
fails, and the answer separates engineers who have run a gate from
engineers who have only read about one. You do not raise the
tolerance, and you do not delete the failing case, because both of
those teach the team that the gate is negotiable, and a negotiable
gate is soon a dead gate. You read the report, you find the first
failing step, and you decide which of three things happened: the
change genuinely regressed the agent, in which case you fix the
change; the case is wrong or stale, in which case you fix the case
in the same pull request and say so in the diff; or the baseline is
out of date because the agent legitimately improved, in which case
you raise the baseline and the improvement is visible to every
reviewer. Those are the only three moves, and the discipline is in
never reaching for a fourth.

The last thing worth saying in an interview is what the gate costs,
because a principal engineer is expected to have done that
arithmetic. The code graders are free and deterministic, the
distance metrics are a few lines of Python, and a regression suite
of a few hundred cases runs in about two minutes, so the gate itself
is nearly free. What costs money is the judge layer, and the numbers
above are the reason to spend it carefully: a typed judge at $0.34
against a large-language-model judge at $28.17 for the same run is
not a marginal saving, it is the difference between a judge you can
afford to run on every pull request and a judge you can only afford
to run nightly, and a nightly judge is a judge that tells you about
a regression a day late. The cheapest gate that still catches the
regression is the right gate, and the most expensive mistake is a
gate so costly that nobody runs it.

## What to run first

Start in `code/`: run `python ci_gate.py` and watch the gate fail
on the critical ordering case, then run `python -m unittest
test_trajectory -v` and read the tests, because the tests are the
specification of what each piece promises. Then work through
`exercises.md`, which builds the grader, the golden DAG, the
distance metric, and the gate in that order, and ends with the
canary verdict that completes the release story.
