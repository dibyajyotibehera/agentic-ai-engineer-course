# Chapter 9 — Self-Correction & Reflection Loops

## The idea in plain words

Every agent you have built so far in this course makes its first attempt and
then, if you are lucky, somebody notices the mistake. A support agent drafts
the wrong refund, a coding agent writes a function that crashes on an empty
list, a research agent quotes a document that does not say what it claims —
and in each case the error is discovered downstream, by a test, a customer,
or a human reviewer, long after the agent has moved on. This chapter is
about closing that gap inside the agent itself, so that the agent notices its
own error, works out what went wrong, tries again with the failure in front
of it, and — the part that makes the whole thing safe to run in production —
knows when to stop trying.

The intuition is older than large language models (LLMs). A human programmer
does not write a function, run it once, and ship whatever comes out; they
write it, run the tests, read the failure, and change the code in light of
what the failure says. The loop is *generate, execute, diagnose, repair*,
and the diagnosis is the load-bearing step, because a repair made without a
diagnosis is a guess, and guesses compound. What is new is that the agent
can now perform every step of that loop itself, provided it is given the
right structure: something that can judge its output, something that can read
an error trace, and a set of rules that bound how long the loop is allowed
to run.

There is, however, a trap at the heart of this chapter, and it is worth
stating before anything else, because it shapes every design decision that
follows. Asking a language model to check its own reasoning, with no outside
evidence, does not reliably make its reasoning better. [Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798) showed this directly: when a model critiques and revises
its own reasoning with no external feedback — what the paper calls intrinsic
self-correction — accuracy fails to improve and often goes down, as the model
talks itself out of correct answers. The lesson is not that self-correction
is impossible; it is that the correction signal has to come from somewhere
outside the model's own head. A test suite, a compiler, a traceback, a
retrieval evaluator, a second model with a different brief — these are all
external signals, and every pattern in this chapter is built around one of
them. A loop that only ever asks the model whether it did well is not
self-correction; it is self-congratulation with extra steps.

That framing also explains where this chapter sits in the course. Chapter 6
gave you the sandbox in which agent-generated code can be executed safely,
Chapter 8 gave you the human approval gate for the cases a loop cannot
resolve, and Chapter 10 will grade whole trajectories so you can prove a
loop is helping rather than merely spinning. This chapter is the loop itself:
the critic that judges, the parser that reads the trace, and the budget that
decides when the loop has had enough.

## The mechanism

The mechanism is best understood as one loop with five named patterns
inside it, each of which you could lift out and use on its own, and each of
which answers a different question the loop has to ask. The loop begins when
the agent produces a first attempt — a draft answer, a function, a plan —
and it ends when the attempt passes its checks or when a stopping rule fires.
Everything between those two points is one of the five patterns below, and
the story of the loop is the story of how they hand work to each other.

### The critic agent: a second pass with a different job

The first pattern is the critic agent, and it exists because the model that
wrote the draft is the worst judge of the draft. The critic is a separate
pass — ideally a separate prompt, a separate model, or best of all a
deterministic check — whose only job is to find fault. Anthropic's
[evaluator–optimiser workflow](https://www.anthropic.com/engineering/building-effective-agents)
is the canonical form of this idea: one language model call generates a
response while another provides evaluation and feedback in a loop, and
Anthropic's guidance is that the pattern fits exactly when evaluation
criteria are clear and iterative refinement provides measurable value. The
same shape appears in [Self-Refine](https://arxiv.org/abs/2303.17651),
where a single model plays author, editor and rewriter in turn, and the
paper reports an average improvement of about 20 percentage points across
seven tasks — though an independent re-analysis of the paper's tables puts
the mean gain closer to 18.7 points, and near zero on mathematical
reasoning, which is itself a useful warning that a critic's value depends
heavily on the task.

What makes a critic useful, as opposed to merely present, comes down to
three properties, and the code in this chapter enforces all three. First,
the critic must be *independent* of the maker: it must judge against
evidence the maker did not produce, which in practice means a test suite, a
schema validator, or a retrieved document, rather than the maker's own
account of what it did. Second, the critic's findings must be *specific* —
not "this is wrong" but "the even-length case returns 3 where the
specification requires 2.5" — because a vague finding cannot guide a
repair, and a repair guided by a vague finding is a random walk. Third, the
critic must return a *verdict*, a binary pass or fail, because the loop
needs a decision to act on, not an essay to interpret. In
`code/critic_agent.py` the critic is a test-suite critic: it executes the
draft against seven tests for a median function, and on the first draft it
fails five of them, naming each failure precisely enough that the revision
passes all seven on the second round. That is the whole pattern in
miniature, and the measured result is worth noting because it is the
chapter's baseline: a first draft that passes 2 of 7 tests becomes a draft
that passes 7 of 7 after one critic-guided revision. The critic did not make
the maker smarter; it gave the maker something true to aim at.

### Traceback analysis: reading the failure instead of fearing it

Once an agent executes code, the most information-dense artefact it will
ever receive is the traceback, and the second pattern is the discipline of
reading it as data rather than as noise. A Python traceback, documented in
the standard library's [`traceback` module](https://docs.python.org/3/library/traceback.html),
is a structured record: a chain of frames, each naming a file, a line
number and a function, ending in an exception type and a message. The
analysis has a small number of steps, and each one removes a different kind
of confusion. First, separate the frames that belong to the programmer's own
code from the frames that belong to the standard library and installed
packages, because the fix almost always lives in the programmer's code and
an agent that edits library internals is an agent that has gone wrong.
Second, find the deepest user frame — the last line of the programmer's own
code that ran before the failure — because that is where the faulty
assumption lives. Third, classify the exception, because different
exceptions mean different kinds of mistake: a `KeyError` is a missing key or
a wrong assumption about the data, an `IndexError` is usually an off-by-one
error, a `NameError` is a misspelling or a missing import, and an
`AttributeError` very often means the object was `None` when the code
assumed it was something else.

The reason this matters for an agent, rather than merely for a human
debugging at a terminal, is that a structured diagnosis changes what the
repair step can do. A language model handed a raw traceback has to do the
parsing itself, in its head, alongside the repair, and it frequently does
neither well — it fixes the wrong frame, or it "fixes" the error by deleting
the failing line. A model handed a diagnosis — exception type, deepest user
frame, source line, likely cause, suggested fix — can spend its effort on
the repair itself. It also changes what the loop can *decide*: a diagnosis
has a stable identity, a failure signature made of the exception type and
the failing location, and that signature is what lets the loop recognise
that it has seen this exact failure before, which is the subject of the
stopping rules below. The parser in `code/traceback_parser.py` implements
exactly this: given the traceback from a checkout function that looks up a
discount key that does not exist, it reports `KeyError: 'discount'` at line
6 of the checkout function, classifies it as a missing-key problem, and
suggests checking whether the key should exist or whether the lookup should
tolerate its absence. That is a small result, but it is the difference
between an agent that reacts to an error and an agent that understands one.

### Retry with context: the failure is part of the prompt

With a diagnosis in hand, the third pattern is the one that gives the
chapter its name: retry with context, which means the next attempt is not
a fresh start but a continuation that carries the failure along with it.
[Reflexion](https://arxiv.org/abs/2303.11366) is the
research form of this idea — after a failed trial the agent writes a verbal
critique of what went wrong, stores it in an episodic memory buffer, and
conditions its next attempt on that critique, with no weight updates at all
— and the reported results show why the idea caught on: 91.0% pass@1 on the
HumanEval code-generation benchmark against 80.1% for GPT-4 alone, and 130
of 134 ALFWorld decision-making tasks completed against roughly 100 for the
underlying ReAct agent. Reflexion also bounds its memory to the most recent
few reflections, usually one to three, which is a quiet acknowledgement of
the same constraint every production loop faces: context is finite, and a
loop that appends every failure to the prompt will eventually drown in its
own history.

The practical form of retry with context is therefore a matter of choosing
*what* to carry forward, and the answer is: the diagnosis, not the raw
trace; the last one or two failures, not the whole history; and the list of
failure signatures already seen, so the generator can be told explicitly
never to repeat a program that has already failed. The repair prompt should
also contain the constraint that matters most for code repair — change only
what the diagnosis names — because a model that rewrites the whole file on
every retry will eventually fix the bug by breaking something else, and the
verifier will then be grading a different program than the one that failed.
This is also where the distinction between a *retry* and a *repair* becomes
important: retrying the same code after a transient failure, such as a
timeout or a dropped connection, is sensible; retrying the same code after a
deterministic failure, such as a `KeyError` on the same line, is not a
strategy at all. The loop in `code/self_healing_loop.py` makes that
distinction explicit by classifying each failure before it decides what to
do next, and it is that classification, more than any cleverness in the
generator, that makes the loop behave like an engineer rather than a slot
machine.

### Compiler-error parsing: let the compiler do the critic's easiest job

The fourth pattern is the narrowest and, for that reason, the easiest to get
right: compiler-error parsing. Before any generated code is executed, it
should be parsed, and if parsing fails the compiler's error is already a
complete diagnosis — it names the file, the line, the column, and the token
that broke, with a caret pointing at the exact spot. A missing colon, an
unclosed bracket, a mixed-indentation block: these are not reasoning
failures and they should never reach a language model as a reasoning
problem, because the compiler has already solved them. The parser in
`code/traceback_parser.py` treats syntax errors as a special case for
exactly this reason: the stack frame in a syntax-error traceback is the
call site of `compile()`, not the broken code, so the diagnosis discards
the frames entirely and reports the compiler's location instead — in the
demo, `SyntaxError: expected ':'` at line 1, column 16 of a function
definition missing its colon. There are two engineering rules hiding inside
that small decision. The first is to parse before executing, always, because
executing a program that only partly parses is how a self-healing loop
corrupts state halfway through a repair. The second is to treat the
compiler as the cheapest critic you will ever have: it is deterministic,
it is free, and it is never wrong about syntax, so every syntax error it
catches is one language-model call you did not have to pay for.

### Self-healing execution: the loop that ties the patterns together

The fifth pattern, self-healing execution, is not a new mechanism but the
assembly of the previous four into a single loop with a verifier at the end
of it — and the verifier is the part that most tutorials skip, which is why
most self-healing demos are less impressive than they look. Execution
succeeding is not the same as the code being correct: a median function
that runs without raising an exception and returns 3 for the input
`[3, 1, 2, 4]` has executed perfectly and is still wrong, because the
answer is 2.5. A loop that stops at "it ran" will ship that function with
confidence, so the loop must end with a check against ground truth — a
test suite, an assertion on the output, a schema validation — and only a
pass from that verifier counts as success. This is also the point where the
research converges: [CRITIC](https://arxiv.org/abs/2305.11738)
shows that self-correction works when the model verifies its output against
an external tool, such as a code interpreter or a search engine, and
consistently improves performance across free-form question answering,
mathematical program synthesis and toxicity reduction, while the Huang
result shows it fails when the only tool is the model's own opinion. The
same lesson appears in retrieval: [Corrective
Retrieval-Augmented Generation](https://arxiv.org/abs/2401.15884) grades
retrieved documents with a lightweight evaluator and falls back to web
search when the grade is poor, reporting improvements of 7.0% on PopQA,
14.9% on the FactScore biography measure and 36.6% on PubHealth — the
retrieval analogue of a critic that checks its evidence before trusting it.

Put together, the loop runs like this: generate a first attempt, parse it,
execute it in a sandbox, diagnose any failure from the traceback, have the
critic judge the output against ground truth, and retry with the diagnosis
in context — and the demonstration in `code/self_healing_loop.py` shows
both endings a loop can honestly have. In the first demo the buggy median
fails the verifier, the loop retries with the failure in context, and the
revised program passes on the second attempt. In the second demo the
generator keeps returning code that raises the same `KeyError`, and the
loop stops after two attempts with a full diagnosis instead of burning its
budget — which brings us to the part of the chapter that matters most in
production, because a loop that can heal itself but cannot stop is worse
than a loop that cannot heal at all.

## The stopping rules

A self-correcting loop without stopping rules is a denial-of-service attack
you launch against your own budget, and the history of coding agents gives
the scale of the risk: the benchmark that made self-healing coding agents
respectable, [SWE-bench](https://arxiv.org/abs/2310.06770), hands the agent
2,294 real GitHub issues across 12 Python repositories and grades it on
whether its patch passes the project's own tests, and at launch the best
model resolved only 1.96% of them. The system that moved that number,
[SWE-agent](https://github.com/SWE-agent/SWE-agent), reached 12.47% with
GPT-4 Turbo not by trying harder but by trying inside a designed interface
with bounded steps, lint rollback and managed history — the loop's
discipline, not its enthusiasm, was the improvement. Your production loop
needs the same discipline, and it comes in four rules that should be
implemented in code, not written in a runbook, because a rule that lives in
a document will be forgotten at 2 a.m. and a rule that lives in code cannot
be.

The first rule is a hard cap on attempts. Pick a number — three is a good
default for code repair, five at most — and make it a constant that the loop
reads, not a suggestion the generator can negotiate. The number should be
small enough that the worst-case cost of one task is predictable: if each
attempt costs a model call plus an execution plus a verification, then the
worst case is that cost multiplied by the cap, and that is a number you can
put in a budget. The second rule is a wall-clock budget for the whole loop,
because a cap on attempts does not bound time when a single attempt can hang
on a slow tool call; the loop in this chapter's code checks the elapsed time
before starting each attempt and stops mid-loop rather than beginning work
it cannot finish. The third rule is exponential backoff between retries,
with a cap and with jitter, so that a loop retrying against a failing
external service does not hammer it in lockstep with every other retrying
client — the delay doubles each time, up to a ceiling, and a small random
spread keeps retries from synchronising. Backoff is the one rule that
protects other people's systems as well as your own, and it is the one most
often skipped because it feels like doing nothing, which is precisely the
point: waiting is the correct action when the failure is transient, and
doing nothing quickly is how you turn a transient failure into an outage.

The fourth rule is the most important and the least obvious: stop when the
loop stops learning. If two attempts fail with the same failure signature —
the same exception type at the same line of the same file — then the
generator is not exploring, it is repeating itself, and a third attempt
with the same input will produce the same failure with high probability.
The loop in this chapter's code implements this by recording every failure
signature it sees and stopping early, with a report, the moment one
repeats; in the demo that rule fires on the second attempt of a three-
attempt budget, saving a call that could not have helped. Alongside it sits
a classification rule: some failures are unrepairable by retrying at all —
a missing dependency, a permission error, an exhausted memory limit — and
those stop the loop immediately, because retrying a `ModuleNotFoundError`
does not install the module. And when any of these rules fires, the loop
must fail *closed*: it returns a diagnosis report containing every attempt,
every failure signature, the final diagnosis and a suggested next step, and
it escalates to a human or to the approval gate built in Chapter 8. Failing
closed is the difference between a system that says "I could not fix this,
here is everything I tried and what I learned" and a system that either
loops forever or silently returns the least-broken draft it produced. The
first is a colleague; the second is a liability.

There is a final property the stopping rules give you, and it is the one
that matters most in a design review: they make the
loop's termination provable. A loop with a bounded number of attempts, a
bounded wall-clock budget, and an early stop on a repeated signature
terminates in all cases, because each iteration either succeeds, consumes
one attempt from a finite budget, consumes time from a finite budget, or
repeats a signature and stops — and the number of distinct signatures is
itself finite for a fixed program. That is a termination argument you can
state in one paragraph and defend against a sceptical reviewer, and it is
worth having, because "it usually stops" is not an argument at all.

## When the loop itself goes wrong: runaway loops, and watching the watcher

The stopping rules above bound the loop from the outside — a cap on
attempts, a cap on time, a stop on a repeated signature — and for a
single-agent loop repairing one function, that is usually enough. But
once a loop grows a second agent, a longer horizon, or a task with no
sharp verifier, the failure changes shape: the loop does not crash, it
does not repeat one signature, and it does not run past its budget in
any obvious way. It keeps working, keeps spending, and keeps producing
new text, while getting nowhere. The budget still catches it in the
end, because the budget always catches everything in the end, but
catching a runaway loop on the last attempt is like catching a fire at
the property line: technically a success, and in every way that matters
a failure. What you need, on top of the budget, is something that
watches the *trajectory* — whether each attempt is taking the loop
somewhere new — and that is a different job from judging whether the
latest output is good. The critic judges the output; the watchdog
judges the journey. Confusing the two is the first mistake, because a
loop whose every draft is plausible and whose every step is different
can still be lost, and a critic that passes each draft in isolation
will never notice.

### A taxonomy of runaway loops: five ways to travel without arriving

Runaway loops do not all fail the same way, and naming them apart
matters because each one has a different tell and a different fix, so
a detector tuned for one will walk straight past another. The first
is the **retry spiral**, the simplest and the most familiar: the same
attempt fails the same way, the loop retries it anyway, and each retry
costs a full generation call while teaching the loop nothing. Its
mechanism is not stubbornness in the model but amnesia in the loop —
nothing in the prompt records that this exact attempt already failed —
and its tell is the repeated failure signature, which is why the
healing loop already stops on one. The second is **ping-pong
delegation**, which only exists once there are two agents to bounce
between: agent A hands the task to agent B because it lacks some
context, B hands it back because it lacks a different piece, and
neither one ever asks the user, or a tool, for the missing piece. Its
mechanism is a missing owner — every handoff feels like progress to
the agent making it, because delegating *is* doing something — and its
tell is the actor sequence, not the text: planner, researcher,
planner, researcher, with the task itself unchanged underneath. The
third is **oscillation**, which the design-review angle above already
names: attempt one fails with error A, attempt two fixes A and
introduces error B, attempt three fixes B and reintroduces A, and the
loop travels in a circle. Its mechanism is a repair that is too local
— each fix sees only the latest error and not the history of fixes —
and its tell is the return to a state from two attempts ago, an A-B-A
pattern in the state history that a signature counter alone will miss
if the two errors have different signatures. The fourth is **goal
drift**: the loop is still making progress, but towards a different
task than the one it was given, because each repair quietly
reinterprets the goal to make the current draft pass — the support
agent that starts by refunding an order and ends by explaining the
refund policy, or the coding agent that starts by fixing a function
and ends by deleting the feature the function served. Its mechanism is
a verifier that grades the draft against the draft's own framing
rather than the original task, and its tell is a relevance score
against the original task that falls while the evaluator score rises,
which is why the two scores must be tracked separately and never
allowed to collapse into one number. And the fifth is
**amplification**: each attempt is longer than the last, because each
one carries the whole history of previous attempts, plus its own
apology, plus a new plan, so the context grows faster than the answer
and the loop spends more tokens remembering what it tried than trying
anything new. Its mechanism is unbounded history, the same pressure
Reflexion answers by capping its memory at one to three reflections,
and its tell is output length growing while the evaluator score stays
flat — length alone proves nothing, because a converging answer also
grows as it gets more complete, and it is the combination, growth
without gain, that gives the game away.

### Detection: counters, hashes, and the watchdog agent

Detecting these five needs four instruments, and they build on each
other from cheap to expensive, so a production loop should run them
in that order and spend the dearer ones only where the cheaper ones
are blind. The cheapest is **counters**: how many attempts, how many
seconds, how many tokens, how many times each signature has appeared.
Counters catch the retry spiral and nothing else, but they cost
nothing and they never lie, which is why they are the first line. The
second is **state hashing**: normalise each attempt's output — lower
the case, collapse the whitespace — and hash it, so that two outputs
that differ only in formatting hash identically and the loop can be
caught reproducing an earlier state even when the text is not
byte-identical. A hash is also small, which matters on a long loop:
storing a hash per attempt instead of the full text keeps the
watchdog's own memory from becoming the amplification it is meant to
detect. The third is **semantic repetition**: comparing outputs for
meaning rather than characters, so that the same wrong answer in
different words is recognised as the same answer. In production this
is cosine similarity over embeddings, and the code in this chapter
implements a cheap proxy instead — token overlap plus a normalised
edit distance — labelled honestly as a proxy, because a proxy
over-scores outputs that share boilerplate while differing on the one
load-bearing sentence, and under-scores paraphrases that share almost
no words, and a detector that does not know its own blind spots will
be trusted exactly where it should be doubted. And the fourth is
**progress metrics**: the evaluator score per attempt, tracked as a
best-so-far with a gain floor, so that a score that wobbles up a
little and down a little is recognised for what it is — the best score
never actually moves — rather than mistaken for progress because the
latest number went up.

Those four instruments belong to a **watchdog agent**, and the
distinction from the critic is worth stating precisely, because the
two are easy to merge into one mushy "reviewer" and lose both jobs at
once. The critic is a judge of output: it takes one draft and one
specification and returns a verdict on that draft. The watchdog is an
observer of trajectory: it takes the whole sequence of attempts and
returns a verdict on the loop — continue, warn, or stop — with reasons.
It never rewrites output, it never judges quality, and it never needs
to understand the task, which is exactly why it can be small,
deterministic, and cheap: it reads counters, hashes, similarities and
scores, and it stops the loop when the trajectory says the loop is
lost, even when the latest draft looks fine. In the code the watchdog
is `code/loop_watchdog.py`, and its demo shows the distinction in
miniature: a converging loop whose evaluator score climbs from 0.40
to 0.95 across four genuinely different drafts is let through with
four continue verdicts, an oscillating loop is stopped on its third
attempt when it returns to its first state, and a ping-pong delegation
is stopped on its fifth attempt when the actor sequence completes its
second full planner–researcher cycle. Those scores are a worked
example from the demo, not a measured result, but the shape is the
point: the watchdog's job is to let the first loop finish and to kill
the other two early, and it does both without ever reading a draft for
quality.

### Convergence versus stagnation: the question the score alone cannot answer

The hardest call the watchdog makes is also the most useful one in a
design review, because it is the call a budget cannot make: telling
a loop that is converging from a loop that is merely changing. The
two look identical if you only watch the latest score, because both
produce new text and both occasionally post a slightly better number,
and they separate only when you watch three things together. A
converging loop shows its evaluator score rising *and* its changes
shrinking — the embedding similarity between successive drafts climbs
towards one, the edit distance between them falls, and the score
improves by a margin that clears the gain floor, so each attempt is a
smaller step in the same direction. A stagnating loop shows the
opposite pattern on at least two of the three: the drafts keep
changing — the edit distance stays large, the wording is fresh every
time — but the best score does not move, or it moves by less than the
gain floor and then falls back, so the loop is exploring widely and
arriving nowhere. The tell, in one sentence, is that convergence is
change that narrows while the score climbs, and stagnation is change
that stays wide while the score stays flat, and the watchdog
implements exactly that: it tracks the best score with a gain floor
of 0.02 over a window of three attempts in the worked example, so a
score that wobbles 0.50, 0.51, 0.50, 0.51 never sets a new best and
the loop is stopped for stagnation, while a score that climbs 0.40,
0.65, 0.85, 0.95 clears the floor every time and the loop is allowed
to finish. Two cautions belong with that rule, because both are easy
to get wrong in production. The first is that a flat score is not
always stagnation: a loop can spend three attempts restructuring its
approach before the score moves at all, which is why the stagnation
window must be long enough to survive a genuine rethink and short
enough to catch a loop that is only rewording — three is a worked
starting point, not a law, and the right number is the one your own
logs justify. The second is that the score itself can be gamed by
drift: a loop whose evaluator score rises while its relevance to the
original task falls is not converging, it is drifting towards an
easier task, and only a separate relevance check catches that, which
is why the watchdog tracks relevance on its own line and warns when
it falls even as the score climbs.

One more anchor against drift comes from the harness rather than the watchdog: the feature list in chapter 1. A feature list is a machine-readable file in which each feature is a triple — a behaviour description, a verification command, and a current state — and the state moves to passing only when the verification command succeeds. Drift is a loop reinterpreting its goal, and a goal written this way cannot be reinterpreted: the loop can reword its drafts, but it cannot reword the behaviour the verification command checks. So when the watchdog warns that relevance is falling, the first question is whether the loop is still working from the same feature list, and if the list changed, who changed it.

### Two planning patterns that change the failure surface: ReWOO and tree search

So far this chapter's loop has been a single line — generate, check,
repair, repeat — and the runaway taxonomy above is the failure surface
of that line. Two named planning patterns change the surface by changing the shape of the plan — from the single line to a whole plan written up front, or to a tree of branches that backtracks. Each one trades one failure mode for another, and an advanced-level answer names the trade rather than just the pattern. The first is **ReWOO**, short for
Reasoning WithOut Observation, published in 2023: instead
of interleaving reasoning and tool calls, the planner writes the whole
plan up front — every tool call, with placeholders for results not yet
seen — then a worker executes the plan in one pass, then a solver
synthesises the answer from the results. The grounded number is the
paper's own: on the HotpotQA multi-step reasoning benchmark ReWOO
reached about five times the token efficiency of the interleaved
ReAct pattern with a 4% accuracy improvement, because the reasoning
was done once instead of being re-done, with the whole growing
history attached, at every step. The failure surface moves with the
shape: a ReWOO loop cannot spiral, because there is no loop for it to
spiral in, and it cannot ping-pong, because the planner hands over
once and never takes the task back — but it inherits a failure the
retry loop does not have, which is a plan that is wrong from the start
and has no observation anywhere in it that could correct it, so a
task whose tool calls cannot be known in advance, one that branches
on what the first result says, is simply the wrong shape for ReWOO
and will fail cleanly and completely rather than gradually. The
second pattern is **tree search**, in its language-model form the
Tree of Thoughts, published in 2023: instead of one chain
of thought, the model branches, generating several candidate thoughts
at each step, evaluating which partial states look promising, and
backtracking from dead ends, by breadth-first or depth-first search.
The grounded number is again the paper's own, and it is a startling
one: on the Game of 24 puzzle, GPT-4 with ordinary chain-of-thought
prompting solved 4% of tasks, while Tree of Thoughts reached 74%, by
holding several possible futures at once instead of committing to the
first one. The failure surface moves in the opposite direction from
ReWOO's: tree search is the pattern that *contains* oscillation
rather than suffering it, because backtracking is oscillation made
deliberate — returning to an earlier state is a designed move, not a
bug — but it pays for that containment in branching cost, since every
branch is a generation call and every evaluation is another one, and
it inherits a new failure of its own, which is a bad evaluator: if the
model that judges which branches look promising shares the
generator's blind spot, the search prunes the right branch and
explores the wrong subtree thoroughly, which is the critic's blind
spot wearing a search costume. The design lesson that ties the two
together is that the shape of the plan decides which runaway is
possible, so the watchdog must be told the shape: a ReWOO loop needs
a plan-validity check before execution, because there is no later
observation to catch a bad plan, while a tree search needs its
watchdog to watch the *frontier* — how many branches are alive, and
whether the best score across branches is moving — rather than any
single path, because a single path in a tree is supposed to die.

### Outcome versus process reward models, and buying accuracy at inference time

Underneath both patterns sits a question the chapter has been
answering implicitly since the critic — what, exactly, is the reward
that says an attempt is good — and it is worth making explicit now,
because the answer decides what the watchdog can even measure. An
**outcome reward model** scores only the final answer: did the
program pass the tests, did the refund match the policy, did the
answer match the key. It is cheap, it is unambiguous where ground
truth exists, and it is blind to how the answer was reached, so a
loop guided only by outcome rewards can converge on a right answer
for the wrong reason and will keep trying approaches that are one
lucky guess away from passing. A **process reward model** scores each
intermediate step: is this reasoning step correct, is this tool call
the right one, is this partial state on a path to a good answer. It
is expensive, because it needs step-level labels — the process-supervision paper released PRM800K, 800,000
step-level human labels, to train one — but the grounded number
justifies the cost where it matters: on a representative subset of
the MATH dataset, the process-supervised verifier solved 78.2% of
problems under best-of-N search against 72.4% for the strongest
outcome-supervised verifier, because catching a wrong step early is
worth more than grading a wrong answer late. For an agent loop the
practical reading is that the critic you already have is an outcome
reward model, the watchdog's progress metric is an outcome metric
too, and a process reward is what you reach for when the loop's
failures are *step* failures — a planner that picks the wrong tool
on step two of five — rather than answer failures, and only when you
can afford the labels or a verifier cheap enough to stand in for
them, such as a test that runs after each step rather than only at
the end.

That same cost logic governs the last idea in this section,
**inference-time scaling**: spending more compute per query, after
training is finished, to buy accuracy without retraining the model.
There are two broad ways to spend it, and they cost differently.
**Parallel sampling**, in its simplest form self-consistency, samples
many independent reasoning paths at a non-zero temperature and takes
the majority answer: with PaLM
540B, 40 sampled chains lifted GSM8K grade-school mathematics from
56.5% with greedy chain-of-thought to 74.4%, a gain of 17.9
percentage points, with no new training and no new model — but the
cost is linear in the number of samples, so 40 samples cost roughly
40 times a single answer, and the method only helps where the correct
answer is the one most paths converge on, which is true for
mathematics with a single right answer and false for open-ended
writing, where there is no majority to converge on. **Verifier-guided
search** spends the same budget differently: generate candidates, score
them with a verifier — ideally a process reward model — and search
towards the best-scored branch, which is Tree of Thoughts with a
learned evaluator rather than the model's own judgement. The trade-off was made systematic in 2024, showing that a
compute-optimal allocation — choosing the method and the budget per
question based on its difficulty — was more than four times as
efficient as naive best-of-N sampling, and that where a small model
already had a non-trivial chance of solving a question, extra
test-time compute could let it outperform a model fourteen times
larger, while on the hardest questions, where the small model's
success rate was close to zero, extra compute added almost nothing
and more pre-training was the better investment. The advanced-level
reading of that result is a budget rule you can state in one
sentence: spend inference-time compute where the model is already
close, because it amplifies capability that is present and does not
create capability that is absent — and spend it adaptively, easy
questions cheap and hard questions dear, because a flat budget
wastes most of itself on questions that were already solved and
starves the ones that were not. And the watchdog is what enforces
that rule at runtime: it is the component that notices when the extra
samples have stopped buying accuracy — the best score flat across a
window, the drafts merely rewording — and stops the spending, which
is the difference between inference-time scaling as a strategy and
inference-time scaling as a more expensive way to loop forever.

### The advanced-level design-review angle, extended

If the earlier design-review angle was about the loop's cost and its
breaks, this section adds the question the reviewer asks next,
which is usually some version of: "your agent has been running for
ten minutes and produced nothing usable — what do you look at first?"
Answer with the taxonomy, not with a shrug: name which of the five
runaways the trajectory matches, say what its tell is, and say which
instrument caught it, because an engineer who can say "the actor
sequence is planner, researcher, planner, researcher, so this is
ping-pong delegation, and the fix is a single owner plus a rule that
a handoff must carry the missing context or go to the user" is
answering a different and better question than one who says "I would
increase the retry budget". Then say what you would *not* do, because
the refusals are what make the rest credible: you would not fix a
runaway loop by raising the attempt cap, because the cap is what the
runaway is already consuming; you would not fix oscillation by
retrying harder, because oscillation *is* retrying, and the fix is a
state history the repair can see, or a tree search that makes the
backtrack deliberate; and you would not buy your way out with
parallel sampling, because sampling forty paths of a loop that is
drifting just gives you forty confident drifts, and the money is
better spent on the verifier that would have caught the drift on the
first path. Finish, if the question allows it, with the watchdog's
own job description in one line — a separate observer that judges the
trajectory, not the output, and stops the loop when the trajectory
says it is lost — because that sentence is the whole section in
miniature, and it is the sentence a reviewer remembers.

### Explain it back

Before moving on, explain this section back in your own words, to a
sceptical reviewer who believes a hard attempt cap already solves
every runaway: walk them through one loop that the cap catches too
late and the watchdog catches early — the oscillating median repair
is a good one, since it burns its whole budget travelling between
two errors — and show them, from the watchdog's verdict reasons,
which signal fired and why the cap alone could not have fired it.
Then explain the difference between convergence and stagnation twice:
once with the three measurements — evaluator score, embedding
similarity between successive drafts, edit distance — and once
without any numbers at all, in plain words a non-technical reviewer
could follow. If you cannot do the second version, you do not yet
understand the first.

## The advanced-level design-review angle

If you are asked about self-correction in a design review, the
reviewer is rarely testing whether you know the pattern names; they are
testing whether you understand what the loop costs and where it breaks, so
answer in those terms. Start with the signal: say plainly that intrinsic
self-correction does not work, cite the Huang result, and say that every
correction in your design is anchored to an external signal — a test suite,
a compiler, a traceback, a retrieval grade — because that single sentence
separates a design grounded in evidence from one grounded in optimism. Then
talk about the loop as an economic object: each round costs a generation
call, a critic call, an execution and a verification, so the cost of a task
is the per-round cost multiplied by the number of rounds, and the number of
rounds is the thing you control with the budget. An engineer who can say
"three rounds at roughly this cost each, so the worst case is this, and the
loop stops early on a repeated signature" is answering a different and
better question than one who says "the agent retries until it works".

Then name the failure modes, because the reviewer will be waiting for
them. The first is the critic that shares the maker's blind spot: if the
critic is the same model with the same prompt, it will approve the same
mistakes, which is why the critic in this chapter runs tests rather than
reading the draft. The second is the loop that fixes the symptom and breaks
the invariant — the model that silences a `KeyError` by deleting the lookup
instead of handling the missing key — which is why the verifier must check
the output against a specification, not merely check that the code ran. The
third is the loop that oscillates: attempt one fails with error A, attempt
two fixes A and introduces error B, attempt three fixes B and reintroduces
A, and the loop burns its whole budget travelling in a circle. The defence
is the signature history: keep every failure signature, and stop when the
set stops growing. The fourth is context exhaustion: a loop that appends
every traceback to the prompt will eventually spend more tokens on its own
history than on the task, which is why Reflexion caps its memory at one to
three reflections and why this chapter's loop carries the diagnosis, not the
raw trace. And the fifth is the quietest and most expensive: a loop that
succeeds slowly, passing on the fifth attempt after four failures, and
teaching the team that the system "works" while it burns five times the
necessary cost on every task. That failure mode is only visible in
measurement, which is why Chapter 10 grades trajectories: if you cannot see
how many rounds the average task takes, you cannot know whether your loop
is healing or merely expensive.

Finish, if the question allows it, by saying what you would *not* build.
You would not put a self-correction loop around a task with no ground
truth, because there is nothing for the critic to check and the loop will
optimise for the model's own confidence. You would not let a loop retry a
write operation — a payment, a deployment, a database mutation — without
an idempotency key or a human approval gate, because a retry that succeeds
twice is worse than a retry that fails. And you would not ship a loop whose
termination you cannot prove, because an agent that cannot stop is an agent
you cannot operate. Those three refusals, stated plainly, are what make the
rest of the design credible.

## What the code does

The `code/` directory holds four standard-library-only Python modules,
each runnable with no model and no application programming interface (API) key, because the loop's logic — not
the model call — is what this chapter teaches. `traceback_parser.py` turns
a raw traceback into a structured diagnosis with a failure signature;
`critic_agent.py` runs a maker–checker loop in which a test-suite critic
catches five failures in a first draft and passes the revision on round
two, `self_healing_loop.py` assembles the full loop with a hard retry
budget, exponential backoff, repeat-signature detection and a fail-closed
report; and `loop_watchdog.py` adds the separate observer from the
runaway-loops section above — state hashing, a token-overlap and
edit-distance proxy for semantic repetition (labelled as a proxy, not
a real embedding), evaluator-score progress tracking, and a
continue / warn / stop verdict with recorded reasons. The generators in the last three modules are scripted stand-ins with
a fixed list of candidate programs, and the `TODO` comments mark exactly
where a real model call plugs in — the interface is `generate(task,
history) -> str` for the healer and `revise(specification, findings) -> str`
for the maker, and nothing else in the loop changes when you make the swap.
For the watchdog the swap points are the semantic-similarity function
(real embeddings in place of the proxy) and the evaluator scores (the
verifier's or critic's own scores, passed in per attempt). Run each module directly to see its demo, and note the measured results
rather than taking them on trust: the critic moves a median function from
2 of 7 tests passing to 7 of 7 in one revision round, the healing loop
repairs the same function in 2 attempts, and the unfixable case fails
closed after 2 attempts — stopping early on a repeated signature instead of
spending its budget of 3. The watchdog's demo shows the same discipline
one level up: a converging loop whose worked-example score climbs from
0.40 to 0.95 is let through with four continue verdicts, an oscillating
loop is stopped on its third attempt, and a ping-pong delegation is
stopped on its fifth attempt after two full delegation cycles. The exercises then ask you to rebuild each piece
yourself, with the model plugged in for real, and to prove the termination
argument for a loop of your own design.

## Provenance

The metacognition framing of this chapter — planning, self-correction and
corrective retrieval — is adapted from Lesson 9 of Microsoft's [AI Agents
for Beginners](https://github.com/microsoft/ai-agents-for-beginners) (MIT
licence), with attribution in `references.md`. The evaluator–optimiser
pattern is cited from Anthropic's [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents).
The named patterns in this chapter — critic agents, traceback analysis,
retry with context, compiler-error parsing and self-healing execution, with
explicit stopping rules — are original writing: no course the research for
this course surveyed teaches them as named patterns. The full provenance
record is in the repository's [CONVERSION_PLAN.md](../CONVERSION_PLAN.md).
