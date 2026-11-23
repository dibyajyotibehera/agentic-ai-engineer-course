"""Self-healing execution: run, diagnose, repair, and — crucially — stop.

The loop in this module is the chapter's central pattern. It takes a task, a
code generator and a verifier, and it keeps trying until the code passes or
until one of three stopping rules fires:

1. The retry budget is exhausted (a hard cap on attempts).
2. The same failure signature appears twice (no new information is being
   produced, so another retry would be superstition, not engineering).
3. The failure is classified as unrepairable by retrying (for example a
   missing dependency or a permission error — retrying cannot fix those).

When any rule fires, the loop fails *closed*: it returns a diagnosis report
containing every attempt, every failure signature and a suggested next step,
instead of looping forever or silently returning a wrong answer.

The generator is a real language-model call (``LLMGenerator``, through
``llm_client.LLMClient``, the same client Chapter 7 uses). Its prompt is
built from the task, the last diagnosis (exception type, deepest user
frame, suggested fix) and the list of failure signatures already seen,
and it is instructed never to repeat a failed program verbatim and to
change only what the diagnosis names. The executor, the traceback
parser, the failure classifier and the stopping rules are all
deterministic and local — the model is only ever the generator, never
the judge of its own work. That division is what keeps the loop honest:
execution and verification are the external signals, and the model
supplies only the next attempt.

Running the demo needs a configured model backend — ``LLM_API_KEY``
(or ``LLM_BACKEND=opencheap-connector`` on the author's machine, as
in Chapter 7). Without one it fails loudly instead of pretending
with a scripted generator. The deterministic behaviour of the loop
itself — budgets, stopping rules, fail-closed reports — is covered by
the tests in ``test_critic_and_healing.py``, which drive the loop with
a small scripted generator defined in that file, so the tests need no
model and no key.

Safety note: the executor runs code in-process with ``exec`` for the sake of
a runnable demo. Production code must run agent-generated code in a sandbox
(Chapter 6 of this course) with resource limits and no network egress.
Never point this executor at untrusted code.

Run the module directly to see three endings — a repair that succeeds, a
repair that fails closed on a repeated signature, and a failure that is
unrepairable on the first attempt:

    python self_healing_loop.py
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol

from llm_client import LLMClient, LLMError
from traceback_parser import TracebackDiagnosis, diagnose_exception, parse_traceback

# Generous enough for a short program: this model spends part of its
# token budget on hidden reasoning, and a small budget can be spent
# entirely on reasoning, leaving an empty reply (see Chapter 7).
_GENERATOR_MAX_TOKENS = 800

_GENERATOR_SYSTEM = """You are a Python programmer. Reply with exactly one fenced python code block \
containing the complete program and nothing else — no explanation, no extra code."""

_GENERATOR_PROMPT = """Write a Python program for this task:

{task}

{context}
The program must print its result to standard output.
"""

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)\n?```", re.DOTALL)


def _extract_code(reply: str) -> str:
    """Return the Python code from a model reply.

    Prefers the first fenced code block; falls back to the whole reply
    when it already looks like code. Raises instead of returning a
    plausible-looking fragment, because a half-extracted program would
    fail execution for the wrong reason and teach the loop nothing.
    """
    match = _CODE_BLOCK_RE.search(reply)
    if match:
        return match.group(1).strip() + "\n"
    stripped = reply.strip()
    if stripped.startswith(("def ", "import ", "from ", "print(")):
        return stripped + "\n"
    raise LLMError(f"Model reply contained no Python code block: {reply[:300]}")


class FailureKind(Enum):
    """What kind of failure the loop is looking at."""

    TRANSIENT = "transient"        # Retry the same code: timeouts, connections.
    REPAIRABLE = "repairable"     # Retry with the diagnosis in context.
    SYNTAX = "syntax"             # The code does not parse; repair the text.
    UNREPAIRABLE = "unrepairable"  # Retrying cannot help: stop now.


_TRANSIENT_TYPES = {"TimeoutError", "ConnectionError", "ConnectionResetError"}
_UNREPAIRABLE_TYPES = {
    "ModuleNotFoundError",  # A dependency is missing; install it, don't retry.
    "PermissionError",      # The environment forbids the operation.
    "MemoryError",          # Retrying the same work will exhaust memory again.
    "KeyboardInterrupt",
}


def classify_failure(diagnosis: TracebackDiagnosis) -> FailureKind:
    """Classify a diagnosed failure by what retrying can do about it."""
    exc_type = diagnosis.exception_type.split(".")[-1]
    if exc_type in _TRANSIENT_TYPES:
        return FailureKind.TRANSIENT
    if exc_type in _UNREPAIRABLE_TYPES:
        return FailureKind.UNREPAIRABLE
    if exc_type in ("SyntaxError", "IndentationError"):
        return FailureKind.SYNTAX
    return FailureKind.REPAIRABLE


@dataclass
class RetryBudget:
    """The hard limits on a healing loop. All three must be set."""

    max_attempts: int = 3
    base_delay_s: float = 0.2
    backoff_factor: float = 2.0
    max_delay_s: float = 5.0
    # Total wall-clock budget for the whole loop, in seconds. A loop that is
    # bounded in attempts but not in time can still run forever if each
    # attempt is slow.
    max_total_s: float = 60.0

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff with a cap; attempt 1 waits base_delay_s."""
        return min(self.base_delay_s * (self.backoff_factor ** (attempt - 1)), self.max_delay_s)


@dataclass
class AttemptRecord:
    attempt: int
    code: str
    outcome: str  # "passed", "failed", "syntax_error", "skipped"
    diagnosis: TracebackDiagnosis | None = None
    failure_kind: FailureKind | None = None


@dataclass
class HealingReport:
    """Everything a human (or the next agent) needs after the loop ends."""

    task: str
    succeeded: bool
    attempts: list[AttemptRecord] = field(default_factory=list)
    stop_reason: str = ""
    final_code: str | None = None
    final_output: str | None = None

    def __str__(self) -> str:  # pragma: no cover - formatting helper
        lines = [
            f"Task: {self.task}",
            f"Outcome: {'SUCCEEDED' if self.succeeded else 'FAILED CLOSED'} "
            f"after {len(self.attempts)} attempt(s)",
            f"Stop reason: {self.stop_reason}",
        ]
        for record in self.attempts:
            lines.append(f"  Attempt {record.attempt}: {record.outcome}")
            if record.diagnosis is not None:
                lines.append(f"    {record.diagnosis.exception_type}: "
                             f"{record.diagnosis.message}")
                frame = record.diagnosis.deepest_user_frame
                if frame is not None:
                    lines.append(f"    at {frame.filename}:{frame.lineno} "
                                 f"in {frame.function}")
        if self.final_code is not None:
            lines.append("Final code:")
            lines.append(self.final_code)
        return "\n".join(lines)


class Generator(Protocol):
    """What the loop needs from a generator, model or otherwise."""

    def generate(self, task: str, history: list[AttemptRecord]) -> str: ...


class LLMGenerator:
    """A real language-model code generator.

    The prompt carries the diagnosis, not the raw trace — the
    exception type, the deepest user frame, the suggested fix — plus
    the failure signatures already seen, so the model can be told
    explicitly never to repeat a program that has already failed.
    Only the last two attempts are carried forward, following
    Reflexion's cap on episodic memory: context is finite, and a
    loop that appends every failure to the prompt drowns in its own
    history.
    """

    def __init__(self, client: LLMClient, first_code: str | None = None) -> None:
        self.client = client
        # ``first_code`` starts the loop from an existing program — a
        # human's first attempt, or a draft from an earlier run —
        # instead of asking the model for one. The demo uses it to
        # start from a known-buggy program, so the repair path is
        # exercised on every run instead of depending on the model
        # making the same mistake first. The repair itself is still a
        # real model call.
        self._first_code = first_code

    def generate(self, task: str, history: list[AttemptRecord]) -> str:
        if not history and self._first_code is not None:
            return self._first_code
        context = self._context_for(history)
        reply = self.client.complete(
            [
                {"role": "system", "content": _GENERATOR_SYSTEM},
                {"role": "user", "content": _GENERATOR_PROMPT.format(
                    task=task, context=context)},
            ],
            temperature=0.0,
            max_tokens=_GENERATOR_MAX_TOKENS,
        )
        return _extract_code(reply)

    @staticmethod
    def _context_for(history: list[AttemptRecord]) -> str:
        if not history:
            return "This is the first attempt."
        lines = ["Previous attempts (most recent last):"]
        for record in history[-2:]:
            lines.append(f"Attempt {record.attempt} ({record.outcome}):")
            lines.append("```python")
            lines.append(record.code.rstrip())
            lines.append("```")
            if record.diagnosis is not None:
                lines.append("Diagnosis:")
                lines.append(record.diagnosis.summary())
        signatures = sorted(
            {str(record.diagnosis.signature)
             for record in history if record.diagnosis is not None}
        )
        if signatures:
            lines.append(
                "Failure signatures already seen — do not produce a program "
                f"that fails the same way: {', '.join(signatures)}"
            )
        lines.append(
            "Change only what the diagnosis names. Do not repeat a failed "
            "program verbatim."
        )
        return "\n".join(lines)


def _execute(code: str) -> tuple[str, str | None, TracebackDiagnosis | None]:
    """Execute code, returning (outcome, captured stdout, diagnosis).

    The code is parsed first, so a syntax error is reported by the compiler
    without any of the code running — partial execution of a broken program
    is how self-healing loops corrupt state.
    """
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return "syntax_error", None, diagnose_exception(exc)

    namespace: dict = {}
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            exec(compile(code, "<agent_code>", "exec"), namespace)  # noqa: S102
    except Exception as exc:  # noqa: BLE001 - the loop must catch everything
        return "failed", buffer.getvalue(), diagnose_exception(exc)
    return "passed_execution", buffer.getvalue(), None


def run_self_healing(
    task: str,
    generator: Generator,
    verifier,
    budget: RetryBudget | None = None,
    sleep=time.sleep,
) -> HealingReport:
    """Run the generate-execute-verify loop until success or a stopping rule.

    ``verifier`` is a callable taking the captured stdout and returning
    ``(ok: bool, reason: str)``. Execution succeeding is not enough — the
    output must also be *correct*, and only the verifier can say that.
    """
    budget = budget or RetryBudget()
    started = time.monotonic()
    history: list[AttemptRecord] = []
    seen_signatures: set[tuple[str, str, int | None]] = set()

    for attempt in range(1, budget.max_attempts + 1):
        if time.monotonic() - started > budget.max_total_s:
            return HealingReport(
                task=task, succeeded=False, attempts=history,
                stop_reason="Wall-clock budget exhausted — stopped mid-loop "
                            "rather than starting another attempt.",
            )

        code = generator.generate(task, history)
        outcome, output, diagnosis = _execute(code)
        kind = classify_failure(diagnosis) if diagnosis else None

        if outcome == "passed_execution":
            ok, reason = verifier(output)
            if ok:
                history.append(AttemptRecord(attempt, code, "passed"))
                return HealingReport(
                    task=task, succeeded=True, attempts=history,
                    stop_reason="Verifier accepted the output.",
                    final_code=code, final_output=output,
                )
            # The code ran but the answer is wrong: this is a failure of the
            # generator, not of the runtime, so it counts as an attempt.
            diagnosis = parse_traceback(
                f"AssertionError: verifier rejected the output: {reason}"
            )
            kind = FailureKind.REPAIRABLE
            history.append(AttemptRecord(attempt, code, "failed",
                                         diagnosis, kind))
        else:
            history.append(AttemptRecord(attempt, code, outcome,
                                         diagnosis, kind))

        # --- Stopping rules, checked before any retry is scheduled. ---
        if kind is FailureKind.UNREPAIRABLE:
            return HealingReport(
                task=task, succeeded=False, attempts=history,
                stop_reason=f"Unrepairable failure ({diagnosis.exception_type}): "
                            "retrying cannot fix this. Escalate to a human.",
            )
        if diagnosis is not None:
            if diagnosis.signature in seen_signatures:
                return HealingReport(
                    task=task, succeeded=False, attempts=history,
                    stop_reason=f"Same failure signature twice "
                                f"({diagnosis.signature[0]} at line "
                                f"{diagnosis.signature[2]}): no new "
                                "information is being produced. Stopping "
                                "early instead of burning the budget.",
                )
            seen_signatures.add(diagnosis.signature)

        if attempt < budget.max_attempts:
            sleep(budget.delay_for(attempt))

    return HealingReport(
        task=task, succeeded=False, attempts=history,
        stop_reason=f"Retry budget exhausted ({budget.max_attempts} attempts). "
                    "Failing closed with the full diagnosis below.",
    )


# ---------------------------------------------------------------------------
# Demos
# ---------------------------------------------------------------------------

BUGGY_MEDIAN = '''
def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2]

print(median([3, 1, 2, 4]))
'''


def _median_verifier(output: str) -> tuple[bool, str]:
    # median([1, 2, 3, 4]) is 2.5; the buggy version prints 3.
    if output.strip() == "2.5":
        return True, ""
    return False, f"expected '2.5', got {output.strip()!r}"


def _timestamp_verifier(output: str) -> tuple[bool, str]:
    # The task asks for the current UTC time; the verifier accepts only
    # one fixed timestamp. No program that follows the task can ever
    # print it, so the verifier rejects every attempt and the loop
    # stops early on the repeated failure signature. The reason
    # deliberately does not name the expected value: naming it would
    # hand the model a literal to print, which would "pass" without
    # doing the task.
    if output.strip() == "2026-11-22T00:00:00+00:00":
        return True, ""
    return False, "output did not match the required timestamp"


def main() -> int:
    try:
        client = LLMClient()
    except LLMError as error:
        print(f"Cannot run the demo: {error}", file=sys.stderr)
        print("Set LLM_API_KEY (and optionally LLM_BASE_URL and LLM_MODEL), "
              "or run with LLM_BACKEND=opencheap-connector.", file=sys.stderr)
        return 2

    print("=== Demo 1: the loop heals itself ===")
    report = run_self_healing(
        task="Write a median function and print median([3, 1, 2, 4]).",
        generator=LLMGenerator(client, first_code=BUGGY_MEDIAN),
        verifier=_median_verifier,
        budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
        sleep=lambda _s: None,
    )
    print(report)

    print("\n=== Demo 2: the verifier never accepts — stop on a repeated signature ===")
    report = run_self_healing(
        task="Print the current UTC time as an ISO 8601 timestamp.",
        generator=LLMGenerator(client),
        verifier=_timestamp_verifier,
        budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
        sleep=lambda _s: None,
    )
    print(report)
    print("\nDiagnosis of the final failure:")
    print(report.attempts[-1].diagnosis.summary())

    print("\n=== Demo 3: a missing dependency — unrepairable on the first attempt ===")
    report = run_self_healing(
        task="Import the qzx_cryptolib module and use its encrypt() "
             "function on the string 'hello', then print the ciphertext.",
        generator=LLMGenerator(client),
        verifier=lambda output: (True, ""),
        budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
        sleep=lambda _s: None,
    )
    print(report)

    print(
        f"\nModel usage for this run: {client.total_prompt_tokens} prompt tokens, "
        f"{client.total_completion_tokens} completion tokens"
        + (f", ${client.total_cost_usd:.4f}" if client.total_cost_usd else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
