# References — Chapter 8: Human-in-the-Loop & Breakpoints

Every link below was verified live on 18 November 2026 by opening
the page or by confirming it in search results returned for that
exact address. No link here was constructed from a domain name or
from memory.

## Syllabus inspiration and adapted material

- **LangChain Academy — Introduction to LangGraph (Python).**
  The free course whose third module (streaming, breakpoints,
  editing state and human feedback, dynamic breakpoints, time
  travel) is the syllabus inspiration for this chapter. 55 lessons,
  about 6 hours of video. The course prose carries no open licence,
  so nothing from it was copied; only its structure was followed.
  <https://academy.langchain.com/courses/intro-to-langgraph>
- **Microsoft — AI Agents for Beginners, Lesson 14: Microsoft Agent
  Framework.** Adapted with attribution under the MIT Licence: the
  lesson lists durability (agent threads and workflows that pause,
  resume and recover from errors) and human-in-the-loop control
  (tasks marked as requiring human approval) as production features,
  and its workflow-checkpointing pattern is the adapted source for
  the durable pause in this chapter.
  <https://microsoft.github.io/ai-agents-for-beginners/14-microsoft-agent-framework/>
- **Microsoft — AI Agents for Beginners, Lesson 6: Building
  Trustworthy Agents.** Adapted with attribution under the MIT
  Licence: the human-in-the-loop section and its pre-action approval
  gates, risk tiering and audit logging.
  <https://microsoft.github.io/ai-agents-for-beginners/06-building-trustworthy-agents/>
- **Microsoft — AI Agents for Beginners (repository).** The source
  repository for the two lessons above, MIT-licensed.
  <https://github.com/microsoft/ai-agents-for-beginners>

## The pause mechanism

- **LangGraph — Interrupts (Python documentation).** The canonical
  reference for this chapter's mechanism: `interrupt()` as a dynamic
  pause, static breakpoints via `interrupt_before` /
  `interrupt_after`, checkpointers as the persistence layer, the
  thread identifier as the resume cursor, `Command(resume=...)` as
  the resume call, and the three rules that govern interrupts —
  the node restarts from its beginning on resume, `interrupt()` must
  not be wrapped in a bare `try`/`except`, and side effects before
  an interrupt must be idempotent.
  <https://docs.langchain.com/oss/python/langgraph/interrupts>
- **Temporal — Workflow Execution.** The durable-execution
  alternative to a checkpointer: a workflow execution is durable
  because it runs effectively once and to completion, is fully
  recoverable after a failure, and resumes by replaying its event
  history.
  <https://docs.temporal.io/workflow-execution>
- **Temporal — Delayed Callback (Webhooks).** Durable timers and
  signals as the way to wait for another system, including the
  inbound-webhook-via-signal-with-start pattern used for approval
  routing.
  <https://docs.temporal.io/design-patterns/delayed-callback>

## Approval routing: webhooks and chat user interfaces

- **Slack — Block Kit.** The user-interface framework for approval
  cards: blocks, block elements and composition objects; up to 50
  blocks in a message and 100 in modals or Home tabs; an `actions`
  block holds the interactive elements.
  <https://docs.slack.dev/block-kit/>
- **Slack — Creating an interactive message.** How an interactive
  Block Kit message is built and how its interactions are handled,
  including the `action_id` and `block_id` that identify the
  component and the block a user interacted with.
  <https://docs.slack.dev/tools/deno-slack-sdk/guides/creating-an-interactive-message/>
- **Slack — Verifying requests from Slack.** The signing scheme this
  chapter's webhook copies: the `X-Slack-Signature` and
  `X-Slack-Request-Timestamp` headers, the
  `v0:<timestamp>:<raw body>` base string hashed with the signing
  secret using the SHA-256 hash function, constant-time comparison,
  and rejecting timestamps more than five minutes from local time.
  <https://docs.slack.dev/authentication/verifying-requests-from-slack/>
- **Adaptive Cards.** The card framework behind Microsoft Teams
  approval cards, including the Teams UI kit, the icon catalogue
  and the open-source software development kit documentation.
  <https://adaptivecards.io/>
- **Microsoft Teams — Up-to-date views.** The pattern for approval
  cards in a shared channel: a refresh property that shows
  approvers a card with Approve and Reject buttons and everyone
  else the same card without them, with the card updated by message
  edit as each approver decides.
  <https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/universal-actions-for-adaptive-cards/up-to-date-views>

## Design guidance

- **Anthropic — Building effective agents.** The framing this
  chapter's interview angle draws on: agents begin with a command
  from, or discussion with, the human user, and can pause for human
  feedback at checkpoints or when encountering blockers, with
  stopping conditions such as a maximum number of iterations to
  maintain control.
  <https://www.anthropic.com/engineering/building-effective-agents>

## Course context

- **LLM course.** The style model for this whole
  course (chapter shape, hands-on notebooks, references), cited
  here rather than copied from.
  <https://github.com/mlabonne/llm-course>

## Provenance note

Per `CONVERSION_PLAN.md`: LangChain Academy's *Introduction to
LangGraph* is syllabus inspiration only (no open licence on the
course prose, never copied); Microsoft Lesson 14 is adapted with
attribution under the MIT Licence; the asynchronous approvals,
webhook routing, Slack and Teams user-interface gates, audit trails
that survive a restart, and the approval-gate design for write tools
are written originally for this chapter.
