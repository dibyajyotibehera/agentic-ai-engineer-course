# Chapter 9 — Self-Correction & Reflection Loops: code

All modules use only the Python standard library, so there are no
dependencies to install. Python 3.11 or later is required.

Two of the modules call a real language model, and need a configured
model backend to run their demos — connect a model as described in
the root README's "Connect a model" section (key-based, or AWS
Bedrock). Without a backend
the model-backed demos fail loudly instead of pretending with a
scripted stand-in. `traceback_parser.py` and the first three demos of
`loop_watchdog.py` need no model and no key.

## Files

| File | What it is |
| --- | --- |
| `llm_client.py` | The shared client for a model (copied verbatim from Chapter 7, so the chapter is self-contained): key-based or AWS Bedrock configuration, retries, structured JSON replies, token and cost totals. |
| `traceback_parser.py` | Parses a raw Python traceback into a structured diagnosis: exception type, deepest user frame, likely cause, suggested fix, and a stable failure signature. Deterministic; no model. |
| `critic_agent.py` | A maker–checker loop: a real model maker drafts and revises code, a test-suite critic returns a pass/fail verdict with specific findings, and a real model critic gives a second opinion from the same test evidence. The loop acts only on the test suite's verdict. |
| `self_healing_loop.py` | The full self-healing loop with a real model generator: generate, execute, verify, and stop — with a hard retry budget, exponential backoff, repeat-failure detection, and a fail-closed diagnosis report. |
| `loop_watchdog.py` | A separate observer for a loop: state hashing, a labelled proxy for semantic repetition, evaluator-score progress tracking, and a continue / warn / stop verdict with recorded reasons. Demos (a)–(c) run with no model; demo (d) watches a real critic loop live. Tests in `test_loop_watchdog.py`. |
| `test_loop_watchdog.py` | Tests for `loop_watchdog.py`, standard library only (`unittest`): `python3 test_loop_watchdog.py`. |
| `test_critic_and_healing.py` | Tests for `critic_agent.py` and `self_healing_loop.py`: deterministic tests that need no model, plus live tests that need a configured model backend and skip otherwise. `python3 -m unittest test_critic_and_healing -v`. |

## Running them

```bash
python traceback_parser.py
python loop_watchdog.py
python critic_agent.py
python self_healing_loop.py
# Or, on the Bedrock path:
LLM_BACKEND=bedrock python critic_agent.py
LLM_BACKEND=bedrock python self_healing_loop.py
python test_loop_watchdog.py
python -m unittest test_critic_and_healing test_loop_watchdog -v
```

Each module prints a demo when run directly. The critic demo starts
from a known-buggy median draft, the test-suite critic fails it 2 of
7, the model maker revises it to 7 of 7 on the second round, and the
model critic agrees with the test suite on both rounds. The healing
demo shows all three endings: a repair that succeeds in 2 attempts,
a verifier that never accepts (stopped after 2 attempts on a repeated
failure signature), and a missing dependency (unrepairable on the
first attempt). The watchdog's live demo (d) observes the real critic
loop and lets the converging repair through with continue verdicts.

Measured on the runs above, on the configured model: the
critic demo used 1,307 prompt and 1,059 completion tokens ($0.0015),
the healing demo 936 prompt and 1,275 completion tokens ($0.0016),
and the watchdog's live demo 344 prompt and 248 completion tokens
($0.0003). The whole chapter's live behaviour costs a few tenths of
a cent per run.

## What is still a stand-in, and why

Two deliberate stand-ins remain, and both are labelled where they
live rather than hidden:

* The watchdog's `_semantic_similarity` in `loop_watchdog.py` is a
  *proxy* for embedding similarity — token overlap plus a normalised
  edit distance — not a real embedding. Its docstring states exactly
  what it can and cannot tell apart, and a `TODO` marks the swap
  point (cosine similarity over real embeddings, same signature and
  threshold semantics). It stays because the watchdog's verdict logic
  is the thing being studied, the proxy lets that logic be exercised
  deterministically with no embedding endpoint, and a detector that
  does not know its own blind spots would be trusted exactly where it
  should be doubted.
* The scripted makers and generators in `test_critic_and_healing.py`
  are *test doubles*, not shipped behaviour: they exist only in the
  test file, so the loops' deterministic logic — rounds, stopping
  rules, fail-closed reports — can be tested with no model and no
  key. The shipped modules contain no scripted maker or generator;
  their demos call a real model and fail loudly without a backend.

The demos also start from a known-buggy first draft or program
(`DRAFT_BUGGY` in `critic_agent.py`, `BUGGY_MEDIAN` in
`self_healing_loop.py`), passed in as the loop's starting point so
the repair path is exercised on every run instead of depending on
the model making the same mistake first. The repair itself is a real
model call; the starting draft is a human's first attempt handed to
the loop, not a stand-in for the model.

## Safety

The demos execute generated code in-process with `exec` for readability.
Do not do that with untrusted code in production: run agent-generated code
in a sandbox with resource limits and no network egress, as built in
Chapter 6 of this course.
