"""A raw Model Context Protocol server, built from the JSON-RPC layer up.

This module uses only the Python standard library. There is no Model Context
Protocol framework here on purpose: the point of this chapter is to show
exactly what a framework does for you, so that when it misbehaves in
production you can reason about the wire instead of guessing.

Protocol pin
------------
The implementation follows revision ``2026-07-28`` of the Model Context
Protocol specification. That revision made the protocol stateless: the
``initialize`` handshake and the ``Mcp-Session-Id`` header are gone, every
request carries its own protocol version and client capabilities in its
``_meta`` field, and every server must answer ``server/discover``. One
request, one response, no memory of what came before.

Transport
---------
Standard input and standard output, one JSON object per line (newline
delimited JSON). That is the standard input/output transport: the host
process spawns this server as a subprocess and talks to it over pipes.
Logging goes to standard error, never to standard output, because standard
output is the wire.

Run it
------
    python raw_mcp_server.py

Then type JSON-RPC requests, one per line, for example::

    {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}}
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list",
     "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}}

See ``mcp_client.py`` for a small client that drives this server, and
``test_server.py`` for the full acceptance suite.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_VERSIONS = ("2026-07-28",)
SERVER_NAME = "chapter-02-raw-mcp-server"
SERVER_VERSION = "1.0.0"

# JSON-RPC 2.0 error codes. Codes -32768 to -32000 are reserved for
# pre-defined errors; -32000 to -32099 are reserved for server-defined
# errors, which is where PERMISSION_DENIED lives.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
PERMISSION_DENIED = -32001  # Server-defined: the caller lacks the scope.

META_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"

# Methods that existed in earlier, handshake-based revisions and were
# removed in 2026-07-28. A client that still sends them gets a clear
# METHOD_NOT_FOUND instead of silence, with a message that says why.
REMOVED_METHODS = {
    "initialize": "removed in protocol revision 2026-07-28: use server/discover, "
    "and send the protocol version in _meta on every request",
    "ping": "removed in protocol revision 2026-07-28",
}


# ---------------------------------------------------------------------------
# A tiny in-memory domain: support tickets and documentation
# ---------------------------------------------------------------------------
# The tools below are deliberately boring. The chapter is about the wire,
# the schemas and the permissions, not about the domain, so the domain is
# small enough to hold in your head while you watch the protocol work.

TICKETS: dict[str, dict[str, Any]] = {
    "TKT-1001": {
        "ticket_id": "TKT-1001",
        "title": "Bedrock agent times out on long tool chains",
        "status": "open",
        "severity": "high",
    },
    "TKT-1002": {
        "ticket_id": "TKT-1002",
        "title": "MCP server returns stale tool list after deploy",
        "status": "in_progress",
        "severity": "medium",
    },
    "TKT-1003": {
        "ticket_id": "TKT-1003",
        "title": "Guardrail blocks legitimate refund request",
        "status": "resolved",
        "severity": "low",
    },
}

DOCS: dict[str, str] = {
    "mcp-overview": "The Model Context Protocol is an open protocol that lets "
    "a language-model application discover and call tools through one "
    "JSON-RPC 2.0 interface.",
    "tool-scoping": "Scope every tool to the smallest permission it needs, "
    "and filter tools/list so a caller never even sees a tool it cannot use.",
    "fail-closed": "A tool call that fails validation must fail without side "
    "effects: no partial write, no retry with guessed arguments.",
}

TICKET_ID_PATTERN = re.compile(r"^TKT-\d+$")
VALID_STATUSES = ("open", "in_progress", "resolved", "closed")


def search_docs(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Return documentation entries whose text mentions the query."""
    lowered = query.lower()
    hits = [
        {"doc_id": doc_id, "text": text}
        for doc_id, text in DOCS.items()
        if lowered in text.lower() or lowered in doc_id
    ]
    return hits[:max_results]


def get_ticket(ticket_id: str) -> dict[str, Any]:
    """Return one ticket, or raise KeyError when it does not exist."""
    return dict(TICKETS[ticket_id])


def update_ticket_status(ticket_id: str, status: str) -> dict[str, Any]:
    """Move a ticket to a new status. This is the only write tool."""
    if ticket_id not in TICKETS:
        raise KeyError(f"unknown ticket: {ticket_id}")
    TICKETS[ticket_id]["status"] = status
    return dict(TICKETS[ticket_id])


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
# Each entry is the whole contract for one tool: the name the model sees,
# the description the model reasons from, the JSON Schema the model must
# conform to, and the function that runs. ``required_scope`` is None here
# because the raw server does not enforce scopes; ``scoped_server.py``
# subclasses this server and gives every tool a scope.

ToolHandler = Callable[..., Any]


class Tool:
    """One tool, as the server advertises it and as it executes."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: ToolHandler,
        required_scope: str | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler
        self.required_scope = required_scope

    def to_dict(self) -> dict[str, Any]:
        """The shape this tool takes inside a tools/list result."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def _string_property(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


TOOLS: list[Tool] = [
    Tool(
        name="search_docs",
        description="Search the support documentation for entries about a topic. "
        "Read-only: it never changes anything.",
        input_schema={
            "type": "object",
            "properties": {
                "query": _string_property("Topic to search for, for example 'tool scoping'."),
                "max_results": {
                    "type": "integer",
                    "description": "Maximum entries to return, from 1 to 10.",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=lambda query, max_results=5: search_docs(query, max_results),
    ),
    Tool(
        name="get_ticket",
        description="Read one support ticket by its identifier, for example TKT-1001. "
        "Read-only: it never changes anything.",
        input_schema={
            "type": "object",
            "properties": {
                "ticket_id": _string_property("Ticket identifier, of the form TKT-<digits>."),
            },
            "required": ["ticket_id"],
            "additionalProperties": False,
        },
        handler=lambda ticket_id: get_ticket(ticket_id),
    ),
    Tool(
        name="update_ticket_status",
        description="Move a support ticket to a new status. This is a WRITE tool: "
        "it changes stored state, so it needs the tickets:write scope.",
        input_schema={
            "type": "object",
            "properties": {
                "ticket_id": _string_property("Ticket identifier, of the form TKT-<digits>."),
                "status": {
                    "type": "string",
                    "description": "The new status for the ticket.",
                    "enum": list(VALID_STATUSES),
                },
            },
            "required": ["ticket_id", "status"],
            "additionalProperties": False,
        },
        handler=lambda ticket_id, status: update_ticket_status(ticket_id, status),
    ),
]


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


class RawMCPServer:
    """A stateless JSON-RPC 2.0 server speaking MCP revision 2026-07-28.

    The server keeps no per-client state between requests. Every request is
    judged on its own: its protocol version is checked, its method is
    dispatched, and the response goes back on the same line-based wire.
    Subclasses override :meth:`visible_tools` and :meth:`check_permission`
    to add scoping; the raw server itself exposes everything to everyone,
    which is exactly the posture the scoped server exists to fix.
    """

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in (tools if tools is not None else TOOLS)}

    # -- hooks for subclasses ------------------------------------------------

    def visible_tools(self) -> list[Tool]:
        """Tools this caller is allowed to discover. The raw server shows all."""
        return list(self._tools.values())

    def check_permission(self, tool: Tool) -> None:
        """Raise PermissionError when this caller may not call ``tool``.

        The raw server enforces nothing. ``scoped_server.py`` overrides this.
        """

    # -- JSON-RPC plumbing ---------------------------------------------------

    def handle_line(self, line: str) -> str | None:
        """Handle one input line. Returns a response line, or None.

        None means "no response": the input was a notification, which by the
        JSON-RPC 2.0 rules is a request without an ``id`` and must not be
        answered. Answering a notification would corrupt the line framing,
        because the client is not reading a reply for it.
        """
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            return self._encode(self._error_response(None, PARSE_ERROR, f"Parse error: {exc}"))
        if not isinstance(message, dict):
            return self._encode(
                self._error_response(None, INVALID_REQUEST, "Request must be a JSON object")
            )
        if message.get("jsonrpc") != "2.0":
            return self._encode(
                self._error_response(
                    message.get("id"),
                    INVALID_REQUEST,
                    'Field "jsonrpc" must be exactly "2.0"',
                )
            )

        # A notification has no "id": acknowledge it by saying nothing.
        if "id" not in message:
            self._log(f"Ignoring notification: {message.get('method')}")
            return None

        request_id = message["id"]
        method = message.get("method")
        params = message.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return self._encode(
                self._error_response(request_id, INVALID_REQUEST, "Malformed request object")
            )
        return self._encode(self._dispatch(request_id, method, params))

    def _dispatch(self, request_id: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Route one request. Every branch returns a full response object."""
        if method in REMOVED_METHODS:
            return self._error_response(request_id, METHOD_NOT_FOUND, REMOVED_METHODS[method])

        # server/discover is the one method that must work before the client
        # knows which protocol version to declare: it is how the client
        # learns the versions in the first place, so it is exempt from the
        # per-request version check below.
        if method == "server/discover":
            return self._result_response(
                request_id,
                {
                    "protocolVersions": list(SUPPORTED_VERSIONS),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )

        # Every other request must declare a protocol version it supports,
        # in _meta, on every request. No version, no service: this is the
        # stateless core doing its job, and it is also what makes the
        # server safe to run behind a plain load balancer, since no
        # request depends on any earlier one.
        version_error = self._check_protocol_version(params)
        if version_error is not None:
            return self._error_response(request_id, INVALID_REQUEST, version_error)

        if method == "tools/list":
            return self._result_response(
                request_id,
                {"tools": [tool.to_dict() for tool in self.visible_tools()]},
            )
        if method == "tools/call":
            return self._call_tool(request_id, params)
        return self._error_response(
            request_id, METHOD_NOT_FOUND, f"Method not found: {method}"
        )

    def _check_protocol_version(self, params: dict[str, Any]) -> str | None:
        """Return an error message when the request declares no usable version."""
        meta = params.get("_meta")
        if not isinstance(meta, dict):
            return (
                "Missing params._meta: every request must declare "
                f'"{META_VERSION_KEY}" (protocol revision {PROTOCOL_VERSION})'
            )
        version = meta.get(META_VERSION_KEY)
        if version not in SUPPORTED_VERSIONS:
            return (
                f"Unsupported protocol version: {version!r}. "
                f"This server supports: {', '.join(SUPPORTED_VERSIONS)}"
            )
        return None

    def _call_tool(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Validate the call envelope, check permission, then run the tool."""
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(name, str):
            return self._error_response(
                request_id, INVALID_PARAMS, 'tools/call requires a string "name"'
            )
        if not isinstance(arguments, dict):
            return self._error_response(
                request_id, INVALID_PARAMS, 'tools/call requires an object "arguments"'
            )
        tool = self._tools.get(name)
        if tool is None:
            return self._error_response(
                request_id, INVALID_PARAMS, f"Unknown tool: {name}"
            )
        try:
            self.check_permission(tool)
        except PermissionError as exc:
            return self._error_response(request_id, PERMISSION_DENIED, str(exc))
        try:
            result = self._invoke(tool, arguments)
        except (TypeError, ValueError, KeyError) as exc:
            # The raw server's validation is deliberately thin: it checks
            # types and required fields by hand, so that the contrast with
            # the strict Pydantic validation in schemas.py is visible.
            # Anything it cannot run is an INVALID_PARAMS error, never a
            # half-executed call.
            return self._error_response(request_id, INVALID_PARAMS, f"Invalid arguments: {exc}")
        except Exception as exc:  # A handler bug must not kill the server.
            return self._error_response(request_id, INTERNAL_ERROR, f"Tool failed: {exc}")
        return self._result_response(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(result)}]},
        )

    def _invoke(self, tool: Tool, arguments: dict[str, Any]) -> Any:
        """Run a tool after the thin hand-rolled checks of the raw server."""
        schema = tool.input_schema
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for field in required:
            if field not in arguments:
                raise ValueError(f'missing required argument: "{field}"')
        if schema.get("additionalProperties") is False:
            for field in arguments:
                if field not in properties:
                    raise ValueError(f'unknown argument: "{field}"')
        return tool.handler(**arguments)

    # -- response construction -------------------------------------------------

    @staticmethod
    def _result_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        # Revision 2026-07-28 results carry a resultType field.
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"resultType": "success", **result},
        }

    @staticmethod
    def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def _encode(response: dict[str, Any]) -> str:
        return json.dumps(response)

    @staticmethod
    def _log(message: str) -> None:
        print(f"[raw-mcp-server] {message}", file=sys.stderr, flush=True)

    # -- transport loop ---------------------------------------------------------

    def serve_forever(self) -> None:
        """Read newline-delimited JSON from stdin, write replies to stdout."""
        self._log(f"Listening on stdio, protocol revision {PROTOCOL_VERSION}.")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                response = self.handle_line(line)
            except Exception as exc:  # Never let one bad line kill the server.
                response = self._encode(
                    self._error_response(None, INTERNAL_ERROR, f"Unhandled error: {exc}")
                )
            if response is not None:
                print(response, flush=True)


def main() -> None:
    RawMCPServer().serve_forever()


if __name__ == "__main__":
    main()
