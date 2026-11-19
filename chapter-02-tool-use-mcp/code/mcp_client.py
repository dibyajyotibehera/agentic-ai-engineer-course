"""A minimal Model Context Protocol client, for driving the chapter's servers.

The client is deliberately small — under a hundred lines — because its job
is to make the wire visible, not to be a framework. It spawns a server as
a subprocess, writes one JSON object per line to the server's standard
input, and reads one JSON object per line back from its standard output.
That is the whole standard input/output transport, from the other side.

Every request carries the per-request metadata that revision 2026-07-28
requires: the protocol version and the client capabilities in ``_meta``.
``server/discover`` is the exception, since it is the call that tells the
client which versions the server speaks.

Run it against the raw server
-----------------------------
    python mcp_client.py

Run it against the scoped server, as the read-only role
--------------------------------------------------------
    python mcp_client.py --server scoped_server.py --args --role reader

Then watch the write call fail with -32001 (permission denied), and the
tool list come back without the write tool at all.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROTOCOL_VERSION = "2026-07-28"
META = {
    "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {"tools": {}},
    "io.modelcontextprotocol/clientInfo": {"name": "chapter-02-demo-client", "version": "1.0.0"},
}


class MCPClient:
    """A line-oriented JSON-RPC 2.0 client over a server subprocess."""

    def __init__(self, command: list[str]) -> None:
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._next_id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        """Send one request and return the parsed response object."""
        self._next_id += 1
        message: dict = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        # server/discover goes out without _meta: it is how the client
        # learns the versions before it can declare one.
        if params is not None:
            message["params"] = params
        assert self._process.stdin is not None and self._process.stdout is not None
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()
        line = self._process.stdout.readline()
        if not line:
            raise RuntimeError("Server closed the connection without a response.")
        return json.loads(line)

    def discover(self) -> dict:
        return self.request("server/discover", {})

    def list_tools(self) -> dict:
        return self.request("tools/list", {"_meta": META})

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments, "_meta": META})

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        self._process.wait(timeout=5)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Minimal MCP client for the chapter servers.")
    parser.add_argument("--server", default="raw_mcp_server.py", help="Server script to spawn.")
    parser.add_argument("--args", nargs=argparse.REMAINDER, default=[], help="Arguments for the server.")
    args = parser.parse_args(argv)
    here = Path(__file__).resolve().parent
    command = [sys.executable, str(here / args.server), *args.args]
    client = MCPClient(command)
    try:
        print("== server/discover ==")
        print(json.dumps(client.discover(), indent=2))
        print("== tools/list ==")
        print(json.dumps(client.list_tools(), indent=2))
        print("== tools/call get_ticket TKT-1001 ==")
        print(json.dumps(client.call_tool("get_ticket", {"ticket_id": "TKT-1001"}), indent=2))
        print("== tools/call update_ticket_status TKT-1001 -> resolved ==")
        print(
            json.dumps(
                client.call_tool(
                    "update_ticket_status",
                    {
                        "ticket_id": "TKT-1001",
                        "status": "resolved",
                        "resolution_note": "Raised the tool-chain timeout; verified on a replay.",
                    },
                ),
                indent=2,
            )
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
