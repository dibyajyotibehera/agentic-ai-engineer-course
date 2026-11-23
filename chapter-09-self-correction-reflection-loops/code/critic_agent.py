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

Both model-facing components are real model calls, through
``llm_client.LLMClient`` (the same client Chapter 7 uses):

* ``LLMMaker`` generates the first draft and revises it using the
  critic's findings, verbatim. It is the component that used to be a
  scripted stand-in; it now calls a real model.
* ``LLMCritic`` is a second-opinion judge in the Chapter 7 judge
  pattern: it reads the specification, the draft and the test
  evidence, and returns a structured JSON verdict with a confidence
  score. It never rewrites the draft — rewriting is the maker's job.

The verdict the loop *acts on* is always the test-suite critic's
(``Critic``), never the model critic's. That division is the
chapter's central lesson, not an implementation detail: a model
critic on its own is exactly the intrinsic self-correction that the
research shows does not work (see README.md), so the model judge is
a second opinion whose agreement with the test suite is worth
checking, and the test suite is the ground truth that decides
whether the loop continues. The demo prints both verdicts side by
side so the reader can see where they agree.

Running the demo needs a configured model backend — ``LLM_API_KEY``
(or ``LLM_BACKEND=opencheap-connector`` on the author's machine, as
in Chapter 7). Without one it fails loudly instead of pretending
with a scripted maker. The deterministic behaviour of the loop
itself — rounds, stopping rules, escalation — is covered by the
tests in ``test_critic_and_healing.py``, which drive the loop with a
small scripted maker defined in that file, so the tests need no
model and no key.

Run the module directly:

    python critic_agent.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from llm_client import LLMClient, LLMError

# The maker is asked for code only, in one fenced block, so the reply can
# be extracted mechanically instead of interpreted. A maker that is
# allowed to explain itself in prose is a maker whose explanation the
# loop has to parse — and a parser is a place for a bug to hide.
_MAKER_SYSTEM = """You are a Python programmer writing a single function. \
Reply with exactly one fenced python code block containing the complete \
function and nothing else — no explanation, no extra code, no tests."""

_MAKER_FIRST_DRAFT = """Write a Python function that satisfies this specification:

{specification}
"""

_MAKER_REVISE = """Revise the draft below so that it satisfies this specification:

{specification}

Current draft:
```python
{draft}
```

The critic ran the draft against a test suite and found these failures. \
Change only what the findings name; do not rewrite unrelated parts:
{findings}
"""

# The model critic is instructed to judge from the evidence, not from
# its own opinion of the code: the test evidence is the ground truth,
# and the critic's job is to turn it into a structured verdict with a
# confidence score, in the same shape as Chapter 7's injection judge.
_CRITIC_SYSTEM = """You are a code critic. Review the draft below against the specification
and the test evidence. Do not rewrite the code.

Reply with a JSON object only, with exactly these fields:
- "verdict": "PASS" or "FAIL"
- "confidence": a number from 0.0 to 1.0
- "findings": a list of objects, one per failing test, each with exactly
  these fields: "test_name", "what_happened", "what_is_required". Each
  finding must name the failing test, state what happened, and state what
  the specification requires instead. Keep each field to one short
  sentence. An empty list when the verdict is PASS.
- "reason": one sentence explaining the verdict

Do not add any other text."""

_CRITIC_REQUIRED_FIELDS = ("verdict", "confidence", "findings", "reason")

# Generous enough for a short function: this model spends part of its
# token budget on hidden reasoning, and a small budget can be spent
# entirely on reasoning, leaving an empty reply (see Chapter 7).
_MAKER_MAX_TOKENS = 800
# The critic returns a short structured verdict, not code — but with five
# failing tests the findings list is long, and this model spends part of
# its token budget on hidden reasoning, so the budget must still be
# generous or the reply is cut off mid-JSON (see Chapter 7).
_CRITIC_MAX_TOKENS = 1500

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)\n?```", re.DOTALL)


def _extract_code(reply: str) -> str:
    """Return the Python code from a model reply.

    Prefers the first fenced code block; falls back to the whole reply
    when it already looks like code. Raises instead of returning a
    plausible-looking fragment, because a half-extracted draft would
    fail the tests for the wrong reason and teach the loop nothing.
    """
    match = _CODE_BLOCK_RE.search(reply)
    if match:
        return match.group(1).strip() + "\n"
    stripped = reply.strip()
    if stripped.startswith("def ") or stripped.startswith("import "):
        return stripped + "\n"
    raise LLMError(f"Model reply contained no Python code block: {reply[:300]}")


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


@dataclass
class LLMCritique:
    """What the model critic decided about one draft."""

    verdict: str  # "PASS" or "FAIL"
    confidence: float
    findings: list[Finding] = field(default_factory=list)
    reason: str = ""


class LLMMaker:
    """A real language-model maker: drafts and revises code by model call."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def first_draft(self, specification: str) -> str:
        reply = self.client.complete(
            [
                {"role": "system", "content": _MAKER_SYSTEM},
                {"role": "user", "content": _MAKER_FIRST_DRAFT.format(
                    specification=specification)},
            ],
            temperature=0.0,
            max_tokens=_MAKER_MAX_TOKENS,
        )
        return _extract_code(reply)

    def revise(self, specification: str, draft: str,
               findings: list[Finding]) -> str:
        findings_text = "\n".join(f"- {finding}" for finding in findings)
        reply = self.client.complete(
            [
                {"role": "system", "content": _MAKER_SYSTEM},
                {"role": "user", "content": _MAKER_REVISE.format(
                    specification=specification,
                    draft=draft,
                    findings=findings_text,
                )},
            ],
            temperature=0.0,
            max_tokens=_MAKER_MAX_TOKENS,
        )
        return _extract_code(reply)


class LLMCritic:
    """A model critic: a second opinion, judged against test evidence.

    Follows the Chapter 7 judge pattern — a cheap model, a fixed
    prompt, a JSON verdict, temperature zero, and a confidence score.
    The test-suite critique is passed in as the evidence, so the
    model judge is reading ground truth, not re-deriving it from its
    own opinion of the code. Its verdict never decides the loop; the
    test-suite critic's verdict does.
    """

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def judge(self, draft: str, evidence: Critique) -> LLMCritique:
        # The evidence is the test-suite critique: a pass count, plus one
        # line per failing test. Passing tests need no line — a finding
        # exists only for a test that failed, so the absence of a finding
        # for a test is the record that it passed.
        lines = [f"{evidence.tests_passed}/{evidence.tests_run} tests passed."]
        for finding in evidence.findings:
            lines.append(
                f"- {finding.test_name}: {finding.what_happened}; "
                f"required: {finding.what_is_required}"
            )
        evidence_text = "\n".join(lines)
        user_message = (
            f"Specification:\n{MEDIAN_SPEC}\n\n"
            f"Draft:\n```python\n{draft}```\n\n"
            f"Test evidence (each test the draft was run against, and "
            f"its result):\n{evidence_text}"
        )
        result = self.client.structured(
            [
                {"role": "system", "content": _CRITIC_SYSTEM},
                {"role": "user", "content": user_message},
            ],
            model=self.client.judge_model,
            temperature=0.0,
            max_tokens=_CRITIC_MAX_TOKENS,
            required_fields=_CRITIC_REQUIRED_FIELDS,
        )
        verdict = str(result["verdict"]).upper()
        if verdict not in ("PASS", "FAIL"):
            raise ValueError(f"Critic returned an unknown verdict: {verdict!r}.")
        findings = [
            Finding(
                test_name=str(item.get("test_name", "")),
                what_happened=str(item.get("what_happened", "")),
                what_is_required=str(item.get("what_is_required", "")),
            )
            for item in (result["findings"] or [])
            if isinstance(item, dict)
        ]
        return LLMCritique(
            verdict=verdict,
            confidence=float(result["confidence"]),
            findings=findings,
            reason=str(result["reason"]),
        )


class Maker(Protocol):
    """What the loop needs from a maker, model or otherwise."""

    def first_draft(self, specification: str) -> str: ...
    def revise(self, specification: str, draft: str,
               findings: list[Finding]) -> str: ...


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
    maker: Maker,
    critic: Critic,
    max_rounds: int = 3,
    first_draft: str | None = None,
    on_round: Optional[Callable[[int, str, Critique], None]] = None,
) -> MakerCheckerReport:
    """Run the maker–critic loop for at most ``max_rounds`` rounds.

    ``first_draft`` starts the loop from an existing draft — a human's
    first attempt, or a draft from an earlier run — instead of asking
    the maker for one. ``on_round`` is called after each critique
    with (round number, draft, critique); the demo uses it to ask the
    model critic for a second opinion, and Chapter 9's watchdog demo
    uses it to observe the loop's trajectory.
    """
    critiques: list[Critique] = []
    if first_draft is not None:
        draft = first_draft
    else:
        draft = maker.first_draft(specification)
    for round_no in range(1, max_rounds + 1):
        critique = critic.critique(draft)
        critiques.append(critique)
        if on_round is not None:
            on_round(round_no, draft, critique)
        if critique.verdict == "PASS":
            return MakerCheckerReport(True, round_no, max_rounds, draft, critiques)
        if round_no == max_rounds:
            break
        # The findings — and the draft they apply to — are passed to the
        # maker so a real maker can revise using exactly what the critic
        # found.
        draft = maker.revise(specification, draft, critique.findings)
    return MakerCheckerReport(False, max_rounds, max_rounds, draft, critiques)


# ---------------------------------------------------------------------------
# Demos
# ---------------------------------------------------------------------------

# Draft 1 is the classic first-pass mistake: it never sorts, it averages
# nothing for even lengths, and it crashes on an empty list. The demo
# starts the loop from this draft — as if it were a first attempt handed
# to the loop for repair — so the repair path is exercised on every run
# instead of depending on the model making the same mistake first.
DRAFT_BUGGY = '''
def median(xs):
    n = len(xs)
    return xs[n // 2]
'''


def main() -> int:
    try:
        client = LLMClient()
    except LLMError as error:
        print(f"Cannot run the demo: {error}", file=sys.stderr)
        print("Set LLM_API_KEY (and optionally LLM_BASE_URL and LLM_MODEL), "
              "or run with LLM_BACKEND=opencheap-connector.", file=sys.stderr)
        return 2

    maker = LLMMaker(client)
    critic = Critic(MEDIAN_SPEC, MEDIAN_TESTS)
    llm_critic = LLMCritic(client)

    def second_opinion(round_no: int, draft: str, critique: Critique) -> None:
        # The model critic reads the same test evidence the test-suite
        # critic produced. Its verdict is printed, never acted on.
        judgement = llm_critic.judge(draft, critique)
        print(f"  Model critic, round {round_no}: {judgement.verdict} "
              f"(confidence {judgement.confidence:.2f}) — {judgement.reason}")
        if judgement.verdict != critique.verdict:
            print("  NOTE: the model critic disagrees with the test suite. "
                  "The test suite decides; this is the blind spot the "
                  "chapter warns about.")

    report = run_maker_checker(
        MEDIAN_SPEC, maker, critic, max_rounds=3,
        first_draft=DRAFT_BUGGY, on_round=second_opinion,
    )
    print(report)
    print("\nFinal draft:")
    print(report.final_draft)
    print(
        f"\nModel usage for this run: {client.total_prompt_tokens} prompt tokens, "
        f"{client.total_completion_tokens} completion tokens"
        + (f", ${client.total_cost_usd:.4f}" if client.total_cost_usd else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
