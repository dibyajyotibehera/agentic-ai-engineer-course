"""Acceptance suite for the chapter's servers, plus a round-trip measurement.

Run it
------
    python test_server.py

It spawns the raw server and the scoped server (as the read-only ``reader``
role) as subprocesses and checks, over the real standard input/output
wire, that:

1. ``server/discover`` answers with protocol revision 2026-07-28.
2. ``tools/list`` returns the three tools on the raw server.
3. A valid ``get_ticket`` call returns the ticket.
4. A malformed call (missing field, wrong type, unknown extra field)
   fails with JSON-RPC -32602 and no side effect.
5. On the scoped server as ``reader``, ``tools/list`` hides the write
   tool, and calling it anyway fails with -32001 (permission denied).
6. An unknown method fails with -32601, garbage input with -32700, a
   request without ``_meta`` with -32600, and a notification gets no
   reply at all.

It finishes by timing 50 ``tools/call`` round trips against the raw server
and printing the median, so the chapter's latency claim is a measurement
from this machine, not a guess. Timing includes process start-up only
once; the 50 calls share one server process.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_client import META, MCPClient  # noqa: E402

HERE = Path(__file__).resolve().parent

CHECKS = 0


def check(condition: bool, label: str) -> None:
    """One named assertion; the suite reports the count at the end."""
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(f"FAILED: {label}")
    print(f"  ok: {label}")


def raw_request(client: MCPClient, message: dict) -> dict | None:
    """Send a hand-built message and read one response line, or None."""
    assert client._process.stdin is not None and client._process.stdout is not None
    client._process.stdin.write(json.dumps(message) + "\n")
    client._process.stdin.flush()
    line = client._process.stdout.readline()
    return json.loads(line) if line else None


def test_raw_server() -> None:
    print("Raw server")
    client = MCPClient([sys.executable, str(HERE / "raw_mcp_server.py")])
    try:
        response = client.discover()
        check(
            response["result"]["protocolVersions"] == ["2026-07-28"],
            "server/discover advertises protocol revision 2026-07-28",
        )
        response = client.list_tools()
        names = [tool["name"] for tool in response["result"]["tools"]]
        check(
            names == ["search_docs", "get_ticket", "update_ticket_status"],
            "tools/list returns all three tools",
        )
        response = client.call_tool("get_ticket", {"ticket_id": "TKT-1001"})
        check(
            json.loads(response["result"]["content"][0]["text"])["status"] == "open",
            "valid get_ticket returns the ticket",
        )
        response = client.call_tool("get_ticket", {"ticket_id": "NOPE"})
        check(
            response["error"]["code"] == -32602,
            "unknown ticket identifier fails with -32602 (the raw server's "
            "hand-rolled checks reject it; the strict schema would reject "
            "the shape as well)",
        )
        response = raw_request(
            client,
            {
                "jsonrpc": "2.0",
                "id": 90,
                "method": "tools/call",
                "params": {
                    "name": "search_docs",
                    "arguments": {"query": "scoping", "max_results": "5"},
                    "_meta": META,
                },
            },
        )
        assert response is not None
        check(
            response["error"]["code"] == -32602,
            "string where an integer belongs fails with -32602 (no coercion)",
        )
        response = client.request("tools/list", {})
        check(
            response["error"]["code"] == -32600,
            "request without _meta protocol version fails with -32600",
        )
        response = client.request("no_such/method", {"_meta": META})
        check(
            response["error"]["code"] == -32601,
            "unknown method fails with -32601",
        )
        response = raw_request(
            client, {"jsonrpc": "2.0", "id": 91, "method": "initialize", "params": {}}
        )
        assert response is not None
        check(
            response["error"]["code"] == -32601
            and "2026-07-28" in response["error"]["message"],
            "removed initialize handshake fails with -32601 and a migration hint",
        )
        # Garbage input: write it directly, read the -32700 parse error.
        assert client._process.stdin is not None and client._process.stdout is not None
        client._process.stdin.write("{not json\n")
        client._process.stdin.flush()
        response = json.loads(client._process.stdout.readline())
        check(response["error"]["code"] == -32700, "unparseable line fails with -32700")
        # A notification (no "id") must produce no response line at all.
        client._process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        client._process.stdin.flush()
        client._process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 92, "method": "server/discover", "params": {}})
            + "\n"
        )
        client._process.stdin.flush()
        response = json.loads(client._process.stdout.readline())
        check(
            response["id"] == 92,
            "notification produces no reply: the next response belongs to the next request",
        )
    finally:
        client.close()


def test_scoped_server() -> None:
    print("Scoped server, role reader")
    client = MCPClient(
        [sys.executable, str(HERE / "scoped_server.py"), "--role", "reader"]
    )
    try:
        response = client.list_tools()
        names = [tool["name"] for tool in response["result"]["tools"]]
        check(
            names == ["search_docs", "get_ticket"],
            "tools/list for reader hides the write tool (filtered discovery)",
        )
        response = client.call_tool(
            "update_ticket_status", {"ticket_id": "TKT-1001", "status": "closed"}
        )
        check(
            response["error"]["code"] == -32001,
            "reader calling the write tool fails with -32001, before validation",
        )
        # The refusal happens before validation, so even a malformed call
        # to the write tool is refused for permission, not for shape.
        response = client.call_tool("update_ticket_status", {"ticket_id": "NOPE"})
        check(
            response["error"]["code"] == -32001,
            "permission is checked before argument validation",
        )
        response = client.call_tool("get_ticket", {"ticket_id": "TKT-1001", "extra": 1})
        check(
            response["error"]["code"] == -32602,
            "unknown extra argument fails with -32602",
        )
        response = client.call_tool(
            "update_ticket_status",
            {"ticket_id": "TKT-1001", "status": "resolved"},
        )
        check(
            response["error"]["code"] == -32001,
            "resolving without a note is still a permission refusal for reader",
        )
    finally:
        client.close()

    print("Scoped server, role support")
    client = MCPClient(
        [sys.executable, str(HERE / "scoped_server.py"), "--role", "support"]
    )
    try:
        response = client.list_tools()
        names = [tool["name"] for tool in response["result"]["tools"]]
        check(
            "update_ticket_status" in names,
            "tools/list for support shows the write tool",
        )
        response = client.call_tool(
            "update_ticket_status",
            {"ticket_id": "TKT-1001", "status": "resolved"},
        )
        check(
            response["error"]["code"] == -32602,
            "resolving without a resolution note fails with -32602 (cross-field rule)",
        )
        response = client.call_tool(
            "update_ticket_status",
            {
                "ticket_id": "TKT-1001",
                "status": "resolved",
                "resolution_note": "Raised the tool-chain timeout.",
            },
        )
        check(
            json.loads(response["result"]["content"][0]["text"])["status"] == "resolved",
            "resolving with a note succeeds and changes the ticket",
        )
    finally:
        client.close()


def measure_round_trip() -> float:
    """Median milliseconds for one tools/call round trip on this machine."""
    client = MCPClient([sys.executable, str(HERE / "raw_mcp_server.py")])
    try:
        client.discover()  # Warm the process up before timing.
        durations: list[float] = []
        for _ in range(50):
            start = time.perf_counter()
            client.call_tool("get_ticket", {"ticket_id": "TKT-1001"})
            durations.append((time.perf_counter() - start) * 1000)
        return statistics.median(durations)
    finally:
        client.close()


def main() -> None:
    test_raw_server()
    test_scoped_server()
    median_ms = measure_round_trip()
    print(f"\nMedian tools/call round trip over stdio: {median_ms:.2f} ms (own measurement, "
          f"this machine, 50 calls, one server process).")
    print(f"\nAll {CHECKS} checks passed.")


if __name__ == "__main__":
    main()
