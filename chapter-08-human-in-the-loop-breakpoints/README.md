# Chapter 8 — Human-in-the-Loop & Breakpoints

Every agent you ship will eventually reach a step it should not take
alone. The step is usually small — publishing a page, merging a
change, issuing a refund, deleting a record — and the reason it
should not be taken alone is never that the model is stupid. It is
that the step is irreversible, or expensive, or attributable to a
person who will have to answer for it afterwards, and none of those
three properties is something a language model can underwrite. So
sooner or later the loop has to stop, hand the decision to a human,
and wait. This chapter is about building that stop properly, because
a stop that only works while the process is alive, or that can be
answered by anyone who finds the endpoint, or that leaves no record
of who said yes, is not a control at all — it is a pause button wired
to nothing.

The shape of the problem is easiest to see in the loop you already
own. The example this course was built around is a hand-built loop for a stock-price agent
with a human-approval gate on its write tool, alongside a bounded
step loop, a hard token and cost budget, deterministic stops, a
replay log, bounded error recovery, and the requirement that the loop
itself owns the stopping conditions rather than leaving them to the
model. That gate is the subject of this chapter, and everything here
is written so it maps back onto it: the gate sits in the loop, not in
the prompt, and the rest of the chapter is the engineering that makes
the gate hold when the process restarts, when the approver is asleep,
and when somebody asks, six months later, who approved the write.

## The idea in plain words

A human-in-the-loop system is an agent that can be interrupted. That
sentence sounds trivial until you ask what "interrupted" has to mean
for it to be worth anything, and the answer turns out to have four
parts that all have to be true at once. First, the agent must be able
to stop *before* the consequential action rather than after it, which
means the pause has to be placed in code the model cannot route
around. Second, the stop must survive the process that is doing the
stopping, because approvals take minutes or hours and processes
restart, deploy, and crash on exactly that timescale. Third, the
question has to reach the right human where that human already works
— a chat message, a webhook, a queue — carrying enough context for a
real decision and carrying it in a form the human can answer with one
click. And fourth, the answer has to be recorded in a way that
outlives the run itself, so that the question "who approved this, and
what exactly did they approve?" always has an answer that does not
depend on anyone's memory.

Notice what is missing from that list: the model. Nothing in those
four parts asks the model to be careful, or to remember to ask, or to
judge whether an action is risky. That omission is the whole design
principle, and it is the one Microsoft's course states most plainly
in its lesson on building trustworthy agents, where the human-in-the-loop
pattern is presented as users acting as agents in a multi-agent
system, approving or terminating a running process
([Lesson 6: Building Trustworthy Agents](https://microsoft.github.io/ai-agents-for-beginners/06-building-trustworthy-agents/)).
Anthropic makes the same point from the other direction in its guide
to building effective agents: agents should pause for human feedback
at checkpoints or when they encounter blockers, and the stopping
conditions belong to the system around the model, not to the model's
own judgement
([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).
If the only thing standing between the model and a destructive tool
is a sentence in the system prompt telling it to ask first, then you
do not have a gate — you have a request, and requests are exactly
what a model under pressure, or a model reading hostile text in a
tool result, will forget. The gate has to live underneath the model,
in code the model cannot see or edit, and the rest of this chapter is
about what that code has to do.

## The mechanism

### Interrupts and breakpoints: where the loop stops

The vocabulary here comes from LangGraph, whose documentation is the
clearest statement of the two kinds of pause, and it is worth
learning the distinction precisely because the two kinds solve
different problems
([Interrupts — LangGraph documentation](https://docs.langchain.com/oss/python/langgraph/interrupts)).
A *static breakpoint* is declared when the graph is compiled: pause
before this node runs, or after that node runs. It is a property of
the graph's shape, it does not depend on anything the model decided,
and it is the right tool for debugging and for gates whose position
never changes — always pause before the node that writes to
production, no matter what the run is doing. A *dynamic interrupt*,
by contrast, is a call to `interrupt()` placed anywhere inside a
node's code, and it fires only when the code reaches it, which means
it can be conditional on the run's own state: pause here only if the
write touches more than a hundred rows, or only if the amount exceeds
a threshold, or only if the tool call the model just produced names a
file outside the allow-list. LangChain Academy's free course
*Introduction to LangGraph* teaches exactly this pair — static
breakpoints, editing state for human feedback, and dynamic breakpoints
— as the whole of its third module
([Introduction to LangGraph — LangChain Academy](https://academy.langchain.com/courses/intro-to-langgraph)),
and the course's own syllabus is the reason this chapter treats
LangGraph as syllabus inspiration rather than as a dependency: the
ideas are portable, and the code in this chapter implements them in
framework-neutral Python so you can see the mechanism without
adopting the framework.

Three rules from the LangGraph documentation govern everything that
follows, and each of them is a trap that has bitten a real
production system. The first is that an interrupt needs a
checkpointer — a persistence layer that saves the graph state when
the pause happens — because without somewhere durable to put the
state, there is nothing to resume from. The second is that resuming
restarts the node from its beginning rather than from the exact line
where the interrupt was called, which means any code before the
interrupt runs again, and therefore any side effect before the
interrupt must be idempotent or must live after the interrupt or in
a separate node. The third is that the interrupt itself works by
raising an exception, so wrapping it in a bare `try`/`except` block
swallows the pause entirely and the agent sails on as if nobody had
asked it to stop. All three rules are really the same rule stated
three ways: a pause is a property of the runtime and its storage,
not of the code path that happened to be executing, and if you treat
it as a local control-flow trick it will fail in exactly the way
local control-flow tricks fail — silently, under load, at 2 a.m.

That is also why the most useful place to put an interrupt is often
not in the agent's reasoning at all but inside the tool itself.
LangGraph documents interrupts in tools as a first-class pattern:
the tool pauses for approval whenever it is called, the approval
logic lives with the tool and is reusable across every graph that
uses it, and the model calls the tool naturally without knowing a
gate exists. This is the shape of the stock-price gate — the write
tool itself refuses to run without an approval — and it is the shape
you should reach for first, because it makes the gate impossible for
the model to forget: the model does not decide to ask, the tool
decides to stop.

### Durable pause and resume: surviving the restart

A pause that only works while the process stays alive is a demo, not
a control, and the difference between the two is entirely about
where the state lives when the human is thinking. The durable version
of the pattern has a strict ordering rule, and it is the single most
important sentence in this chapter: *checkpoint first, then
announce*. The agent writes the full state of the run — the task,
the proposed write, the approval request with its deadline — to
durable storage, and only then tells anyone that an approval is
waiting. If the process dies between those two steps, the run is
still resumable, because the announcement is reconstructible from
the checkpoint while the checkpoint is not reconstructible from the
announcement. The code in `code/durable_pause.py` follows this order
literally, and the exercise that kills the process mid-run exists to
make you feel why the order matters rather than merely to make you
believe it.

What "durable storage" means in practice depends on how long your
pauses last and how much you are willing to operate. At the small
end, a checkpointer backed by a database — SQLite in this chapter's
code, Postgres in production — is enough: the run's state is a JSON
document keyed by a thread identifier, and resuming is loading that
document by the same identifier and continuing from it. LangGraph's
documentation makes the thread identifier the central concept here,
calling it "effectively your persistent cursor", because reusing it
resumes the same checkpoint while a new value starts a brand-new
thread with empty state. At the large end, the same idea is
implemented by a durable-execution runtime such as Temporal, where
the workflow's entire event history is persisted by the platform and
a crashed worker resumes by replaying that history, which is a
stronger guarantee than checkpointing because the replay also covers
timers, signals, and activities that already ran
([Workflow Execution — Temporal documentation](https://docs.temporal.io/workflow-execution)).
Microsoft's course reaches the same conclusion from its own
framework's direction: its lesson on the Microsoft Agent Framework
lists durability — agent threads and workflows that can pause,
resume, and recover from errors — alongside human-in-the-loop
control as the two production features that separate a prototype
from a system
([Lesson 14: Microsoft Agent Framework](https://microsoft.github.io/ai-agents-for-beginners/14-microsoft-agent-framework/)).
The choice between a checkpointer and a durable-execution runtime is
a real architectural decision, and the design-review angle at the end of
this chapter asks you to defend one, but the pause itself does not
care which you chose: what it cares about is that the state is on
disk before anyone is asked anything, that the identifier that finds
it again is stable, and that resuming is a fresh process reading
state rather than an old process waking up.

There is one more property a durable pause needs, and it is the one
people forget because it only shows up in the failure case: the
pause needs a deadline. An interrupt in LangGraph waits indefinitely
until you resume it, which is correct for the framework and wrong
for a production approval, because an approval nobody answers is a
run that occupies a slot, a thread, and a human's attention forever.
So the approval this chapter issues carries an `expires_at`, and
every path that touches it — the decision endpoint, the resume path,
the timeout sweep — treats an expired approval as a rejection. The
default in the code is fifteen minutes, which is a design choice for
a chat-approval workflow rather than a number from any vendor, and
you should choose your own from the rhythm of your approvers: long
enough that a human in another timezone can answer, short enough
that a stuck run fails closed while anyone still remembers what it
was for. What matters is not the number but the invariant it
protects, which is that silence is never consent.

### Approval routing: webhooks and chat user interfaces

Once the agent has paused, the question has to travel, and the
routing layer is where most human-in-the-loop designs quietly fall
apart, because routing looks like plumbing and is actually the
security boundary. The question has two halves — getting the request
to the human, and getting the answer back — and each half has its own
failure modes. The outbound half is usually a webhook: the agent
posts a signed JSON payload to an endpoint the approver's system
owns, and the signature is what makes the payload trustworthy rather
than merely well-formed. This chapter's webhook uses the same
construction Slack documents for verifying its own requests: a
Hash-based Message Authentication Code using the SHA-256 hash
function, computed over `v0:<timestamp>:<raw body>` with a shared
secret, sent as `v0=<hex digest>`, and compared in constant time
([Verifying requests from Slack](https://docs.slack.dev/authentication/verifying-requests-from-slack/)).
The timestamp is inside the signed string, which is what makes the
five-minute replay window work — a captured request cannot be
replayed with a fresh timestamp, because changing the timestamp
breaks the signature. The inbound half, the decision endpoint, has
three obligations of its own: it must verify that signature before
it trusts anything in the body, it must be idempotent so that a
retried delivery does not record the same decision twice, and it
must refuse a second, different decision for the same approval,
because the first decision is the one the agent may already have
acted on. The code in `code/approval_server.py` implements all
three, and the test that replays a decision and expects `duplicate:
true` back is the test that would have caught the one real bug found
while writing this chapter — a stale state snapshot that overwrote a
recorded decision — which is exactly the class of bug this layer
exists to prevent.

The alternative to a raw webhook is to meet the human where they
already are, which in most organisations means Slack or Microsoft
Teams, and the two platforms rhyme closely enough that learning one
teaches you the other. In Slack the approval is a Block Kit message:
a header, a section with the facts of the request, and an `actions`
block holding an Approve button and a Reject button, each carrying
the approval identifier in its `value` so the click can be matched
to the pending approval
([Block Kit — Slack developer documentation](https://docs.slack.dev/block-kit/)).
Slack's own documentation puts the limits in plain numbers — up to
50 blocks in a message, up to 25 elements in an `actions` block —
which is enough structure to design a card that shows the human
everything they need to decide and nothing they do not
([Creating an interactive message — Slack developer documentation](https://docs.slack.dev/tools/deno-slack-sdk/guides/creating-an-interactive-message/)).
In Teams the same card is an Adaptive Card: the request as the card
body, Approve and Reject as `Action.Execute` actions carrying the
approval identifier in their data
([Adaptive Cards](https://adaptivecards.io/)).
The two platforms differ in one respect that matters more than any
difference in JSON shape, which is who can see the card. A Slack
message in a channel and a Teams card in a channel are both visible
to the whole channel, so a button anyone can press is not an approval
control — it is a suggestion box — and the fix is to scope the card
to the approver, either by sending it as a direct message or, in
Teams, by using the up-to-date views pattern Microsoft documents,
where the refresh property shows approvers a card with buttons and
everyone else the same card without them
([Up-to-date views — Microsoft Teams documentation](https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/universal-actions-for-adaptive-cards/up-to-date-views)).
And in both platforms, the button click is only the beginning of the
decision: the click arrives as a signed request, the signature is
verified exactly as the webhook's is, and only then does the decision
enter the store. The chat user interface is the presentation of the
gate, not the gate itself, and any design that confuses the two has
built a voting widget where it needed an access control.

Routing also has to answer the question nobody asks until the first
approval sits unanswered for a day, which is who the approval goes
to when the first approver does not answer. The minimal honest
design is escalation with a deadline: the request names one
approver, the deadline passes, the run fails closed, and the audit
trail records the timeout so the failure is visible rather than
silent. The richer design is a chain — a second approver, then a
duty roster, then a human on call — and the thing to notice is that
each step of the chain is itself a durable pause with its own
deadline, not a new mechanism. Temporal's documentation of delayed
callbacks makes this point in passing and it generalises well:
waiting for another system is done with durable timers and signals,
not with a process sleeping and hoping
([Delayed Callback — Temporal documentation](https://docs.temporal.io/design-patterns/delayed-callback)).
Whichever depth you build, the routing layer must never be the thing
that decides whether the write happens. It only carries the human's
decision to the place that does.

### Audit trails: proving who approved what, and when

If the pause is the control and the routing is the delivery, then
the audit trail is the proof, and it is the part of the system that
gets judged after the fact — by an incident review, by a compliance
audit, by a customer asking why a record changed. A useful audit
trail records five things about every consequential event, and the
absence of any one of them makes the other four deniable: what
happened, when it happened, who did it, what exactly was decided,
and what the state of the world was at the time. "What exactly was
decided" deserves emphasis, because an approval of "the changelog"
is not evidence of anything — the trail must record the exact
proposed write, the exact content, the exact file, so that the
decision can be checked against what actually ran. This chapter's
store records the approval request, the decision with the decider's
identifier and note, the write execution with its idempotency key,
and the run's completion as separate events, and the reconstruction
exercise in `exercises.md` asks you to rebuild the whole story from
those events alone, because a trail you cannot read without the live
system is a trail that dies with the live system.

The second property a trail needs is tamper-evidence, and the
cheapest honest version of it is a hash chain: every event records
the SHA-256 hash of the event before it, so editing or deleting any
earlier row changes every hash after it and the verification fails at
the first altered entry. The code in `code/store.py` implements this
in about twenty lines, and the test that rewrites one actor's name
and expects verification to fail at exactly that sequence number is
the whole point of the mechanism in miniature. A hash chain is not a
blockchain and it does not need to be one — it proves that the log
has not been altered since it was written, which is the property an
incident review actually needs, and it does so without any
infrastructure beyond the database the trail already lives in. What
it does not prove is equally worth stating: it does not prove the
events themselves are true, only that they are unaltered, and it
does not prove the approver was who they claimed to be — that is
the job of the signature verification on the inbound request and the
identity the chat platform attests to, not of the log. A trail that
claims more than that is overselling itself, and oversold audit
trails are how organisations discover, during an incident, that
their proof proves nothing.

Finally, the trail has to survive the same restart the pause does,
which is why this chapter keeps it in the same SQLite database as
the checkpoints rather than in a log file on the agent host. A log
file on a container that gets replaced is a trail that gets replaced
with it, and a trail that lives only in the model provider's
dashboard is a trail you cannot produce when the provider is the
party under question. Keep it in your own storage, in the same
transactional store as the run state, append-only by convention and
tamper-evident by construction, and treat any write path that can
update or delete an event as a bug rather than a feature.

## Mapping this onto your own stack

If your production agent runs on Mastra on Amazon Web Services
Elastic Container Service with Amazon Bedrock, as the reader's does,
then every mechanism in this chapter has a direct counterpart and
none of them requires changing frameworks. The durable pause maps to
whatever your checkpointer is — Mastra's storage for workflow state,
or a Postgres-backed checkpointer if you are running LangGraph
alongside it — and the rule is the same either way: the workflow
step that requests approval writes its checkpoint before it emits
the approval event, and the resume path is a new container reading
that checkpoint rather than an old container waking up. The webhook
endpoint maps to a route on your existing service behind your
existing authentication, with the shared-secret signature as an
additional check rather than a replacement for it, and the Slack or
Teams card maps to the channel your team already uses for deploys.
The audit trail maps to the same store your evaluation and guardrail
results already land in, with the hash chain added as a column
rather than a new system, so that the question "who approved this
write?" is answerable from the same place as the question "what did
the guardrail say about this run?". And the stock-price gate itself —
the human-approval gate on the write tool — is the place where all
four mechanisms meet: the tool pauses, the pause is checkpointed,
the approval is routed, the decision is recorded, and the write runs
exactly once or not at all. If you build only one thing from this
chapter, build that gate in that shape, and let the rest of the
chapter be the reasons why each of its properties is load-bearing.

## The advanced-level design-review angle

Reviewers at an advanced level do not ask you to recite what a
human-in-the-loop system is. They ask you to defend one under
pressure, and the pressure always takes the same three forms, so it
is worth rehearsing the answers as a connected argument rather than
as separate facts.

The first pressure is the guarantee question: *what does your gate
actually guarantee?* The honest answer is narrow, and its narrowness
is the point. The gate guarantees that a write in the gated class
cannot execute without a recorded decision from an authenticated
human, that the decision names the exact action it authorises, and
that the record of the decision survives the process that requested
it. It guarantees those things because the check is in code the
model cannot edit, because the pause is checkpointed before the
request goes out, and because the decision is written to durable
storage before the write runs. Notice the shape of that answer: each
guarantee names the mechanism that provides it, and a guarantee
without a mechanism is a wish. If you cannot name the mechanism,
you do not have the guarantee.

The second pressure is the negation: *what does it not guarantee?*
It does not guarantee the human read the request, or understood it,
or was the right person to approve it — those are properties of your
routing, your card design, and your identity verification, not of
the pause. It does not guarantee the approved action was safe,
because a human can approve an unsafe action and the gate will
faithfully execute it; the gate is a control on *who* decides, not
a judgement on *what* was decided. It does not guarantee the model
will ask at the right times, because the model never asks — the gate
fires on the tool call, unconditionally, and any design where the
model decides when to seek approval has reintroduced the exact
failure the gate was built to remove. And it does not guarantee
liveness: an approver who never answers leaves the run paused until
its deadline, and the system must fail closed, escalate, and record
the timeout, rather than waiting forever or — far worse — timing out
into an approval.

The third pressure is the trade-off question, and it is the one that
separates an advanced answer from a senior one: *when would you not
build this?* A gate on every tool call is a system that grinds to a
halt, because every approval costs a human's attention and attention
is the scarcest resource in the loop, so the real design work is
deciding the risk tiers — which tools are always gated, which are
gated above a threshold, and which run free with only an audit
record — and being able to say why each tier is where it is. The
strongest answer names the cost explicitly: approval latency against
autonomy, human fatigue against coverage, and the failure mode where
approvers start clicking Approve without reading, which is the point
at which the gate has become theatre and the honest response is to
narrow its scope until approvals are rare enough to be read. A gate
that fires a hundred times a day is not a control; it is a
notification stream with a button on it, and the engineer's job is
to notice that before the incident review does.

## What to run next

The code in `code/` implements the whole chapter in standard-library
Python. The plan step is a real model call, so the agent run and the
demo need a connected model — see "Connect a model" in the root
README — either key-based (any OpenAI-compatible API) or through
AWS Bedrock. Without a backend the run fails loudly instead of
planning from a template — this chapter has no mock model.
`durable_pause.py` is the agent that plans with the model and pauses
before its write tool, `approval_server.py` is the signed webhook
endpoint, `slack_gate.py` and `teams_gate.py` build the two chat
approval cards and deliver them for real when credentials are set,
`store.py` holds the checkpoints and the hash-chained audit log, and
`audit.py` reconstructs a run from the log alone. Start with
`python demo_end_to_end.py` (or `LLM_BACKEND=bedrock
LLM_BEDROCK_MODEL_ID=... python demo_end_to_end.py` on the Bedrock
path), which runs the full pause–approve–resume–audit cycle with
assertions at every step, then work through `exercises.md`, where
the first exercise kills the process mid-pause and demands the run
back.
