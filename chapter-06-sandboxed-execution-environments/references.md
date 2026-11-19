# References — Chapter 6: Sandboxed Execution Environments

Every link below was verified live on 18 November 2026 with a web search or a page fetch. No URL here was invented or guessed. Where a page is a versioned snapshot (for example a dated SDK reference), the link is given exactly as returned by the search.

## The syllabus inspiration (proprietary — shape only, nothing copied)

- DeepLearning.AI, *Building Coding Agents with Tool Execution* — the lesson on tool execution environments (local execution, containers, and microVM sandboxes): https://learn.deeplearning.ai/courses/building-coding-agents-with-tool-execution/lesson/i11do3/tool-execution-environments
- DeepLearning.AI, *Building Coding Agents with Tool Execution* — course overview (built in partnership with E2B; 1 hour 21 minutes, intermediate): https://learn.deeplearning.ai/courses/building-coding-agents-with-tool-execution/lesson/gyy74j/your-first-coding-agent

## E2B and managed microVM sandboxes

- E2B Documentation (home page — what E2B is, the Sandbox / Template / Persistence building blocks, quickstarts and the SDK reference): https://docs.e2b.dev
- E2B machine-readable documentation index: https://e2b.dev/llms.txt
- E2B — customising the Code Interpreter template (build a template on top of `code-interpreter-v1`): https://docs.e2b.dev/code-interpreting/customize-template
- E2B Python SDK reference, Sandbox (synchronous): https://docs.e2b.dev/sdk-reference/python-sdk/v2.61.0/sandbox_sync
- E2B on PyPI (`e2b` package): https://pypi.org/project/e2b/2.51.0/

## Firecracker and microVMs

- Firecracker project README (what Firecracker is, the virtual machine monitor built on Linux KVM, its use in AWS Lambda and AWS Fargate, Apache 2.0 licence): https://github.com/firecracker-microvm/firecracker/blob/HEAD/README.md
- Kata Containers on Kubernetes (each pod in its own lightweight virtual machine, selected with `runtimeClassName: kata`): https://documentation.suse.com/cloudnative/rke2/latest/en/add-ons/kata.html

## gVisor

- gVisor — *Introduction to gVisor security* (the Sentry, why gVisor never passes a system call through to the host, the systrap and KVM platforms, and what gVisor does *not* protect against): https://gvisor.dev/docs/architecture_guide/intro/
- gVisor — *Networking* (gVisor's userspace network stack, netstack, and disabling external networking with `--network=none`): https://gvisor.dev/docs/user_guide/networking/
- gVisor — *Checkpoint/Restore* (checkpointing a running sandbox and restoring it, including under Docker): https://gvisor.dev/docs/user_guide/checkpoint_restore/

## WebContainers

- WebContainers API Reference (the public WebContainer API: `boot`, `mount`, `spawn`, `fs`, `teardown`; cross-origin isolation requirements): https://webcontainers.io/api

## Docker, containers and resource limits

- Docker Docs — *Resource constraints* (the default of no limits, hard versus soft memory limits, `--memory-swap`, the CFS scheduler, `--cpus`, and the 6-megabyte minimum memory value): https://docs.docker.com/engine/containers/resource_constraints/
- Docker run reference (network settings, including `--network none` disabling all incoming and outgoing networking): https://github.com/Shopify/docker/blob/master/docs/reference/run.md

## Docker-in-Docker

- *Understanding Docker-in-Docker: Power, Risks, and Safer Alternatives* — the two patterns (true DinD versus Docker socket binding) and why socket access is root-equivalent: https://tiendu.github.io/2025/04/18/dind.html
- *Docker in Docker Security: Risks, Alternatives, and Safe Implementation Guide 2026* — privileged mode, socket risks, and safer alternatives: https://bckinfo.com/docker-in-docker-security-risks-alternatives-and-safe-implementation-guide-2026/

## Linux primitives underneath it all

- Linux kernel documentation — *Control Group v2* (the authoritative reference for the unified cgroup hierarchy and the CPU, memory, input/output and process-number controllers): https://docs.kernel.org/admin-guide/cgroup-v2.html

## Course style model

- *LLM course* (the style model for this whole course — chapter shape, hands-on notebooks, references): https://github.com/mlabonne/llm-course

## What was not verified

- The gVisor Docker quick-start page (`gvisor.dev/docs/user_guide/quick_start/docker/`) was cited in a secondary source during research, but a live fetch of it hit a rate limit on 18 November 2026 and it is therefore **not** listed above as verified. Use the gVisor security introduction and networking guide above instead, and re-check the quick-start URL yourself before relying on it.
- No performance numbers (startup times, overhead percentages, prices) are quoted anywhere in this chapter, because no such number was verified against a primary source during research. Any number you measure yourself with the exercises in this chapter is your own measured result — label it as such.
