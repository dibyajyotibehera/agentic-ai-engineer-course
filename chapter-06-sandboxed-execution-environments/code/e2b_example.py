"""Run the benign and breakout samples in a managed E2B sandbox.

This is Exercise 6. It is a STUB in one specific sense: it cannot run
without an E2B API key, because E2B is a hosted service — the sandbox
is a Firecracker microVM in E2B's cloud, not something on your
machine. Everything else in the file is a complete, working example:
create a sandbox, run code in it, print the results, kill the sandbox.

Setup (never commit the key)::

    export E2B_API_KEY="e2b_..."
    pip install -r requirements.txt
    python e2b_example.py

What it demonstrates, against Exercise 1:

* The sandbox lifecycle is one call (``Sandbox.create()``) instead of
  a ``docker run`` command with a dozen flags — E2B owns the isolation
  layer, including the microVM boundary.
* ``timeout`` is the sandbox lifetime: E2B kills the sandbox when it
  expires, so a runaway agent cannot leave a sandbox running.
* The sandbox is killed explicitly at the end of the run, and the
  script prints the sandbox identifier so you can confirm it is gone.

See the E2B documentation at https://docs.e2b.dev for the full
software development kit reference, including templates (which define
what environment a sandbox starts with) and pause/resume.
"""

from __future__ import annotations

import os
import sys

from untrusted_samples import SAMPLES

# TODO (you, not the code): nothing is missing here except your key.
# Set E2B_API_KEY in the environment and install the SDK with
# `pip install -r requirements.txt`. There is no local fallback for
# this file on purpose — a managed sandbox either exists or it does
# not, and pretending otherwise would hide the trade-off the exercise
# is about.


def main() -> int:
    api_key = os.environ.get("E2B_API_KEY")
    if not api_key:
        print("E2B_API_KEY is not set.", file=sys.stderr)
        print("Create a key at https://e2b.dev (free tier) and run:", file=sys.stderr)
        print('  export E2B_API_KEY="e2b_..."', file=sys.stderr)
        print("  pip install -r requirements.txt", file=sys.stderr)
        return 2

    try:
        from e2b import Sandbox
    except ImportError:
        print("The `e2b` package is not installed.", file=sys.stderr)
        print("Run: pip install -r requirements.txt", file=sys.stderr)
        return 2

    # timeout=300: the sandbox lives for at most 5 minutes, then E2B
    # kills it whether or not we remembered to. This is the cheapest
    # kill-switch in the chapter — set it before the run starts.
    with Sandbox.create(timeout=300) as sandbox:
        print(f"sandbox created: {sandbox.sandbox_id}")
        for name in ("benign", "breakout"):
            print(f"\n--- {name} (inside the E2B sandbox) ---")
            # Write the code into the sandbox as a file rather than
            # passing multi-line code through a shell string.
            sandbox.files.write(f"/tmp/{name}.py", SAMPLES[name]())
            result = sandbox.commands.run(f"python /tmp/{name}.py")
            print(f"exit code: {result.exit_code}")
            print(result.stdout)
            if result.stderr:
                print(f"stderr: {result.stderr[:1000]}")
        print(f"\nkilling sandbox {sandbox.sandbox_id} (also automatic on exit)")
    print("sandbox killed: the identifier above no longer resolves.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
