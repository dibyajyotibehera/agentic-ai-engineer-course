"""Traceback analysis: turn a raw Python traceback into a structured diagnosis.

This module is the deterministic half of the chapter's self-correction story.
Before any language model is asked to repair code, the failure should be
parsed into facts: what exception was raised, where in the programmer's own
code it happened, and what kind of mistake it most plausibly is. A model that
receives a structured diagnosis repairs faster and more reliably than one
handed a wall of raw text, and — just as importantly — a structured diagnosis
lets the loop decide *whether* a retry is even worth attempting.

Only the standard library is used. Run the module directly to see a demo:

    python traceback_parser.py
"""

from __future__ import annotations

import re
import sys
import sysconfig
import traceback
from dataclasses import dataclass, field

# A single stack frame line in a formatted traceback, for example:
#   File "/home/user/work.py", line 42, in compute_total
_FRAME_RE = re.compile(
    r'^\s*File "(?P<file>.+?)", line (?P<line>\d+), in (?P<func>.+?)\s*$'
)

# The final line of a traceback, for example "KeyError: 'user_id'".
_EXCEPTION_RE = re.compile(r'^(?P<type>[A-Za-z_][\w.]*)(?:: (?P<message>.*))?$')

# Frames that belong to the interpreter or installed packages rather than to
# the code under repair. The deepest *user* frame is usually where the fix
# belongs; frames below it are library internals the agent should not edit.
_STDLIB_DIR = sysconfig.get_paths()["stdlib"]


def _is_user_code(filename: str) -> bool:
    if filename.startswith("<"):  # <stdin>, <string>, <frozen ...>
        return True
    if "site-packages" in filename or "dist-packages" in filename:
        return False
    if filename.startswith(_STDLIB_DIR):
        return False
    return True


@dataclass
class Frame:
    """One frame of a stack trace."""

    filename: str
    lineno: int
    function: str
    source_line: str | None = None

    def __str__(self) -> str:  # pragma: no cover - formatting helper
        line = f"  File {self.filename!r}, line {self.lineno}, in {self.function}"
        if self.source_line:
            line += f"\n    {self.source_line.strip()}"
        return line


@dataclass
class TracebackDiagnosis:
    """A structured reading of a failure, ready to hand to a repair step."""

    exception_type: str
    message: str
    frames: list[Frame] = field(default_factory=list)
    cause: str = ""
    suggested_fix: str = ""
    syntax_location: tuple[str, int, int | None] | None = None

    @property
    def user_frames(self) -> list[Frame]:
        """Frames in the programmer's own code, deepest first."""
        return [f for f in reversed(self.frames) if _is_user_code(f.filename)]

    @property
    def deepest_user_frame(self) -> Frame | None:
        frames = self.user_frames
        return frames[0] if frames else None

    @property
    def signature(self) -> tuple[str, str, int | None]:
        """A stable identity for this failure, used to detect repeats.

        Two attempts that fail with the same signature have produced no new
        information, which is the loop's signal to stop rather than retry.
        """
        if self.syntax_location is not None:
            return (self.exception_type, self.syntax_location[0],
                    self.syntax_location[1])
        frame = self.deepest_user_frame
        return (
            self.exception_type,
            frame.filename if frame else "",
            frame.lineno if frame else None,
        )

    def summary(self) -> str:
        lines = [f"{self.exception_type}: {self.message}"]
        if self.syntax_location is not None:
            filename, lineno, offset = self.syntax_location
            location = f"Compiler location: {filename}:{lineno}"
            if offset is not None:
                location += f", column {offset}"
            lines.append(location)
        frame = self.deepest_user_frame
        if frame is not None:
            lines.append(f"Deepest user frame: {frame.filename}:{frame.lineno} in {frame.function}")
            if frame.source_line:
                lines.append(f"  {frame.source_line.strip()}")
        lines.append(f"Likely cause: {self.cause}")
        lines.append(f"Suggested fix: {self.suggested_fix}")
        return "\n".join(lines)


# What each common exception usually means, and the first repair to try.
# These are heuristics, not certainties — the critic in critic_agent.py is
# what checks whether the suggested fix actually worked.
_KNOWN_ERRORS: dict[str, tuple[str, str]] = {
    "SyntaxError": (
        "The code does not parse; the compiler rejected it before any of it ran.",
        "Read the compiler's line and column (the caret points at the token) "
        "and fix the syntax there — a missing colon, bracket or quote — then "
        "re-parse before executing anything.",
    ),
    "IndentationError": (
        "A block is indented inconsistently, so the compiler cannot tell "
        "where the block starts or ends.",
        "Normalise the indentation of the reported block to one consistent "
        "width (four spaces) and re-parse.",
    ),
    "NameError": (
        "A name was used before it was defined, or it was misspelt.",
        "Check the spelling against the definitions in scope; if the name "
        "comes from another module, add the missing import.",
    ),
    "TypeError": (
        "An operation was applied to a value of the wrong type, or a function "
        "was called with the wrong arguments.",
        "Print the types of the values at the failing line and align the call "
        "with the function's signature.",
    ),
    "KeyError": (
        "A dictionary was accessed with a key it does not contain.",
        "Check whether the key should exist (a data problem) or whether the "
        "lookup should tolerate a missing key (.get with a default).",
    ),
    "IndexError": (
        "A sequence was indexed past its end, often an off-by-one error.",
        "Check the index arithmetic and the length of the sequence at the "
        "failing line; guard empty sequences explicitly.",
    ),
    "AttributeError": (
        "An attribute or method does not exist on the object it was accessed on.",
        "Check the object's actual type — it is often None, or a different "
        "type than the code assumed.",
    ),
    "ValueError": (
        "A value of the right type but an inappropriate value was supplied.",
        "Validate the input before use, or trace where the bad value came from.",
    ),
    "ZeroDivisionError": (
        "A division or modulo by zero was attempted.",
        "Guard the divisor, or decide what the result should be when it is zero.",
    ),
    "FileNotFoundError": (
        "A file path does not exist where the code expected it.",
        "Check the working directory and whether the path is relative to the "
        "wrong base; list the directory to confirm.",
    ),
    "ModuleNotFoundError": (
        "An import names a module that is not installed or not on the path.",
        "Check the spelling, the virtual environment, and whether the module "
        "needs installing.",
    ),
    "ImportError": (
        "An import failed — a missing name inside a module, or a circular import.",
        "Check the imported name exists in the module, and look for import cycles.",
    ),
    "AssertionError": (
        "An explicit assertion failed, so a stated invariant did not hold.",
        "Read the assertion as a specification: the code's assumption about "
        "its own state was wrong.",
    ),
    "TimeoutError": (
        "An operation did not complete in the time allowed.",
        "Treat as transient first: retry with backoff. If it repeats, the "
        "timeout or the operation itself needs changing.",
    ),
    "ConnectionError": (
        "A network call failed before it could complete.",
        "Treat as transient: retry with backoff, and check whether the "
        "endpoint is reachable at all.",
    ),
}


def classify(exception_type: str, message: str) -> tuple[str, str]:
    """Return a (likely cause, suggested fix) pair for an exception type."""
    short_type = exception_type.split(".")[-1]
    return _KNOWN_ERRORS.get(
        short_type,
        (
            "An unclassified failure — no heuristic applies, so the diagnosis "
            "must come from reading the failing code and its inputs.",
            "Read the deepest user frame and reproduce the failure with a "
            "minimal input before changing anything.",
        ),
    )


def parse_traceback(text: str) -> TracebackDiagnosis:
    """Parse formatted traceback text into a TracebackDiagnosis.

    Only the last exception block is diagnosed: when exceptions chain
    ("During handling of the above exception..."), the final exception is
    the one the repair must address first.
    """
    lines = text.strip().splitlines()
    frames: list[Frame] = []
    exception_type = "UnknownError"
    message = ""
    syntax_location: tuple[str, int, int | None] | None = None

    i = 0
    while i < len(lines):
        line = lines[i]
        match = _FRAME_RE.match(line)
        if match:
            source_line = None
            if i + 1 < len(lines) and not _FRAME_RE.match(lines[i + 1]):
                # A source line is indented; an exception line starts at
                # column 0, so it must never be swallowed as a source line.
                # (This matters for code compiled from a string, where the
                # traceback carries no source lines at all.)
                following = lines[i + 1]
                if following.startswith((" ", "\t")):
                    candidate = following.strip()
                    if candidate and not candidate.startswith("^"):
                        source_line = candidate
                        i += 1
            frames.append(
                Frame(
                    filename=match.group("file"),
                    lineno=int(match.group("line")),
                    function=match.group("func"),
                    source_line=source_line,
                )
            )
        elif line.startswith("SyntaxError") or line.startswith("IndentationError"):
            # Syntax errors carry their location on the following lines:
            #   File "x.py", line 3
            #     def broken(
            #                ^
            #   SyntaxError: '(' was never closed
            exc_match = _EXCEPTION_RE.match(line)
            if exc_match:
                exception_type = exc_match.group("type")
                message = exc_match.group("message") or ""
        elif line.startswith("  File ") and "line " in line:
            # The file/line line of a syntax error block.
            parts = re.search(r'File "(.+?)", line (\d+)', line)
            if parts and exception_type in ("SyntaxError", "IndentationError"):
                caret_offset = None
                if i + 2 < len(lines) and lines[i + 2].strip().startswith("^"):
                    caret_offset = lines[i + 2].index("^")
                syntax_location = (parts.group(1), int(parts.group(2)), caret_offset)
        else:
            exc_match = _EXCEPTION_RE.match(line)
            if exc_match and not line.startswith(" "):
                exception_type = exc_match.group("type")
                message = exc_match.group("message") or ""
        i += 1

    cause, suggested_fix = classify(exception_type, message)
    # A syntax error's stack frame is the call site of compile(), not the
    # broken code, so it would mislead the repair. The compiler's own
    # location is the diagnosis; drop the frames.
    if exception_type in ("SyntaxError", "IndentationError"):
        frames = []
    return TracebackDiagnosis(
        exception_type=exception_type,
        message=message,
        frames=frames,
        cause=cause,
        suggested_fix=suggested_fix,
        syntax_location=syntax_location,
    )


def diagnose_exception(exc: BaseException) -> TracebackDiagnosis:
    """Build a diagnosis from a live exception, without re-raising it."""
    formatted = "".join(traceback.format_exception(exc))
    diagnosis = parse_traceback(formatted)
    # Keep the exact type even if the formatted text was truncated.
    diagnosis.exception_type = type(exc).__name__
    if isinstance(exc, SyntaxError):
        diagnosis.syntax_location = (
            exc.filename or "<unknown>",
            exc.lineno or 0,
            exc.offset,
        )
    return diagnosis


if __name__ == "__main__":
    # Demo: a small buggy program, diagnosed twice — once from live capture
    # and once from raw traceback text, the form an agent usually receives
    # from a subprocess or a remote executor.
    buggy = '''
def total(prices):
    return sum(prices) / len(prices)

def checkout(cart):
    return total(cart["items"]) * cart["discount"]

print(checkout({"items": [10, 20, 30]}))
'''

    print("=== Live capture ===")
    try:
        exec(compile(buggy, "<demo>", "exec"), {})
    except Exception as exc:  # noqa: BLE001 - the demo deliberately catches all
        print(diagnose_exception(exc).summary())
        print(f"Failure signature: {diagnose_exception(exc).signature}")

    print("\n=== From raw traceback text ===")
    try:
        exec(compile(buggy, "<demo>", "exec"), {})
    except Exception:  # noqa: BLE001 - the demo deliberately catches all
        raw = traceback.format_exc()
    print(parse_traceback(raw).summary())

    print("\n=== A syntax error, with the compiler's location ===")
    broken = "def greet(name)\n    return f'hello {name}'\n"
    try:
        compile(broken, "<demo>", "exec")
    except SyntaxError as exc:
        diagnosis = diagnose_exception(exc)
        print(diagnosis.summary())
        print(f"Compiler location: {diagnosis.syntax_location}")
