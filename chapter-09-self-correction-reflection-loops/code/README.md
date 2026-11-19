# Chapter 9 — Self-Correction & Reflection Loops: code

All four modules use only the Python standard library, so there are no
dependencies to install. Python 3.11 or later is required.

## Files

| File | What it is |
| --- | --- |
| `traceback_parser.py` | Parses a raw Python traceback into a structured diagnosis: exception type, deepest user frame, likely cause, suggested fix, and a stable failure signature. |
| `critic_agent.py` | A maker–checker loop: a maker drafts code, a test-suite critic returns a pass/fail verdict with specific findings, and the maker revises for a bounded number of rounds. |
| `self_healing_loop.py` | The full self-healing loop: generate, execute, verify, and stop — with a hard retry budget, exponential backoff, repeat-failure detection, and a fail-closed diagnosis report. |
| `loop_watchdog.py` | A separate observer for a loop: state hashing, a labelled proxy for semantic repetition, evaluator-score progress tracking, and a continue / warn / stop verdict with recorded reasons. Tests in `test_loop_watchdog.py`. |
| `test_loop_watchdog.py` | Tests for `loop_watchdog.py`, standard library only (`unittest`): `python3 test_loop_watchdog.py`. |

## Running them

```bash
python traceback_parser.py
python critic_agent.py
python self_healing_loop.py
python loop_watchdog.py
python test_loop_watchdog.py
```

Each module prints a demo when run directly. The demos use scripted
stand-ins for the language-model components (a fixed list of candidate
programs), so everything runs with no model and no API key. The `TODO`
comments in `self_healing_loop.py` and `critic_agent.py` mark exactly where
a real model call plugs in; the loop and stopping-rule logic does not change
when you make that swap.

## Safety

The demos execute generated code in-process with `exec` for readability.
Do not do that with untrusted code in production: run agent-generated code
in a sandbox with resource limits and no network egress, as built in
Chapter 6 of this course.
