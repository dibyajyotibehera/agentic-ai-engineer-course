# Agentic AI Engineer Course

A hands-on course for building production-grade AI agents, in the style of [the LLM course](https://github.com/mlabonne/llm-course): each chapter is a lesson with theory, runnable code, exercises and references. It is written for an advanced practitioner — an experienced engineer with production agentic-AI experience — not a beginner.

## Who this course is for

You have shipped an agent to production, or you are about to. You know what a tool call is, you have watched an agent loop go wrong at 2 a.m., and you want the engineering judgement behind the patterns: when a directed acyclic graph beats a free-form loop, how to scope a tool so it cannot hurt you, what a memory layer costs you per task, and how to prove in continuous integration that a prompt change did not make things worse. If you are starting from zero, work through the [LLM course](https://github.com/mlabonne/llm-course) first — this course assumes its Agents, Evaluation, Deployment and Securing LLMs sections as background.

## How the course is structured

Twelve chapters. Each chapter is a directory with the same four parts:

| File | What it is |
| --- | --- |
| `README.md` | The lesson: the idea in plain words, the mechanism, the numbers that matter, and the advanced-level interview angle |
| `references.md` | Every source the lesson draws on, with a real, verified link |
| `exercises.md` | Hands-on exercises with clear acceptance criteria and a stated difficulty |
| `code/` | Starter code — Python notebooks and scripts in the style of the LLM course, runnable where feasible |

Work the chapters in order: each one builds on the last, and Chapter 12 is a capstone that pulls the whole course together into a public teardown of a real agent you have built.

## The chapters

| # | Chapter | What you will be able to do |
| --- | --- | --- |
| 01 | [Agent Architecture & State Machines](chapter-01-agent-architecture-state-machines/) | Choose between directed acyclic graphs, cyclic workflows and free-form loops; design state that survives a crash |
| 02 | [Tool Use & MCP](chapter-02-tool-use-mcp/) | Build a raw Model Context Protocol server from scratch; scope tools with least-privilege permissions |
| 03 | [Computer Use & GUI Automation](chapter-03-computer-use-gui-automation/) | Drive a screen with screenshots and the Document Object Model; ground clicks reliably with Set-of-Mark prompting |
| 04 | [Advanced Memory & Context Engineering](chapter-04-advanced-memory-context-engineering/) | Build a three-tier memory system; budget tokens across working, episodic and semantic memory |
| 05 | [Multi-Agent Swarms & A2A Handoffs](chapter-05-multi-agent-swarms-a2a/) | Design supervisor topologies and Agent-to-Agent handoffs with typed state passing and conflict resolution |
| 06 | [Sandboxed Execution Environments](chapter-06-sandboxed-execution-environments/) | Run agent code in isolation with resource limits and blocked network egress |
| 07 | [Inter-Agent Security & Guardrails](chapter-07-inter-agent-security-guardrails/) | Defend against indirect prompt injection and tool poisoning; build a sanitisation proxy |
| 08 | [Human-in-the-Loop & Breakpoints](chapter-08-human-in-the-loop-breakpoints/) | Pause a running agent durably, route approvals through webhooks and chat, and keep a complete audit trail |
| 09 | [Self-Correction & Reflection Loops](chapter-09-self-correction-reflection-loops/) | Build critic agents, traceback analysis and self-healing execution that knows when to stop |
| 10 | [Trajectory Evals & CI Gates](chapter-10-trajectory-evals-ci-gates/) | Grade multi-step runs step by step and block regressions in continuous integration |
| 11 | [Graceful Degradation & Fallbacks](chapter-11-graceful-degradation-fallbacks/) | Keep an agent useful when a model, a tool or a budget fails, with circuit breakers and cost kill-switches |
| 12 | [Public Agent Teardowns & Portfolios](chapter-12-public-agent-teardowns-portfolios/) | Tear down a real agent in public: architecture decision records, cost-per-task analysis and latency profiling |

## Setup

Python 3.11 or later. Each chapter's `code/` directory lists its own dependencies; most chapters run with nothing more than the standard library plus a small set of packages. Where a chapter needs a model API key (for example to call a hosted model), the code is written so it runs against a mock or a local model first, and the exercise says so explicitly. No chapter requires a paid service to complete its core exercise.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # per-chapter requirements live in each chapter's code/ directory
```

## Where this course comes from

A research pass over GitHub and the major course platforms (18 November 2026) found that no existing course covers even half of these twelve topics at advanced depth. The best base found was [Microsoft's AI Agents for Beginners](https://github.com/microsoft/ai-agents-for-beginners) (MIT licence, 18 lessons), which this course adapts with attribution where its material is used; the full conversion plan — what is adapted from where, and what is original writing — is in [CONVERSION_PLAN.md](CONVERSION_PLAN.md). Roughly half of this course is adapted material raised to advanced depth, and roughly half is original, because the advanced half of every topic, and the whole of Chapters 6, 11 and 12, had no adequate source anywhere.

## A note on the stack

The code defaults to Python, in the style of the LLM course. The reference stack in the lessons is framework-neutral where it can be, and where a concrete stack helps, the mapping to Mastra on Amazon Web Services Elastic Container Service with Amazon Bedrock is noted, since that is the production stack the course was built around. LangGraph appears in Chapter 1 as the canonical graph framework, and Temporal.io appears alongside it as the durable-execution alternative — the course teaches the pattern, not the vendor.

## Contributing

This is a learning repository. Corrections to facts, links and code are welcome as pull requests; every number, benchmark and price in the course must trace to a cited source, so please include the source with any correction.

## Licence

This course is released under the MIT Licence — see [LICENSE](LICENSE). Material adapted from Microsoft's AI Agents for Beginners is used under its MIT Licence with attribution in [CONVERSION_PLAN.md](CONVERSION_PLAN.md) and in the relevant chapters.

## Pushing to GitHub

This repository was built locally and has not been pushed anywhere. Once a GitHub account is connected, publish it with:

```bash
cd agentic-ai-engineer-course
git remote add origin git@github.com:<your-username>/agentic-ai-engineer-course.git
git branch -M main
git push -u origin main
```

Replace `<your-username>` with your GitHub username, or use an HTTPS remote (`https://github.com/<your-username>/agentic-ai-engineer-course.git`) if you prefer to authenticate that way.
