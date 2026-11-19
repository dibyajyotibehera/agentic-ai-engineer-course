"""Run untrusted, agent-generated Python code inside a sandbox.

Two execution paths, in decreasing order of strength:

1. Docker (the real sandbox). The code runs in a container built from
   ``Dockerfile`` in this directory, with the controls the lesson
   describes: a memory limit with swap disabled, a CPU limit, a process
   limit, no network, a read-only filesystem, a non-root user, and a
   wall-clock timeout. The container is removed when the run finishes
   (``--rm``) and the image is never given the Docker socket or
   ``--privileged`` mode.

2. Local fallback (a teaching aid only, NOT a security boundary). Used
   automatically when Docker is not installed, or forced with
   ``--no-docker``. It applies the same *resource* limits through the
   standard library ``resource`` module (address-space limit,
   CPU-time limit, process-count limit) and a timeout, but it cannot
   isolate the filesystem or the network: code run this way can still
   read your files and reach the network. Never use the local path for
   code you do not trust; it exists so the exercises can be followed on
   a machine without Docker, and so the limit mechanics can be observed.

Usage (from this directory)::

    python sandbox_runner.py --sample benign --memory 256m --cpus 1.0 --timeout 30
    python sandbox_runner.py --sample memory_hog --memory 256m --cpus 1.0 --timeout 30
    python sandbox_runner.py --sample breakout --runtime runsc --memory 256m --timeout 30
    python sandbox_runner.py --list

Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import resource
import shutil
import subprocess
import sys
import tempfile
import textwrap

from untrusted_samples import SAMPLES

IMAGE = "agent-sandbox:ch06"
DEFAULT_MEMORY = "256m"
DEFAULT_CPUS = "1.0"
DEFAULT_PIDS_LIMIT = 64
SANDBOX_UID = "10001:10001"


def parse_memory(value: str) -> int:
    """Parse a Docker-style memory value (``256m``, ``1g``) into bytes."""
    value = value.strip().lower()
    multipliers = {"b": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}
    if value[-1] in multipliers:
        return int(value[:-1]) * multipliers[value[-1]]
    return int(value)


def docker_available() -> bool:
    return shutil.which("docker") is not None


def run_in_docker(code: str, memory: str, cpus: str, timeout: int,
                  pids_limit: int = DEFAULT_PIDS_LIMIT,
                  network: str = "none", runtime: str = "runc") -> dict:
    """Run *code* in a Docker container and return the observed result.

    The ``docker run`` flags are the sandbox, and each one answers one
    of the five harms in the lesson:

    * ``--memory`` + ``--memory-swap`` equal: hard memory limit, no swap
      escape hatch (availability).
    * ``--cpus``: CPU throttle (availability).
    * ``--pids-limit``: fork-bomb defence (availability).
    * ``--network none``: no egress at all (exfiltration / pivoting).
    * ``--read-only`` + a small writable ``tmpfs`` on /tmp: the image
      filesystem cannot be modified (integrity / persistence).
    * ``--user`` non-root + ``--cap-drop ALL`` + ``--security-opt
      no-new-privileges``: no privilege escalation (confidentiality).
    * ``--rm``: the sandbox is destroyed when the run ends (persistence).
    """
    cmd = [
        "docker", "run", "--rm", "-i",
        "--runtime", runtime,
        "--memory", memory,
        "--memory-swap", memory,  # same value: no swap access at all
        "--cpus", cpus,
        "--pids-limit", str(pids_limit),
        "--network", network,
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--user", SANDBOX_UID,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        IMAGE,
        "python", "-",
    ]
    try:
        proc = subprocess.run(cmd, input=code.encode(),
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return {"backend": f"docker ({runtime})", "killed": "timeout",
                "returncode": None,
                "stdout": (exc.stdout or b"").decode(errors="replace"),
                "stderr": (exc.stderr or b"").decode(errors="replace")}
    return {"backend": f"docker ({runtime})",
            "killed": interpret_exit_code(proc.returncode),
            "returncode": proc.returncode,
            "stdout": proc.stdout.decode(errors="replace"),
            "stderr": proc.stderr.decode(errors="replace")}


def _local_limits(memory_bytes: int, cpu_seconds: int) -> None:
    """Applied in the child process before it execs (local path only)."""

    def _apply() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        except (ValueError, OSError):
            pass  # RLIMIT_NPROC is not enforceable for all users; skip.

    return _apply


def run_local_limited(code: str, memory: str, timeout: int) -> dict:
    """Run *code* in a subprocess with kernel resource limits.

    Teaching aid only: this limits resources, it does NOT isolate the
    filesystem or the network. See the module docstring.
    """
    memory_bytes = parse_memory(memory)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, timeout=timeout,
            preexec_fn=_local_limits(memory_bytes, timeout),
        )
    except subprocess.TimeoutExpired as exc:
        return {"backend": "local (resource limits only — NOT a sandbox)",
                "killed": "timeout", "returncode": None,
                "stdout": (exc.stdout or b"").decode(errors="replace"),
                "stderr": (exc.stderr or b"").decode(errors="replace")}
    return {"backend": "local (resource limits only — NOT a sandbox)",
            "killed": interpret_exit_code(proc.returncode),
            "returncode": proc.returncode,
            "stdout": proc.stdout.decode(errors="replace"),
            "stderr": proc.stderr.decode(errors="replace")}


def interpret_exit_code(returncode: int | None) -> str:
    """Translate an exit code into the limit that (probably) fired."""
    if returncode is None:
        return "unknown"
    if returncode == 137:
        return "out-of-memory kill (exit 137)"
    if returncode < 0:
        return f"killed by signal {-returncode}"
    if returncode != 0:
        return "failed inside the sandbox (see stderr)"
    return "completed normally"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run untrusted sample code in a sandboxed container.")
    parser.add_argument("--sample", default="benign",
                        choices=sorted(SAMPLES),
                        help="which sample from untrusted_samples.py to run")
    parser.add_argument("--list", action="store_true",
                        help="list the available samples and exit")
    parser.add_argument("--memory", default=DEFAULT_MEMORY,
                        help="memory limit, e.g. 256m (default: 256m)")
    parser.add_argument("--cpus", default=DEFAULT_CPUS,
                        help="CPU limit, e.g. 1.0 (Docker path only)")
    parser.add_argument("--timeout", type=int, default=30,
                        help="wall-clock timeout in seconds (default: 30)")
    parser.add_argument("--runtime", default="runc",
                        help="OCI runtime: runc (default) or runsc for gVisor")
    parser.add_argument("--network", default="none",
                        help="Docker network: none (default) or a named network")
    parser.add_argument("--no-docker", action="store_true",
                        help="force the local resource-limited path (not a sandbox)")
    args = parser.parse_args(argv)

    if args.list:
        for name in sorted(SAMPLES):
            print(f"{name}: {SAMPLES[name].__doc__ or ''}".strip())
        return 0

    code = SAMPLES[args.sample]()
    if args.no_docker or not docker_available():
        if not args.no_docker:
            print("Docker not found: using the local resource-limited path.",
                  file=sys.stderr)
            print("This limits resources only — it is NOT a sandbox.",
                  file=sys.stderr)
        result = run_local_limited(code, args.memory, args.timeout)
    else:
        result = run_in_docker(code, args.memory, args.cpus, args.timeout,
                               network=args.network, runtime=args.runtime)

    print(f"backend:    {result['backend']}")
    print(f"sample:     {args.sample}")
    print(f"returncode: {result['returncode']}")
    print(f"outcome:    {result['killed']}")
    print("--- stdout ---")
    print(textwrap.shorten(result["stdout"], width=4000, placeholder=" [...]"))
    print("--- stderr (first 2000 chars) ---")
    print(result["stderr"][:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
