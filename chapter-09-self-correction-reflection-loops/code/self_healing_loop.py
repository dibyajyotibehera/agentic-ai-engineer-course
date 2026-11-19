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

The generator here is a scripted stand-in with a fixed list of candidate
programs, so the module runs with no model and no API key. To plug in a real
language model, replace ``ScriptedGenerator`` with a callable that takes the
task and the attempt history and returns code — the loop itself does not
change. See the ``TODO`` in ``ScriptedGenerator``.

Safety note: the executor runs code in-process with ``exec`` for the sake of
a runnable demo. Production code must run agent-generated code in a sandbox
(Chapter 6 of this course) with resource limits and no network egress.
Never point this executor at untrusted code.

Run the module directly to see both endings — a repair that succeeds, and a
repair that fails closed:

    python self_healing_loop.py
"""

from __future__ import annotations

import ast
import contextlib
import io
import time
from dataclasses import dataclass, field
from enum import Enum

from traceback_parser import TracebackDiagnosis, diagnose_exception, parse_traceback


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


class ScriptedGenerator:
    """A stand-in for a language-model code generator.

    It returns a fixed sequence of candidate programs, one per attempt, so
    the loop's behaviour is deterministic and testable without a model.

    TODO: replace with a real generator. The interface is
    ``generate(task, history) -> str``, where ``history`` is the list of
    ``AttemptRecord`` objects so far. A real implementation should build its
    prompt from the task, the last diagnosis (exception type, deepest user
    frame, suggested fix) and the list of failure signatures already seen,
    and it should be instructed never to repeat a failed program verbatim.
    """

    def __init__(self, candidates: list[str]):
        self.candidates = candidates

    def generate(self, task: str, history: list[AttemptRecord]) -> str:
        index = min(len(history), len(self.candidates) - 1)
        return self.candidates[index]


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
    generator: ScriptedGenerator,
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

FIXED_MEDIAN = '''
def median(xs):
    if not xs:
        raise ValueError("median of an empty list is undefined")
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    if n % 2 == 1:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2

print(median([3, 1, 2, 4]))
'''

STILL_BUGGY = '''
def lookup(users, user_id):
    return users[user_id]["name"]

print(lookup({"u1": {"name": "Asha"}}, "u2"))
'''


def _median_verifier(output: str) -> tuple[bool, str]:
    # median([1, 2, 3, 4]) is 2.5; the buggy version prints 3.
    if output.strip() == "2.5":
        return True, ""
    return False, f"expected '2.5', got {output.strip()!r}"


def _never_passes(_output: str) -> tuple[bool, str]:
    return False, "no output can satisfy this verifier"


if __name__ == "__main__":
    print("=== Demo 1: the loop heals itself ===")
    report = run_self_healing(
        task="Write a median function and print median([3, 1, 2, 4]).",
        generator=ScriptedGenerator([BUGGY_MEDIAN, FIXED_MEDIAN]),
        verifier=_median_verifier,
        budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
        sleep=lambda _s: None,
    )
    print(report)

    print("\n=== Demo 2: the same KeyError twice — stop early, fail closed ===")
    report = run_self_healing(
        task="Look up a user that does not exist.",
        generator=ScriptedGenerator([STILL_BUGGY, STILL_BUGGY, STILL_BUGGY]),
        verifier=_never_passes,
        budget=RetryBudget(max_attempts=3, base_delay_s=0.0),
        sleep=lambda _s: None,
    )
    print(report)
    print("\nDiagnosis of the final failure:")
    print(report.attempts[-1].diagnosis.summary())
