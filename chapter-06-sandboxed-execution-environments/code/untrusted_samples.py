"""Sample programs for the sandbox exercises.

Each function returns a string of Python source code. These are the
"agent-generated" programs the sandbox has to contain: one benign
workload and a set of hostile ones. None of them is run on the host
directly by the exercises — they are always passed to
``sandbox_runner.py`` (Docker path) or, for the resource-limit
mechanics only, to its local fallback.

Treat these as untrusted input even though you wrote them: that is
the point. If you add your own samples, add them here and run them
through the runner the same way.
"""

from __future__ import annotations

import textwrap


def benign() -> str:
    """A normal agent workload: parse data, compute, print a result."""
    return textwrap.dedent("""\
        import json

        rows = [{"id": i, "value": i * i} for i in range(1000)]
        total = sum(r["value"] for r in rows)
        print(json.dumps({"rows": len(rows), "total": total}))
        print("benign run completed")
        """)


def memory_hog() -> str:
    """Allocate far more memory than the limit allows."""
    return textwrap.dedent("""\
        # Tries to hold ~512 MiB in a list, against a 256 MiB limit.
        data = []
        for _ in range(64):
            data.append(bytearray(8 * 1024 * 1024))
        print(f"allocated {len(data) * 8} MiB — the limit did not hold!")
        """)


def cpu_spin() -> str:
    """Burn CPU in a tight loop until the timeout kills it."""
    return textwrap.dedent("""\
        import time

        start = time.time()
        x = 0
        while True:
            x += 1
            if x % 10_000_000 == 0:
                print(f"still spinning after {time.time() - start:.0f}s", flush=True)
        """)


def fork_bomb() -> str:
    """Spawn processes without bound until the process limit stops it."""
    return textwrap.dedent("""\
        import os

        # Deliberately hostile: keep forking until something stops us.
        # The --pids-limit flag (Docker) or RLIMIT_NPROC (local path)
        # is what has to stop this, not good behaviour.
        while True:
            os.fork()
        """)


def disk_fill() -> str:
    """Try to fill the disk; the read-only filesystem has to refuse."""
    return textwrap.dedent("""\
        # Tries to write a large file to the working directory.
        # On the Docker path the filesystem is read-only, so this must fail.
        with open("fill.bin", "wb") as f:
            for _ in range(1024):
                f.write(b"x" * (1024 * 1024))
        print("wrote 1 GiB — the read-only filesystem did not hold!")
        """)


def breakout() -> str:
    """A battery of escape attempts; every one of them must fail."""
    return textwrap.dedent("""\
        import os
        import socket

        def attempt(name, fn):
            try:
                result = fn()
                print(f"{name}: SUCCEEDED -> {result!r}")
            except Exception as exc:
                print(f"{name}: failed as expected ({type(exc).__name__}: {exc})")

        def read_container_passwd():
            with open("/etc/passwd") as f:
                return f"{len(f.read())} bytes (the CONTAINER's copy — expected)"

        def read_host_secret():
            # Planted by the exercise on the HOST at /host-secret.txt.
            # It does not exist inside the container, so this must fail.
            with open("/host-secret.txt") as f:
                return f.read().strip()

        def list_host_processes():
            return sorted(os.listdir("/proc"))[:10]

        def reach_internet():
            sock = socket.create_connection(("example.com", 80), timeout=3)
            sock.close()
            return "connected to example.com:80"

        def reach_metadata_service():
            # 169.254.169.254 is the cloud metadata address. Reaching it
            # from a sandbox would hand the code your cloud credentials.
            sock = socket.create_connection(("169.254.169.254", 80), timeout=3)
            sock.close()
            return "connected to the metadata service"

        attempt("read /etc/passwd", read_container_passwd)
        attempt("read /host-secret.txt", read_host_secret)
        attempt("list /proc", list_host_processes)
        attempt("reach example.com:80", reach_internet)
        attempt("reach metadata 169.254.169.254", reach_metadata_service)
        print("breakout battery finished")
        """)


def egress_probe() -> str:
    """Probe which hosts are reachable (Exercise 3: the allowlist test)."""
    return textwrap.dedent("""\
        import os
        import urllib.request

        # The sandbox is expected to reach the network only through the
        # allowlist proxy, via the HTTP_PROXY / HTTPS_PROXY variables.
        proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        print(f"proxy in use: {proxy}")

        for host in ("pypi.org", "example.com", "169.254.169.254"):
            url = f"http://{host}/"
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    print(f"{host}: REACHABLE (HTTP {resp.status})")
            except Exception as exc:
                print(f"{host}: blocked ({type(exc).__name__}: {exc})")
        """)


SAMPLES = {
    "benign": benign,
    "memory_hog": memory_hog,
    "cpu_spin": cpu_spin,
    "fork_bomb": fork_bomb,
    "disk_fill": disk_fill,
    "breakout": breakout,
    "egress_probe": egress_probe,
}
