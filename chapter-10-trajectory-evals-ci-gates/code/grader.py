"""A step-level grader for a support-copilot agent.

An end-state grader asks one question: did the customer get the right
refund? A step-level grader asks a question per step, in order, so it
can tell you *where* a run went wrong and whether the right outcome
was reached by a wrong route. The case this module is built around is
the one that motivates the whole chapter: the agent issued the correct
refund, for the correct amount, but called the refund tool *before*
the eligibility check. The money is right and the run is still a
failure, because an agent that refunds before checking eligibility
will eventually refund someone who is not eligible.

The grader is pure code, no model involved, so it is deterministic,
free, and fast enough to run on every pull request. A language-model
judge (see ``judge.py``) sits on top of it for the things code cannot
see, such as tone; it never replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trajectory import GoldenDAG, Trajectory


@dataclass
class CheckResult:
    """The verdict on one check, tied to the step where it failed."""

    name: str
    passed: bool
    detail: str
    step_index: int | None = None  # index into the trajectory, if any


@dataclass
class Grade:
    """The full step-level grade for one trajectory."""

    case_id: str
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    first_failure: CheckResult | None = None

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [f"{self.case_id}: {status}"]
        for check in self.checks:
            mark = "ok " if check.passed else "FAIL"
            lines.append(f"  [{mark}] {check.name}: {check.detail}")
        return "\n".join(lines)


class StepLevelGrader:
    """Grades a support-copilot trajectory against a golden DAG.

    The checks run in the order a reviewer would work through them:

    1. **Ordering** — every tool call respects the DAG's precedence
       edges. This is the check that catches the refund-before-
       eligibility error.
    2. **Completeness** — every required step ran, and no unknown
       tool was called.
    3. **Arguments** — the refund the agent issued matches what the
       eligibility check authorised: same order, same amount, same
       currency. A correct outcome with wrong arguments is a failure
       that an end-state check would miss.
    4. **Outcome** — the final outcome matches the expected outcome
       recorded on the case.
    """

    def __init__(self, dag: GoldenDAG, expected_outcomes: dict[str, str]):
        self.dag = dag
        self.expected_outcomes = expected_outcomes

    def grade(self, trajectory: Trajectory) -> Grade:
        checks: list[CheckResult] = []

        # 1. Ordering and completeness come straight from the DAG.
        report = self.dag.validate(trajectory)
        checks.append(
            CheckResult(
                name="ordering",
                passed=not report["precedence_violations"],
                detail=(
                    "all tool calls respect the golden ordering"
                    if not report["precedence_violations"]
                    else "; ".join(
                        f"{tool} ran before {missing}"
                        for tool, missing in report["precedence_violations"]
                    )
                ),
                step_index=self._index_of(trajectory, report["precedence_violations"][0][0])
                if report["precedence_violations"]
                else None,
            )
        )
        checks.append(
            CheckResult(
                name="required_steps",
                passed=not report["missing_required"] and not report["unknown_steps"],
                detail=(
                    "all required steps present, no unknown tools"
                    if not report["missing_required"] and not report["unknown_steps"]
                    else (
                        f"missing: {report['missing_required']}; "
                        f"unknown tools: {report['unknown_steps']}"
                    ).strip("; ")
                ),
            )
        )
        checks.append(
            CheckResult(
                name="branching",
                passed=not report["branch_violations"],
                detail=(
                    "took exactly one branch of the resolution"
                    if not report["branch_violations"]
                    else "; ".join(report["branch_violations"])
                ),
            )
        )

        # 2. Argument checks: the refund must match the authorisation.
        checks.append(self._check_refund_arguments(trajectory))

        # 3. Outcome check: the ending must be the expected ending.
        expected = self.expected_outcomes.get(trajectory.case_id)
        checks.append(
            CheckResult(
                name="outcome",
                passed=expected is None or trajectory.final_outcome == expected,
                detail=(
                    f"final outcome {trajectory.final_outcome!r} matches expected"
                    if trajectory.final_outcome == expected
                    else f"final outcome {trajectory.final_outcome!r}, "
                    f"expected {expected!r}"
                ),
            )
        )

        failed = [c for c in checks if not c.passed]
        return Grade(
            case_id=trajectory.case_id,
            passed=not failed,
            checks=checks,
            first_failure=failed[0] if failed else None,
        )

    def _check_refund_arguments(self, trajectory: Trajectory) -> CheckResult:
        """The issued refund must match what eligibility authorised."""
        eligibility = None
        refund = None
        for step in trajectory.steps:
            if step.tool == "check_eligibility":
                eligibility = step
            if step.tool == "issue_refund":
                refund = step
        if refund is None:
            return CheckResult(
                name="refund_arguments",
                passed=True,
                detail="no refund issued, nothing to reconcile",
            )
        if eligibility is None:
            return CheckResult(
                name="refund_arguments",
                passed=False,
                detail="refund issued with no eligibility check anywhere in the run",
                step_index=self._index_of(trajectory, "issue_refund"),
            )
        authorised = eligibility.result.get("eligible_amount")
        issued = refund.args.get("amount")
        currency_ok = refund.args.get("currency") == eligibility.result.get("currency", "USD")
        order_ok = refund.args.get("order_id") == eligibility.args.get("order_id")
        if authorised is not None and issued != authorised:
            return CheckResult(
                name="refund_arguments",
                passed=False,
                detail=f"issued ${issued}, eligibility authorised ${authorised}",
                step_index=self._index_of(trajectory, "issue_refund"),
            )
        if not currency_ok or not order_ok:
            return CheckResult(
                name="refund_arguments",
                passed=False,
                detail=(
                    f"refund arguments do not match the eligibility check "
                    f"(currency_ok={currency_ok}, order_ok={order_ok})"
                ),
                step_index=self._index_of(trajectory, "issue_refund"),
            )
        return CheckResult(
            name="refund_arguments",
            passed=True,
            detail=f"issued ${issued} for the authorised order, as authorised",
        )

    @staticmethod
    def _index_of(trajectory: Trajectory, tool: str) -> int | None:
        for i, step in enumerate(trajectory.steps):
            if step.tool == tool:
                return i
        return None
