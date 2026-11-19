# Exercises — Chapter 6: Sandboxed Execution Environments

Work these in order: each one builds on the last, and Exercise 2 is the one that matters most, because a sandbox you have watched fail an attack is a sandbox you understand. All exercises assume a Linux host with Docker installed. Where an exercise needs something you do not have — an E2B API key, a Linux host with gVisor — the exercise says so and gives you the local alternative. Nothing here needs a paid service to complete its core acceptance criteria, except Exercise 6, which is explicitly marked as needing an API key.

Concepts used below are defined in the lesson (`README.md` in this directory) and sourced in `references.md`. Full forms are given on first use: a sandbox is an isolated execution environment for untrusted code; a microVM is a lightweight virtual machine; cgroups (control groups) are the Linux kernel mechanism for limiting CPU, memory, input/output and process counts; egress is outbound network traffic leaving the sandbox.

## Exercise 1 — Run untrusted code in a container with limits and no network, and prove the limits hold

**Difficulty:** Foundation

**What you will do.** Build the sandbox image in `code/` and run agent-generated code inside it with CPU and memory limits, no network, a read-only filesystem, a non-root user, and a wall-clock timeout — then prove each limit holds by running code that tries to break it.

**Steps.**

1. Build the image:
   ```bash
   cd code
   docker build -t agent-sandbox:ch06 -f Dockerfile .
   ```
2. Run the benign sample through the runner, which applies the limits for you:
   ```bash
   python sandbox_runner.py --sample benign --memory 256m --cpus 1.0 --timeout 30
   ```
   Read `sandbox_runner.py` first and note which `docker run` flags it sets and why each one is there.
3. Now run each hostile sample in turn and record the outcome for each:
   ```bash
   python sandbox_runner.py --sample memory_hog --memory 256m --cpus 1.0 --timeout 30
   python sandbox_runner.py --sample cpu_spin --memory 256m --cpus 0.5 --timeout 20
   python sandbox_runner.py --sample fork_bomb --memory 256m --cpus 1.0 --timeout 20
   python sandbox_runner.py --sample disk_fill --memory 256m --cpus 1.0 --timeout 20
   ```
4. Verify the limits a second way, without the runner: start a container by hand, inspect it, and check the numbers match what you asked for:
   ```bash
   docker run -d --name probe --rm --memory 256m --memory-swap 256m --cpus 1.0 \
     --pids-limit 64 --network none --read-only --user 10001:10001 \
     agent-sandbox:ch06 sleep 60
   docker inspect probe --format '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}} {{.HostConfig.PidsLimit}}'
   docker stats --no-stream probe
   docker kill probe
   ```
   Confirm that the memory value is 268,435,456 bytes (256 mebibytes), that the CPU value corresponds to 1.0 CPU, and that the process limit is 64.

**Acceptance criteria.**

- The benign sample completes with exit code 0 and its output is returned to the host.
- The memory-hog sample is killed (exit code 137, the out-of-memory kill) and the host stays responsive; `docker stats` during the run never shows the container above the memory limit you set.
- The CPU-spin sample runs to its timeout without starving the host: the host remains usable, and the container's CPU usage stays at or below the limit you set.
- The fork-bomb sample fails with a process-creation error instead of exhausting the host's process table.
- The disk-fill sample fails because the filesystem is read-only (or because the writable `tmpfs` mount fills first — either way, the host disk is untouched).
- You can state, for each flag the runner sets, which of the five harms from the lesson it answers.

## Exercise 2 — Breakout attempt: make the code try to escape, and watch it fail

**Difficulty:** Intermediate

**What you will do.** Run a battery of escape attempts inside the sandbox from Exercise 1 and record, for each one, the exact failure you observe. A sandbox is only trustworthy when you have seen its boundary hold.

**Steps.**

1. Run the breakout battery:
   ```bash
   cd code
   python sandbox_runner.py --sample breakout --memory 256m --cpus 1.0 --timeout 30
   ```
   The `breakout` sample tries, in order: reading `/etc/passwd` on the host (it will read the *container's* copy — note the difference and say why that is expected), reading a planted host secret via a path that does not exist inside the container, listing the host's process tree, reaching the network, and reaching the cloud metadata address `169.254.169.254`.
2. Then try the two escapes that the sandbox *must* refuse by configuration, not by luck. First, the Docker socket escape — run a container with the host Docker socket mounted and show what it can do, then explain why you must never do this for agent code:
   ```bash
   docker run --rm -v /var/run/docker.sock:/var/run/docker.sock docker:latest docker ps
   ```
   **Do this only on a disposable host or virtual machine you can rebuild.** It demonstrates that socket access is root-equivalent access to the host: from inside that container you can start a privileged container and mount the host root filesystem.
3. Second, run the same breakout battery with `--privileged` added and record what changes. Then write down, in one paragraph, why `--privileged` and the Docker socket are the two flags that turn a sandbox into a doorway.

**Acceptance criteria.**

- Every breakout attempt inside the properly configured sandbox fails, and for each one you can quote the error or the empty result that proves it (not just "it didn't work" — the actual output).
- You can explain why reading `/etc/passwd` *inside* the container succeeding is not a breakout, and what file you would plant on the host to prove the boundary (the sample plants `/host-secret.txt` outside the container; the code must not be able to read it).
- You can explain, from the socket experiment, why mounting `/var/run/docker.sock` into an agent sandbox is equivalent to giving the agent root access on the host.
- You have written down the one-sentence rule you will apply in code review: any `docker run` for agent code that contains `--privileged` or a `/var/run/docker.sock` mount is rejected.

## Exercise 3 — Egress allowlist: only one host reachable

**Difficulty:** Intermediate

**What you will do.** Give the sandbox a network that leads exactly one place: through an allowlist proxy that permits a single host and refuses everything else. This is the pattern you use when an agent run genuinely needs the network — to install a package, fetch a dataset, or call one API — and must not be able to phone home anywhere else.

**Steps.**

1. Start the allowlist proxy from `code/egress_proxy.py` on the host. It permits only `pypi.org` (the Python Package Index) and refuses every other host:
   ```bash
   cd code
   python egress_proxy.py --allow pypi.org --port 8899 &
   ```
2. Build and run a container on an internal network with no direct route out, with the proxy as its only egress. The exact commands are in the header comment of `egress_proxy.py`; in outline: create the network with `docker network create --internal`, run the sandbox on it with the proxy address passed in as an environment variable, and run the `egress_probe` sample, which tries `pypi.org`, `example.com`, and the metadata address `169.254.169.254`.
3. Observe which request succeeds and which two fail, and read the proxy's log to confirm the refusals were enforced by the proxy, not by the code giving up.
4. Change the allowlist to a different single host, restart the proxy, and confirm the previously allowed host now fails and the new one succeeds. This proves the policy lives in the proxy configuration, not in the sandbox image.

**Acceptance criteria.**

- The request to the allowed host succeeds and the requests to every other host fail, including the metadata address.
- The proxy log shows the allowed request and the refused requests.
- After changing the allowlist, the set of reachable hosts changes with it — proving the control is the proxy, not the code.
- You can explain why the allowlist must be enforced outside the sandbox, and what goes wrong if the sandbox itself is allowed to choose its own DNS (Domain Name System) resolver.

## Exercise 4 — The same sandbox under gVisor

**Difficulty:** Advanced

**What you will do.** Re-run Exercises 1 and 2 with the `runsc` runtime, and record what changes and what breaks.

**Steps.**

1. Install gVisor's `runsc` runtime by following the gVisor documentation linked in `references.md` (the security introduction and the networking guide are the starting points). This needs a Linux host; it does not work on macOS or Windows without a Linux virtual machine.
2. Register the runtime with Docker and re-run the breakout battery:
   ```bash
   docker run --rm --runtime=runsc --memory 256m --cpus 1.0 --network none \
     agent-sandbox:ch06 python -c "import os; print(os.uname())"
   python sandbox_runner.py --sample breakout --runtime runsc --memory 256m --cpus 1.0 --timeout 30
   ```
   (The `--runtime` flag is supported by `sandbox_runner.py`.)
3. Run `dmesg` inside the container under both runtimes and record the difference. Under gVisor you will see gVisor's own startup messages, not the host kernel log — and note the gVisor documentation's warning that an attacker can fake this output, so it is a smoke test, never a security check.
4. Run your real toolchain inside the gVisor sandbox — the actual packages and system calls your agent's generated code uses — and record anything that fails or slows down noticeably. System-call-heavy work is where the cost of gVisor shows up.

**Acceptance criteria.**

- The breakout battery passes under `runsc` exactly as it did under `runc`.
- You have a written note of at least one workload difference you measured yourself (a program that failed, a test that slowed down, or an explicit "no difference observed on my workload"), labelled as your own measured result.
- You can state the decision rule you would use in production: which of your agent workloads get `runsc` and which stay on `runc`, and why.

## Exercise 5 — Design the sandbox policy for a real agent

**Difficulty:** Advanced

**What you will do.** No code to run; this is the design exercise, and it is the closest to the design-review question in the lesson. Write a one-page sandbox policy for a coding agent that serves untrusted users (not your own team), covering the five harms from the lesson.

**Steps.**

1. Pick a concrete scenario: a multi-tenant coding agent where each user's agent runs Python, installs packages from the Python Package Index, and must never see another user's files or your cloud credentials.
2. Write the policy as a short document (about one page) answering: which isolation mechanism you choose and why; the exact resource limits (memory, CPU, process count, wall-clock timeout, disk) and how you picked the numbers; the egress policy (default-deny, and the exact allowlist); the filesystem and identity rules (user, read-only mounts, what is mounted in and what is never mounted); the lifecycle (how long a sandbox lives, what is copied out, how it is destroyed); and the observability (what you log, and which log line tells you the sandbox is being attacked).
3. Include the failure case: what happens when a sandbox is compromised anyway — which credentials you rotate, which tenants you notify, and how you prove the blast radius was one sandbox.

**Acceptance criteria.**

- Every one of the five harms has a named control, and every control has a verification step (a probe, a log line, or a test) rather than an assertion.
- The policy names at least one mechanism you deliberately did *not* choose, and why.
- The policy fits on one page and could be handed to a reviewer who has not read this chapter.

## Exercise 6 — Run the same agent code in a managed microVM sandbox (E2B)

**Difficulty:** Intermediate — needs an E2B API key (free tier); the core exercises above do not need it.

**What you will do.** Take the benign sample and the breakout sample and run them in an E2B sandbox, to feel the difference between operating the boundary yourself and renting it.

**Steps.**

1. Create an account at E2B and take an API key. Set it as an environment variable — never write it into a file that gets committed:
   ```bash
   export E2B_API_KEY="e2b_..."
   pip install -r requirements.txt
   ```
2. Run the example:
   ```bash
   cd code
   python e2b_example.py
   ```
   It creates a sandbox, runs the benign sample and the breakout sample inside it, prints both results, and kills the sandbox.
3. Compare the experience with Exercise 1: what did E2B do for you (lifecycle, timeout, isolation), what did you give up (control, cost visibility, data leaving your infrastructure), and at what scale would you switch from Docker on your own host to E2B?

**Acceptance criteria.**

- Both samples run in the E2B sandbox and the sandbox is killed at the end of the run (confirm this by checking that the sandbox identifier no longer resolves — the script prints how).
- The breakout sample fails inside the E2B sandbox exactly as it failed in your Docker sandbox.
- You have written down the scale or regulatory trigger at which you would move this workload from self-hosted Docker to a managed sandbox, or from a managed sandbox back to self-hosted — with a reason, not just a preference.
