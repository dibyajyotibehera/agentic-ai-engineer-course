# Chapter 9 — Exercises

Work in `code/`, in Python, with the standard library only unless an
exercise says otherwise. Every exercise names its difficulty —
**Foundation**, **Intermediate** or **Advanced** — and its acceptance
criteria, so you know when you are done. The model-backed exercises need a
language-model application programming interface (API) key; where a full solution needs one, the exercise says
so. The chapter's code already wires in a real model for the maker and
the generator — there are no scripted stand-ins left in the shipped
modules, only test doubles in `test_critic_and_healing.py` — so run
the demos first and build on the real calls, rather than on a
pretend model.

## Exercise 1 — Read a traceback like a diagnosis (Foundation)

Take the five traceback snippets below (save them as text files or paste
them into a script) and, for each one, write a diagnosis in the shape
`TracebackDiagnosis` produces: exception type, deepest user frame, likely
cause, and suggested fix. Then check your answers by feeding the same text
to `traceback_parser.parse_traceback` and comparing.

1. `KeyError: 'user_id'` raised at line 14 of `load_profile` in `app.py`,
   inside a call chain that passes through `json.load` and `requests`.
2. `IndexError: list index out of range` at line 31 of `paginate` in
   `paging.py`, on the line `page = items[offset + page_size]`.
3. `AttributeError: 'NoneType' object has no attribute 'email'` at line 8
   of `notify` in `notify.py`.
4. `TypeError: unsupported operand type(s) for +: 'int' and 'str'` at line
   22 of `total` in `billing.py`.
5. `ModuleNotFoundError: No module named 'yaml'` at line 1 of `config.py`.

**Acceptance criteria:** all five diagnoses name the correct exception
type and the correct deepest user frame; at least four of the five likely
causes match the parser's classification, and for any that differ you can
explain why your reading is better. The deliverable is a short write-up,
not code — the point is to learn what a traceback actually says before you
automate reading it.

## Exercise 2 — The critic catches what the first pass missed (Intermediate)

Build a maker–checker loop for a function of your own choosing — `parse_date`
(a string such as `"2026-11-18"` into a `datetime.date`, raising
`ValueError` on anything else) is a good pick because it has sharp edges:
leap years, single-digit months, trailing whitespace, and non-date strings.
Write the specification first, then a test suite of at least ten cases
covering the edges, then a maker whose first draft contains exactly one
seeded bug (for example, accepting `"2026-2-30"` or failing to strip
whitespace). Run the loop from `code/critic_agent.py`, adapted to your
specification and tests, with the real model maker (`LLMMaker`),
starting from your seeded buggy draft as `first_draft`.

**Acceptance criteria:** the round-1 critique is FAIL and names the seeded
bug by test name; the round-2 critique is PASS with all tests green; the
critic's findings quote the failing test and the required behaviour, never
just "this is wrong"; the loop runs for at most three rounds. Stretch goal:
let the maker generate the first draft from scratch (no seeded bug) and
compare the rounds and the token cost — and if the model fixes the bug in
a way your tests do not cover, add a test for it.

## Exercise 3 — Traceback analysis: repair from the trace alone (Intermediate)

Using `code/traceback_parser.py` as your parser, build a repair loop that
takes a buggy program and a traceback and returns a repaired program,
without a language model: implement three deterministic repair rules of
your own choosing, driven purely by the parsed diagnosis. Suggested rules:
a `NameError` for a name that differs by one character from a defined name
is fixed by correcting the spelling; a `KeyError` on a dictionary literal
is fixed by adding the missing key with a sensible default; an `IndexError`
on `xs[len(xs)]` is fixed by subtracting one. Seed three buggy programs,
one per rule, run each through the loop, and verify the repaired program
against a test you wrote before the repair.

**Acceptance criteria:** all three seeded bugs are repaired by the matching
rule with no human intervention; each repair changes only the lines the
diagnosis names (check this by diffing the program before and after); a
fourth seeded bug that matches no rule fails closed with the diagnosis
report instead of being "repaired" by a guess. Write down, in a paragraph,
where deterministic repair stops being useful and a language model becomes
necessary — that boundary is the honest limit of this exercise.

## Exercise 4 — The self-healing loop with a hard budget (Advanced)

Rebuild `code/self_healing_loop.py` for a task of your own — a small
command-line utility, a data-cleaning script, or a function with a
specification and a verifier you write — reusing the real model
generator (`LLMGenerator`) already wired into the chapter's code.
This exercise needs a model API key. Keep the loop's structure exactly as written (parse before executing, classify each
failure, verify the output, never just the exit code) and set the budget to
three attempts, a 60-second wall-clock limit, and exponential backoff
starting at one second.

**Acceptance criteria:** on a task the model can solve, the loop succeeds
in at most three attempts and the report shows the attempt history; on a
task you deliberately make unsolvable (for example, a specification that
contradicts itself, or a function that must import a module that does not
exist), the loop fails closed — it never exceeds three attempts, never runs
past 60 seconds, stops early if the same failure signature repeats, and
returns a report containing every attempt, every failure signature, the
final diagnosis and a suggested next step; the unrepairable case (the
missing module) stops on the first attempt, not the third. Log the cost of
each run — model calls, tokens if your provider reports them, wall-clock
time — and report the worst-case cost of one task as a number.

## Exercise 5 — Compiler-error parsing: fix it from the caret (Intermediate)

Write a repairer that works only from compiler output: given a Python
source file and the `SyntaxError` the compiler raises for it, parse the
error's filename, line number and column offset and apply a fix. Cover at
least these three cases: a missing colon at the end of a `def` or `if`
line, an unclosed bracket (the compiler reports the opening line, not the
line where the bracket should close — your repairer must handle that
misdirection), and inconsistent indentation inside a block. Use `ast.parse`
to verify the repair before the program is ever executed.

**Acceptance criteria:** all three seeded syntax errors are repaired and
the repaired file parses cleanly; the bracket case is fixed at the correct
line, not the line the error message names; a fourth file with a syntax
error your repairer does not cover is reported as unrepairable with the
compiler's location quoted, and is never executed. Bonus: feed the same
three files to a language model with only the compiler error as context and
compare its repairs with yours — where did the model beat the deterministic
rules, and where did it do worse?

## Exercise 6 — Prove your loop terminates, then price its failure modes (Advanced)

Design, on paper, a self-correction loop for a production agent you have
actually operated or built — a support agent, a coding agent, a document
agent — and write it up as a short design document (two to three pages).
The document must contain: the external signals your critic checks against
(test suite, schema, retrieved document, compiler — and why each one is
ground truth rather than the model's opinion); the four stopping rules from
the lesson with concrete numbers for your system (attempt cap, wall-clock
budget, backoff parameters, repeat-signature rule); a termination argument
in the form the lesson gives — each iteration consumes a finite budget or
stops — applied to your loop; and a failure-mode table with at least five
entries (critic shares the maker's blind spot; symptom fixed, invariant
broken; oscillation between two errors; context exhaustion; slow success
that looks like success but costs five times the single-pass price), each
with a detection method and a mitigation.

**Acceptance criteria:** the termination argument holds for every path
through your loop, including the verifier-rejects path and the transient-
failure path — a reviewer can follow it without taking anything on trust;
every failure mode has a detection method you could implement (a metric, a
log line, an alert) rather than a hope; the worst-case cost of one task is
stated as a number with its working shown; and the document names at least
one task your loop must refuse to correct (no ground truth, or a
non-idempotent write) and says what happens instead.

## Exercise 7 — Run the watchdog, then break it (Intermediate)

Run `python code/loop_watchdog.py` and read all three demos: the
converging loop the watchdog lets finish, the oscillating loop it stops
on attempt 3, and the ping-pong delegation it stops on attempt 5.
Then break it, three times, by editing a copy of the demo data (not the
module): first, make the converging loop's scores flat (0.50 on every
attempt) while keeping the drafts genuinely different, and record what
the watchdog says and on which attempt; second, make the oscillating
loop's two drafts differ by only one word each round, and record
whether the semantic-repetition proxy catches it or the state hash
does; third, add a third agent to the ping-pong demo so the actor
sequence reads planner, researcher, verifier, planner, researcher,
verifier, and record whether the watchdog still stops it — and if it
does not, explain why, and what detector you would add for a
three-agent cycle.

**Acceptance criteria:** for each of the three breaks you can state
which watchdog signal fired (or did not fire), on which attempt, and
why, quoting the verdict reasons; the three-agent case includes a
written detector design (what state it tracks, what pattern it looks
for) even if you do not implement it; and you can say, for each break,
whether the loop was genuinely runaway or whether the watchdog was
being fooled — a detector that cannot tell those apart is not a
detector you can trust.

## Exercise 8 — Convergence versus stagnation, explained to a sceptic (Intermediate)

Take a real trajectory from your own work — a coding agent's repair
attempts, a support agent's drafts, or the median-function loop from
Exercise 2 — and build a table with one row per attempt: the draft
(or a one-line summary of it), the evaluator score, the similarity to
the previous draft (use the watchdog's proxy, or real embeddings if
you have them), and the edit distance to the previous draft. Then
write the explain-back: "explain the difference between convergence
and stagnation to a sceptical reviewer". The reviewer believes a hard
attempt cap already solves every runaway and that a rising score on
the latest attempt proves progress. Your explanation must do two
things: show them, from your table, one loop the cap catches too late
and the watchdog catches early; and explain convergence versus
stagnation twice — once with the three measurements (evaluator score,
similarity between successive drafts, edit distance), and once
without any numbers at all, in plain words a non-technical reviewer
could follow.

**Acceptance criteria:** the table has at least four attempts with
real scores and real similarities (not invented numbers); the
numbered explanation names which of the three measurements separates
the two cases in your trajectory and which one would have misled you
alone; the plain-words explanation stands on its own — a reviewer who
reads only that version can still say, for a new trajectory, whether
it is converging or stagnating; and the piece answers the sceptic's
cap argument directly, with the attempt number on which each
mechanism would have stopped the loop.

## Exercise 9 — ReWOO versus retry: change the shape, change the failure (Advanced)

Rebuild a small multi-step agent task twice — a research task with
three tool calls is a good pick, for example "find the refund policy
for an order, check whether the order is inside the return window,
and draft the reply" — once as a plain retry loop in the style of
`code/self_healing_loop.py`, and once as a ReWOO-style plan-then-
execute loop: a planner writes the whole plan up front with
placeholders for results not yet seen, a worker executes it in one
pass, and a solver synthesises the answer. Use the real model calls,
following the chapter's `LLMGenerator` pattern, rather than scripted
stand-ins. Then, for each version, seed
the same fault — the first tool call returns an error, or a result
that changes what the second call should be — and record what
happens: which version recovers, which version fails, how many model
calls each one spends, and which runaway from the chapter's taxonomy
each version is vulnerable to.

**Acceptance criteria:** both versions run against a real model; the seeded fault is identical in both; the write-up states the
call counts and the outcome for each version, names the runaway each
version is vulnerable to (and which one each version cannot suffer,
and why), and says, in one paragraph, for which class of task you
would choose each shape — a task whose tool calls can be known in
advance versus one that branches on intermediate results. Stretch
goal: add a tree-search version that branches at the second step and
backtracks when a branch fails, and state what its branching cost
bought you.
