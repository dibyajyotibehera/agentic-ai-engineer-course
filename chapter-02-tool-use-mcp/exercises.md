# Exercises — Chapter 2: Tool Use & MCP

Work the exercises in order: each one assumes the previous ones are done.
Run everything from `code/`, with the virtual environment from
`code/README.md` active. The acceptance criteria are the definition of
done — a passing test suite is not a substitute for the stated behaviour,
and the stated behaviour is not done until you can show it over the wire.

## Exercise 1 — Talk to the raw server by hand

**Difficulty:** Foundation

Start the raw server and conduct a full exchange yourself, one line at a
time, with no client code in between: `server/discover`, then `tools/list`,
then a `tools/call` to `get_ticket` for ticket `TKT-1001`. Then send three
deliberately broken requests — a line that is not JSON (JavaScript Object
Notation), a request for a method that does not exist, and a `tools/call`
with no `_meta` protocol version — and read the error each one returns.

**Acceptance criteria**

- The `server/discover` response lists protocol revision `2026-07-28`.
- The `tools/list` response contains `search_docs`, `get_ticket` and
  `update_ticket_status`, each with a `name`, a `description` and an
  `inputSchema`.
- The `get_ticket` call returns the ticket for `TKT-1001` with status
  `open`.
- The three broken requests return error codes `-32700`, `-32601` and
  `-32600` respectively, and the server stays alive after each one.

## Exercise 2 — Add a tool to the raw server

**Difficulty:** Foundation

Add a fourth read-only tool, `list_tickets`, to `raw_mcp_server.py`. It
takes an optional `status` argument (one of `open`, `in_progress`,
`resolved`, `closed`) and returns every ticket in that status, or every
ticket when no status is given. Give it a description a model could reason
from, and a schema that forbids unknown arguments.

**Acceptance criteria**

- `tools/list` shows `list_tickets` with the new schema.
- `tools/call` with `{"status": "open"}` returns only the open tickets,
  and with `{}` returns all three.
- `tools/call` with `{"status": "deleted"}` fails with `-32602`.
- `python test_server.py` still passes (add a check for the new tool if
  you extend the suite).

## Exercise 3 — Malformed calls fail closed

**Difficulty:** Intermediate

Using the scoped server as the `support` role, write a script that sends
the following five malformed `update_ticket_status` calls and records the
response to each: a missing `status` field; `max_results`-style coercion,
namely a string where the schema requires an integer, applied to
`search_docs`; an unknown extra field on `get_ticket`; a `ticket_id` of
`TKT-1` (which breaks the declared pattern); and a `status` of `resolved`
with no `resolution_note`. Then verify the ticket store is unchanged after
all five.

**Acceptance criteria**

- All five calls fail with `-32602`, and each error message names the
  offending field and the rule it broke.
- No ticket's status changes as a result of any of the five calls.
- The first four calls against the raw server also fail with `-32602`
  (the raw server's hand-rolled checks catch them, though with less
  precise messages — note the difference in your own words), while the
  fifth call, resolving without a note, succeeds on the raw server,
  because the cross-field rule lives only in the strict Pydantic schema.
  That contrast is the point of the exercise: the raw server cannot
  express a rule that spans two fields.

## Exercise 4 — Dynamic tool discovery

**Difficulty:** Intermediate

Change the scoped server so that tools are not hard-coded in
`scoped_server.py` but loaded at start-up from a registry: a Python module
that maps tool names to their Pydantic schema model, handler and required
scope, following the `TOOL_SCHEMAS` pattern in `schemas.py`. Then add a
new read-only tool, `search_tickets`, through the registry only — without
editing the server's dispatch code — and restart the server to pick it up.

**Acceptance criteria**

- The server starts, lists and calls tools from the registry, and no
  tool name appears in the dispatch code.
- `search_tickets` appears in `tools/list` for the `reader` role with a
  generated JSON Schema, and returns matching tickets when called.
- Removing the tool from the registry and restarting makes it disappear
  from `tools/list` and fail with `-32602` when called by name.

## Exercise 5 — Scoped permissions and a destructive tool

**Difficulty:** Advanced

Add a destructive tool, `delete_ticket`, that permanently removes a ticket
and requires the `tickets:admin` scope, and add a new `admin` usage
pattern to the exercise: the `reader` and `support` roles must both be
refused, while `admin` succeeds. Extend `test_server.py` with checks for
the new behaviour.

**Acceptance criteria**

- For the `reader` and `support` roles, `tools/list` does not contain
  `delete_ticket`, and calling it by name fails with `-32001`.
- The refusal happens before argument validation: calling it with
  malformed arguments as `reader` still returns `-32001`, not `-32602`.
- As `admin`, deleting `TKT-1003` removes it, a later `get_ticket` for
  `TKT-1003` fails with `-32602`, and the other tickets are untouched.
- Your new tests pass alongside the existing 18 checks.

## Exercise 6 — A Streamable HTTP transport

**Difficulty:** Advanced

Build an HTTP transport for the raw server using only the Python standard
library (`http.server`). It must accept a single HTTP POST per tool call,
read the JSON-RPC request from the body, check the per-request `_meta`
protocol version exactly as the standard input/output server does, and
return the JSON-RPC response as the body. A request that declares no
protocol version, or an unsupported one, must be refused with `-32600`.

**Acceptance criteria**

- A `curl` exchange completes `server/discover`, `tools/list` and a
  `tools/call` against the HTTP server, with the same response shapes as
  the standard input/output server.
- A POST with no `_meta` protocol version returns a `-32600` error.
- Two consecutive requests can be served by two different server
  processes with no shared state, demonstrating the stateless core (run
  the server, kill it, start a fresh one, and complete the call).
- No request depends on any earlier request: document one piece of state
  you were tempted to keep between requests, and why the stateless
  revision forbids relying on it.

## Exercise 7 — Wire the scoped server into Mastra

**Difficulty:** Advanced

Using your own Mastra project (the production stack from the lesson — no
new service or key is needed beyond the Amazon Bedrock access you already
have), connect the scoped server through `MCPClient` twice: once as the
`reader` role and once as the `support` role. Compare what each agent can
see and do, using the dynamic tool configuration (`getToolsets()`), and
turn on `requireToolApproval` for the `support` connection.

**Acceptance criteria**

- The `reader` agent's tool list contains no write tool, and asking it to
  change a ticket fails before any tool runs.
- The `support` agent can change a ticket, and the approval gate fires
  before the write executes.
- Write a short note (half a page) on what Mastra's `MCPClient` gave you
  for free compared with the raw server, and what you still had to build
  yourself — scoping, strictness and approval — with the file and line
  where each of those lives in your project.

## Exercise 8 — Package a procedure as a skill

**Difficulty:** Intermediate

Write a skill, as a `SKILL.md` file, that packages the ticket-resolution
procedure: read the ticket with `get_ticket`, search the documentation
with `search_docs` for the known failure, then move the ticket to
`resolved` with `update_ticket_status` and a resolution note saying how
it was resolved. Give it a YAML frontmatter block with a `name` and a
`description` that states both what the skill does and when to use it,
then a Markdown body with the steps in order and the tools each step may
call. Then deliberately add one step that contradicts the tool schema —
for example, a step that tells the agent to resolve the ticket without a
resolution note — and run the skill's steps against the scoped server as
the `support` role to see which source of truth wins.

**Acceptance criteria**

- The skill's frontmatter carries a `name` (lowercase, hyphenated) and a
  `description` stating what the skill does and when to use it; the body
  lists the steps in order, with the tool each step calls.
- Following the skill end to end resolves `TKT-1001` with a resolution
  note, and the note is visible on a later `get_ticket`.
- The contradicting step fails with `-32602`, the schema wins, and you
  can say in one sentence why the failure looks like a model mistake
  when it is really a disagreement between the skill and the schema.
- You can state, for each step of the procedure, whether it belongs in a
  tool, in the skill, or in a scope — and what breaks if it moves.

## Exercise 9 — Explain-back: justify NOT using MCP

**Difficulty:** Advanced

A reviewer assumes the Model Context Protocol is always the answer. Your
integration is a single billing lookup: one agent, in the same service,
calls it about fifty times a minute, the arguments are two fixed fields,
and no other host or team will ever call it. Write a half-page
justification for not using the Model Context Protocol here, addressed to
that reviewer, and sketch the alternative in one paragraph — a plain
in-process function call, or a narrow purpose-built client if the lookup
ever leaves the process.

**Acceptance criteria**

- The justification names the per-call token tax with a worked number
  (state your assumed schema size, as the lesson's worked example does),
  the operational cost of another process to deploy and monitor, and the
  context cost if the lookup ever returned a large payload.
- It states the rule of thumb for choosing, and the condition under
  which you would switch this integration to the Model Context Protocol
  — a second host, a second team, or an untrusted caller that needs
  scoping.
- It does not argue that the protocol is bad, only that it is the wrong
  unit for this integration; a reviewer who reads it can tell exactly
  what would change your mind.
```

