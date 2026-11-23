"""The same three tools, built with FastMCP instead of by hand.

This file is the payoff for building the raw server first: read it after
``raw_mcp_server.py`` and ``scoped_server.py``, and notice how little of
the protocol is left visible. FastMCP owns the JSON-RPC framing, the
``server/discover`` and ``tools/list`` methods, the standard input/output
and Streamable HTTP transports, and the conversion of a Python function
signature into a tool input schema. What remains yours is the part that
matters most: the tool functions, their descriptions, and the decision
about which tools a caller may use.

The three tools are the same ones the raw server exposes — ``search_docs``,
``get_ticket`` and ``update_ticket_status`` — over the same in-memory
tickets and documentation, so the comparison with the raw server is made
by reading the two files side by side. The comparison stops at the code:
the chapter's ``mcp_client.py`` speaks the chapter's 2026-07-28 stateless
revision, with ``server/discover`` and a per-request protocol version in
``_meta``, while this server speaks the standard handshake protocol that
real FastMCP implements, so the chapter client cannot drive this file —
``server/discover`` comes back ``-32601`` and calls sent without an
``initialize`` handshake are refused with ``-32600`` (verified against
both ``python fastmcp_equivalent.py`` and ``fastmcp run``). Drive this
server with a standard MCP client instead: send ``initialize`` first,
then ``tools/list`` and ``tools/call`` as usual.

Requires FastMCP, which is not in ``requirements.txt`` because it is a
framework, not a dependency of the chapter's core exercises::

    pip install fastmcp
    fastmcp run fastmcp_equivalent.py

Two things FastMCP does not do for you, which the chapter's raw server
makes explicit: it does not scope tools by caller (a tool is either
registered or it is not — per-caller filtering and the ``-32001``
permission refusal from ``scoped_server.py`` are still your design to
build, for example by registering different tool sets per deployment or
by wrapping handlers in an authorisation check), and it does not decide
your validation strictness for you (a plain ``int`` annotation still
coerces ``"5"`` to ``5`` unless you ask Pydantic for strict mode, as
``schemas.py`` does).
"""

from __future__ import annotations

from fastmcp import FastMCP

from raw_mcp_server import DOCS, TICKETS, VALID_STATUSES

mcp = FastMCP("chapter-02-fastmcp-server")


@mcp.tool
def search_docs(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search the support documentation for entries about a topic.

    Read-only: it never changes anything.
    """
    lowered = query.lower()
    hits = [
        {"doc_id": doc_id, "text": text}
        for doc_id, text in DOCS.items()
        if lowered in text.lower() or lowered in doc_id
    ]
    return hits[:max_results]


@mcp.tool
def get_ticket(ticket_id: str) -> dict:
    """Read one support ticket by its identifier, for example TKT-1001.

    Read-only: it never changes anything.
    """
    return dict(TICKETS[ticket_id])


@mcp.tool
def update_ticket_status(ticket_id: str, status: str) -> dict:
    """Move a support ticket to a new status.

    This is a WRITE tool: it changes stored state. FastMCP exposes it to
    every client that connects; scoping it to a caller is your job, as
    ``scoped_server.py`` demonstrates for the raw server.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown status: {status!r}")
    if ticket_id not in TICKETS:
        raise KeyError(f"unknown ticket: {ticket_id}")
    TICKETS[ticket_id]["status"] = status
    return dict(TICKETS[ticket_id])


if __name__ == "__main__":
    mcp.run()
