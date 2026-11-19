"""The critic agent: a second pass that checks the first pass's work.

This module implements the maker–checker pattern, the simplest useful form
of a critic agent. One component (the maker) produces a draft; a separate
component (the critic) checks that draft against a rubric and returns a
verdict — pass, or fail with specific, actionable findings. The maker then
revises using only those findings, and the loop runs for a bounded number of
rounds.

Three design decisions matter, and all three are deliberate:

1. The critic is *independent* of the maker. It does not ask the maker
   whether the draft is good; it runs the draft against a test suite, which
   is ground truth the maker cannot argue with. A critic that shares the
   maker's assumptions will share the maker's blind spots.
2. The critic's findings are *specific*. "This is wrong" is not a finding;
   "median([]) raises IndexError, but the specification requires a
   ValueError" is, because it tells the maker exactly what to change.
3. The loop is *bounded*. A maximum number of rounds is set in advance, and
   when it is reached the best draft so far is returned together with the
   full critique history and an escalation note — never an endless loop, and
   never a silent acceptance of a draft the critic rejected.

The maker here is a scripted stand-in with a fixed list of drafts, so the
module runs with no model and no API key. The critic is fully real: it
executes each draft against a test suite. To plug in a language-model critic,
use the ``LLM_CRITIC_PROMPT`` template below — but keep the test suite as
well, because a model critic on its own is exactly the intrinsic
self-correction that the research shows does not work (see README.md).

Run the module directly:

    python critic_agent.py
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The prompt template for a language-model critic. The template is kept here
# so the chapter's lesson is concrete even before a model is wired in: the
# critic is instructed to return a structured verdict, to quote the failing
# evidence, and never to rewrite the draft itself (rewriting is the maker's
# job — a critic that rewrites is a second maker, not a check).
LLM_CRITIC_PROMPT = """You are a code critic. Review the draft below against the specification
and the test evidence. Do not rewrite the code.

Specification:
{specification}

Draft:
```python
{draft}
```

Test evidence (each test the draft was run against, and its result):
{test_evidence}

Return exactly:
VERDICT: PASS or FAIL
FINDINGS:
- <one finding per line; each finding must name the failing test or the
  specific line, state what happened, and state what the specification
  requires instead>
"""


@dataclass
class Finding:
    test_name: str
    what_happened: str
    what_is_required: str

    def __str__(self) -> str:  # pragma: no cover - formatting helper
        return (f"{self.test_name}: {self.what_happened} — "
                f"required: {self.what_is_required}")


@dataclass
class Critique:
    verdict: str  # "PASS" or "FAIL"
    findings: list[Finding] = field(default_factory=list)
    tests_run: int = 0
    tests_passed: int = 0

    def __str__(self) -> str:  # pragma: no cover - formatting helper
        lines = [f"VERDICT: {self.verdict} "
                 f"({self.tests_passed}/{self.tests_run} tests passed)"]
        lines.extend(f"  - {finding}" for finding in self.findings)
        return "\n".join(lines)


@dataclass
class TestCase:
    name: str
    # Python source that, when executed after the draft, asserts the
    # expected behaviour. Keeping tests as source keeps the critic
    # independent of the maker's implementation.
    assertion: str


MEDIAN_SPEC = (
    "median(xs) returns the median of a non-empty list of numbers: "
    "the middle value for an odd-length list, the mean of the two middle "
    "values for an even-length list. For an empty list it raises "
    "ValueError. The input list must not be modified."
)

MEDIAN_TESTS = [
    TestCase("odd length", "assert median([3, 1, 2]) == 2"),
    TestCase("even length", "assert median([3, 1, 2, 4]) == 2.5"),
    TestCase("unsorted input", "assert median([9, 1, 5]) == 5"),
    TestCase("single element", "assert median([7]) == 7"),
    TestCase("empty list raises ValueError",
             "try:\n    median([])\n    raise SystemExit('no error raised')\n"
             "except ValueError:\n    pass"),
    TestCase("input not modified",
             "xs = [3, 1, 2]\nmedian(xs)\nassert xs == [3, 1, 2]"),
    TestCase("negative numbers", "assert median([-5, -1, -3]) == -3"),
]


class Critic:
    """A test-suite critic: the draft is executed, never merely read."""

    def __init__(self, specification: str, tests: list[TestCase]):
        self.specification = specification
        self.tests = tests

    def critique(self, draft: str) -> Critique:
        findings: list[Finding] = []
        passed = 0
        for test in self.tests:
            namespace: dict = {}
            try:
                exec(compile(draft, "<draft>", "exec"), namespace)  # noqa: S102
                exec(compile(test.assertion, "<test>", "exec"), namespace)  # noqa: S102
            except AssertionError:
                findings.append(Finding(
                    test_name=test.name,
                    what_happened="the assertion failed",
                    what_is_required="the behaviour stated in the specification",
                ))
            except Exception as exc:  # noqa: BLE001 - the critic must catch all
                findings.append(Finding(
                    test_name=test.name,
                    what_happened=f"raised {type(exc).__name__}: {exc}",
                    what_is_required="the behaviour stated in the specification",
                ))
            else:
                passed += 1
        return Critique(
            verdict="PASS" if not findings else "FAIL",
            findings=findings,
            tests_run=len(self.tests),
            tests_passed=passed,
        )


class ScriptedMaker:
    """A stand-in for a language-model maker.

    TODO: replace with a real generator. Its interface is
    ``revise(specification, findings) -> str``: given the specification and
    the critic's findings from the previous round, return a new draft. A
    real implementation should pass the findings verbatim into its prompt
    and should be instructed to change only what the findings name.
    """

    def __init__(self, drafts: list[str]):
        self.drafts = drafts
        self._next = 1  # Index of the draft to return on the next revision.

    def first_draft(self, specification: str) -> str:
        return self.drafts[0]

    def revise(self, specification: str, findings: list[Finding]) -> str:
        # The scripted maker simply moves to its next prepared draft; a real
        # maker would rewrite using the findings. The findings are accepted
        # here so the interface matches what a real maker needs.
        draft = self.drafts[min(self._next, len(self.drafts) - 1)]
        self._next += 1
        return draft


@dataclass
class MakerCheckerReport:
    succeeded: bool
    rounds_used: int
    max_rounds: int
    final_draft: str
    critiques: list[Critique] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - formatting helper
        lines = [
            f"Outcome: {'PASS' if self.succeeded else 'FAILED CLOSED — escalate'}",
            f"Rounds used: {self.rounds_used}/{self.max_rounds}",
        ]
        for i, critique in enumerate(self.critiques, start=1):
            lines.append(f"Round {i} critique:\n{critique}")
        return "\n".join(lines)


def run_maker_checker(
    specification: str,
    maker: ScriptedMaker,
    critic: Critic,
    max_rounds: int = 3,
) -> MakerCheckerReport:
    """Run the maker–critic loop for at most ``max_rounds`` rounds."""
    critiques: list[Critique] = []
    draft = maker.first_draft(specification)
    for round_no in range(1, max_rounds + 1):
        critique = critic.critique(draft)
        critiques.append(critique)
        if critique.verdict == "PASS":
            return MakerCheckerReport(True, round_no, max_rounds, draft, critiques)
        if round_no == max_rounds:
            break
        # The findings are passed to the maker so a real maker can revise
        # using exactly what the critic found.
        draft = maker.revise(specification, critique.findings)
    return MakerCheckerReport(False, max_rounds, max_rounds, draft, critiques)


# ---------------------------------------------------------------------------
# Demos
# ---------------------------------------------------------------------------

# Draft 1 is the classic first-pass mistake: it never sorts, it averages
# nothing for even lengths, and it crashes on an empty list.
DRAFT_BUGGY = '''
def median(xs):
    n = len(xs)
    return xs[n // 2]
'''

# Draft 2 fixes every finding from the round-1 critique.
DRAFT_FIXED = '''
def median(xs):
    if not xs:
        raise ValueError("median of an empty list is undefined")
    ordered = sorted(xs)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
'''


if __name__ == "__main__":
    critic = Critic(MEDIAN_SPEC, MEDIAN_TESTS)
    maker = ScriptedMaker([DRAFT_BUGGY, DRAFT_FIXED])
    report = run_maker_checker(MEDIAN_SPEC, maker, critic, max_rounds=3)
    print(report)
    print("\nFinal draft:")
    print(report.final_draft)
