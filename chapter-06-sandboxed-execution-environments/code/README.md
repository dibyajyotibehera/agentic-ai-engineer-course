# Code — Chapter 6: Sandboxed Execution Environments

## Files

| File | What it is |
| --- | --- |
| `sandbox_runner.py` | Runs a sample program in a Docker container with limits and no network — or, when Docker is absent, in a local subprocess with kernel resource limits (teaching aid only, **not** a sandbox). Standard library only. |
| `untrusted_samples.py` | The sample programs: one benign workload, plus a memory hog, CPU spin, fork bomb, disk fill, breakout battery, and egress probe. |
| `egress_proxy.py` | An allowlist forward proxy (standard library only) for Exercise 3. Forwards only hosts on the allowlist; everything else gets HTTP 403. |
| `e2b_example.py` | Runs the benign and breakout samples in a managed E2B microVM sandbox. Needs `E2B_API_KEY` (Exercise 6); see `requirements.txt`. |
| `webcontainer_boot.mjs` | Boots a WebContainer in the browser and runs agent-generated Node.js code inside it. Browser example — needs `npm install @webcontainer/api` and cross-origin isolation headers. |
| `Dockerfile` | Minimal sandbox image (`python:3.12-slim`, non-root user). Build with `docker build -t agent-sandbox:ch06 -f Dockerfile .` |
| `Dockerfile.dind` | Docker-in-Docker image for trusted CI jobs only — never for agent code. See its header comment. |
| `test_sandbox_runner.py` | Tests for the runner's local path (resource limits, timeout, exit-code interpretation). Docker-path tests are skipped automatically when Docker is absent. |

## Quick start

```bash
docker build -t agent-sandbox:ch06 -f Dockerfile .
python sandbox_runner.py --sample benign --memory 256m --cpus 1.0 --timeout 30
python sandbox_runner.py --sample breakout --memory 256m --cpus 1.0 --timeout 30
python -m unittest test_sandbox_runner.py -v
```

Docker is not installed in every environment: without it, the runner
falls back to the local resource-limited path and says so. That path
limits CPU, memory and process count, but it does **not** isolate the
filesystem or the network — do not use it for code you do not trust.
