# Chapter 2 code — Tool Use & MCP

Python 3.11 or later. The raw server needs nothing but the standard
library; the strict schemas and the scoped server need Pydantic:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Files

| File | What it is |
| --- | --- |
| `raw_mcp_server.py` | The raw Model Context Protocol server: JSON-RPC 2.0 over standard input/output, revision 2026-07-28, standard library only |
| `schemas.py` | Strict Pydantic models for every tool's arguments, with `extra="forbid"` and `strict=True` |
| `scoped_server.py` | The raw server plus least-privilege scopes: filtered `tools/list` and a `-32001` refusal on out-of-scope calls |
| `mcp_client.py` | A minimal client that drives any of the servers over the wire |
| `test_server.py` | The acceptance suite, plus a round-trip timing measurement |
| `fastmcp_equivalent.py` | The same three tools built with FastMCP, for comparison (needs `pip install fastmcp`) |

## Run it

```bash
# 1. The raw server, driven by the demo client
python mcp_client.py

# 2. The scoped server as the read-only role: watch the write tool
#    disappear from tools/list and fail with -32001 when called anyway
python mcp_client.py --server scoped_server.py --args --role reader

# 3. The same server as the support role: the write tool appears, and
#    resolving a ticket without a note fails with -32602
python mcp_client.py --server scoped_server.py --args --role support

# 4. The full acceptance suite
python test_server.py

# 5. The strict schemas on their own
python schemas.py
```

## Talking to a server by hand

Start the raw server and type JSON-RPC requests, one per line:

```bash
python raw_mcp_server.py
{"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}}
{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}}
{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_ticket", "arguments": {"ticket_id": "TKT-1001"}, "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}}
```

Server log lines go to standard error; standard output is the wire, so
keep them separate.

## One deliberate stand-in

The scoped server takes the caller's role from a `--role` command-line
flag, and that flag stands in for the transport authentication a
production server uses — an OAuth 2.1 token on a remote server, or a
mutually-authenticated TLS certificate — because a caller's identity must
never come from inside the JSON-RPC body, where a caller could simply
claim a wider scope. In production the process runs as a service account
with a fixed identity supplied by the transport, and the server maps that
authenticated identity to scopes before any tool is listed or called; the
flag is this chapter's stand-in for that arrangement, and the chapter
README explains what replaces it.
```

