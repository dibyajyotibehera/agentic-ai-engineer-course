"""A scoped Model Context Protocol server: least-privilege tools, enforced twice.

This server subclasses the raw server in ``raw_mcp_server.py`` and adds the
one thing the raw server deliberately lacks: permissions. Each tool carries
a required scope, each server process runs as one role with a fixed set of
scopes, and the server enforces the scope in two places, not one:

1. **Discovery is filtered.** ``tools/list`` returns only the tools the
   caller's role may use. A read-only caller never even sees
   ``update_ticket_status``, so the model cannot be talked into calling a
   write tool it was never told exists. Hiding the tool is the first line
   of defence; it is not the only one, because a caller can always guess a
   tool name and call it directly.
2. **Calls are checked.** ``tools/call`` checks the scope before it
   validates arguments and before it runs anything, and refuses with a
   JSON-RPC ``-32001`` error. The check happens before validation on
   purpose: a caller without permission gets no information about whether
   its arguments were well formed.

Roles
-----
The role is fixed when the server process starts, with ``--role``. That
mirrors how a production server should work: the process runs as a service
account with a fixed identity, and the identity comes from the transport's
authentication (an OAuth token, a mutually-authenticated TLS certificate),
not from anything inside the JSON-RPC body, where a caller could simply
claim a wider scope. The command-line flag stands in for that transport
authentication in this chapter; the lesson text explains what replaces it
in production.

    reader   docs:read, tickets:read          (can search and read tickets)
    support  docs:read, tickets:read, tickets:write
    admin    everything support has, plus tickets:admin (reserved for a
             future destructive tool; no tool requires it yet)

Run it
------
    python scoped_server.py --role reader
    python scoped_server.py --role support

Then drive it with ``mcp_client.py`` or ``test_server.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from raw_mcp_server import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    PERMISSION_DENIED,
    RawMCPServer,
    Tool,
    get_ticket,
    search_docs,
    update_ticket_status,
)
from schemas import TOOL_SCHEMAS, tool_input_schema, validate_tool_call

# Scope names follow the ``<resource>:<action>`` shape. Keep them coarse
# enough to audit in one glance and fine enough that a read-only caller
# cannot write: three scopes cover this chapter's whole domain.
SCOPE_DOCS_READ = "docs:read"
SCOPE_TICKETS_READ = "tickets:read"
SCOPE_TICKETS_WRITE = "tickets:write"
SCOPE_TICKETS_ADMIN = "tickets:admin"

ROLES: dict[str, frozenset[str]] = {
    "reader": frozenset({SCOPE_DOCS_READ, SCOPE_TICKETS_READ}),
    "support": frozenset({SCOPE_DOCS_READ, SCOPE_TICKETS_READ, SCOPE_TICKETS_WRITE}),
    "admin": frozenset(
        {SCOPE_DOCS_READ, SCOPE_TICKETS_READ, SCOPE_TICKETS_WRITE, SCOPE_TICKETS_ADMIN}
    ),
}


def scoped_tools() -> list[Tool]:
    """The chapter's tools, each labelled with the scope calling it needs."""
    return [
        Tool(
            name="search_docs",
            description="Search the support documentation for entries about a topic. "
            "Read-only: it never changes anything.",
            input_schema=tool_input_schema("search_docs"),
            handler=lambda query, max_results=5: search_docs(query, max_results),
            required_scope=SCOPE_DOCS_READ,
        ),
        Tool(
            name="get_ticket",
            description="Read one support ticket by its identifier, for example TKT-1001. "
            "Read-only: it never changes anything.",
            input_schema=tool_input_schema("get_ticket"),
            handler=lambda ticket_id: get_ticket(ticket_id),
            required_scope=SCOPE_TICKETS_READ,
        ),
        Tool(
            name="update_ticket_status",
            description="Move a support ticket to a new status. This is a WRITE tool: "
            "it changes stored state, so it needs the tickets:write scope. "
            "Resolving a ticket requires a resolution note.",
            input_schema=tool_input_schema("update_ticket_status"),
            handler=lambda ticket_id, status, resolution_note="": update_ticket_status(
                ticket_id, status
            ),
            required_scope=SCOPE_TICKETS_WRITE,
        ),
    ]


class ScopedMCPServer(RawMCPServer):
    """The raw server, with discovery filtered and calls permission-checked."""

    def __init__(self, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"unknown role: {role!r}; choose from {sorted(ROLES)}")
        super().__init__(tools=scoped_tools())
        self.role = role
        self.scopes = ROLES[role]

    def visible_tools(self) -> list[Tool]:
        # Filtered discovery: a caller only learns about tools it may use.
        # The model cannot call what it cannot see, and an auditor can read
        # the tools/list answer for a role and know exactly what that role
        # is capable of.
        return [tool for tool in self._tools.values() if tool.required_scope in self.scopes]

    def check_permission(self, tool: Tool) -> None:
        if tool.required_scope not in self.scopes:
            raise PermissionError(
                f"role {self.role!r} lacks scope {tool.required_scope!r} "
                f"required by tool {tool.name!r}"
            )

    def _invoke(self, tool: Tool, arguments: dict[str, Any]) -> Any:
        """Validate with the strict Pydantic schema, then run the tool.

        The validated model is unpacked back into keyword arguments, so
        the handler always receives values of the declared types, with
        defaults applied and unknown fields already rejected.
        """
        validated = validate_tool_call(tool.name, arguments)
        return tool.handler(**validated.model_dump())

    def _call_tool(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Call a tool, mapping validation failure to -32602 (Invalid params).

        The base class maps ``TypeError``/``ValueError``/``KeyError`` the
        same way, but Pydantic's ``ValidationError`` is converted to a
        ``ValueError`` inside ``validate_tool_call`` already, so this
        override exists to keep the error message precise: the caller
        learns which fields broke and how, and nothing has run.
        """
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
        except ValueError as exc:
            return self._error_response(request_id, INVALID_PARAMS, str(exc))
        except KeyError as exc:
            return self._error_response(
                request_id, INVALID_PARAMS, f"Invalid arguments: {exc}"
            )
        except Exception as exc:  # A handler bug must not kill the server.
            return self._error_response(request_id, INTERNAL_ERROR, f"Tool failed: {exc}")
        return self._result_response(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(result)}]},
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Scoped MCP server (MCP revision 2026-07-28, stdio transport)."
    )
    parser.add_argument(
        "--role",
        choices=sorted(ROLES),
        default="reader",
        help="Role this server process runs as; fixes the scopes it enforces.",
    )
    args = parser.parse_args(argv)
    server = ScopedMCPServer(role=args.role)
    print(
        f"[scoped-mcp-server] Role {args.role!r}, scopes: {sorted(server.scopes)}",
        file=sys.stderr,
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
