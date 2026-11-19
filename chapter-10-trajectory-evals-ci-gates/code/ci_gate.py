"""The continuous-integration regression gate.

This module is the release gate itself: it runs the whole eval suite,
aggregates the results per criterion, compares each aggregate against
a stored baseline with a tolerance, and exits non-zero when the build
has regressed. A non-zero exit is what makes a gate a gate — the
workflow file in ``eval-gate.yml`` fails the build on it, so a bad
prompt or model change cannot merge.

Three design decisions are doing the work here:

1. **Gate on aggregates, not single cases.** One case can flip for
   a silly reason — a flaky tool, a paraphrase the grader did not
   expect — and blocking a release on it trains the team to ignore
   the gate. The gate compares the pass rate of each criterion
   against its baseline minus a tolerance of two to three points,
   so a real regression has to move the aggregate to trip it.
2. **Keep a baseline file in the repository.** ``baseline.json`` is
   the last known-good score, committed next to the evals. A change
   that improves the score updates the baseline in the same pull
   request, with the improvement visible in the diff; a change that
   lowers it has to explain itself.
3. **Critical cases fail the build on their own.** A small set of
   cases is marked critical — the ordering error in this chapter is
   one — because some failures are not a matter of degree. A refund
   issued before the eligibility check is never acceptable at any
   pass rate, so it bypasses the tolerance entirely.

The module also carries :func:`canary_verdict`, the rollout-side
companion to the pre-merge gate. It is a direct implementation of
the verdict function from LLMOps.si's deployment guide: a canary
starts at 5% of traffic, and the verdict needs at least 2,000
canary requests before it says anything, with the checks error rate
at or below stable times 1.2 plus 0.001, 95th-percentile latency at
or below stable times 1.15, eval pass rate at or above stable minus
0.02, and cost per request at or below stable times 1.10. The
continuous-integration gate and the canary verdict are two halves of
the same idea: the gate stops a regression before it ships, and the
canary catches the regression the gate could not see.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

# A regression is a drop bigger than this, in pass-rate points, below
# the baseline. Two to three points is the working tolerance: smaller
# than that and ordinary run-to-run wobble trips the gate; larger and
# a real regression slips through.
TOLERANCE = 0.02

# Cases in this set fail the build on their own, tolerance or not.
CRITICAL_CASES = {"refund-ordering-error"}


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run_suite(grader, trajectories: list) -> dict:
    """Grade every trajectory and aggregate the results per criterion.

    Returns the overall pass rate, the per-criterion pass rates, and
    the per-case grades, so the report can show both the number that
    tripped the gate and the case that caused it.
    """
    grades = [grader.grade(t) for t in trajectories]
    criteria: dict[str, list[bool]] = {}
    for grade in grades:
        for check in grade.checks:
            criteria.setdefault(check.name, []).append(check.passed)
    return {
        "n_cases": len(grades),
        "pass_rate": sum(g.passed for g in grades) / len(grades) if grades else 0.0,
        "criteria": {
            name: sum(results) / len(results)
            for name, results in criteria.items()
        },
        "cases": [
            {
                "case_id": g.case_id,
                "passed": g.passed,
                "first_failure": (
                    f"{g.first_failure.name}: {g.first_failure.detail}"
                    if g.first_failure
                    else None
                ),
            }
            for g in grades
        ],
    }


def gate(results: dict, baseline: dict, tolerance: float = TOLERANCE) -> dict:
    """Compare eval results against the baseline and decide pass/fail.

    The build fails when any criterion's pass rate falls more than
    ``tolerance`` below its baseline, when the overall pass rate does
    the same, or when any critical case fails. The report lists every
    breach, so the engineer reading the failed build knows whether
    they are looking at a broad regression or a single critical case.
    """
    breaches: list[str] = []
    baseline_criteria = baseline.get("criteria", {})

    overall_floor = baseline.get("pass_rate", 1.0) - tolerance
    if results["pass_rate"] < overall_floor:
        breaches.append(
            f"overall pass rate {results['pass_rate']:.2f} is below the "
            f"baseline {baseline.get('pass_rate', 1.0):.2f} minus tolerance "
            f"{tolerance:.2f} (floor {overall_floor:.2f})"
        )
    for name, score in results["criteria"].items():
        floor = baseline_criteria.get(name, 1.0) - tolerance
        if score < floor:
            breaches.append(
                f"criterion {name!r}: {score:.2f} is below its baseline "
                f"{baseline_criteria.get(name, 1.0):.2f} minus tolerance "
                f"{tolerance:.2f} (floor {floor:.2f})"
            )
    for case in results["cases"]:
        if case["case_id"] in CRITICAL_CASES and not case["passed"]:
            breaches.append(
                f"critical case {case['case_id']!r} failed: "
                f"{case['first_failure']}"
            )
    return {"passed": not breaches, "breaches": breaches}


def canary_verdict(stable: dict, canary: dict) -> str:
    """Promote, roll back, or wait, from the canary's live numbers.

    ``stable`` and ``canary`` each carry ``requests``, ``error_rate``,
    ``p95_ms``, ``eval_pass`` and ``cost_per_request`` for the same
    time window. The thresholds are the LLMOps.si deployment guide's:
    at least 2,000 canary requests before any verdict, then error rate
    at or below stable times 1.2 plus 0.001, p95 latency at or below
    stable times 1.15, eval pass rate at or above stable minus 0.02,
    and cost per request at or below stable times 1.10. A canary that
    passes latency and errors but fails eval pass rate or cost is
    still a rollback — those are the two failures only a language
    model deployment can have, and a classic service canary would
    have promoted it.
    """
    if canary["requests"] < 2_000:
        return "wait: not enough canary traffic yet"
    checks = {
        "error rate": canary["error_rate"] <= stable["error_rate"] * 1.2 + 0.001,
        "p95 latency": canary["p95_ms"] <= stable["p95_ms"] * 1.15,
        "eval pass rate": canary["eval_pass"] >= stable["eval_pass"] - 0.02,
        "cost/request": canary["cost_per_request"]
        <= stable["cost_per_request"] * 1.10,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if not failed:
        return "promote"
    return "roll back: " + ", ".join(failed)


def main() -> int:
    """Run the eval suite and exit 0 on pass, 1 on regression."""
    sys.path.insert(0, str(HERE))
    from grader import StepLevelGrader  # noqa: E402
    from trajectory import GoldenDAG, Trajectory  # noqa: E402

    dag = GoldenDAG.from_json_file(DATA / "golden_refund_dag.json")
    dataset = load_json(DATA / "trajectories.json")
    expected = {
        case["case_id"]: case["expected_outcome"] for case in dataset["cases"]
    }
    trajectories = [Trajectory.from_dict(c) for c in dataset["cases"]]
    grader = StepLevelGrader(dag, expected)

    results = run_suite(grader, trajectories)
    baseline = load_json(DATA / "baseline.json")
    verdict = gate(results, baseline)

    report = {"results": results, "baseline": baseline, "gate": verdict}
    with open(HERE / "eval_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print(f"cases: {results['n_cases']}, pass rate: {results['pass_rate']:.2f}")
    for name, score in sorted(results["criteria"].items()):
        print(f"  {name}: {score:.2f}")
    for case in results["cases"]:
        mark = "PASS" if case["passed"] else "FAIL"
        print(f"  [{mark}] {case['case_id']}" + (f" — {case['first_failure']}" if case["first_failure"] else ""))
    if verdict["passed"]:
        print("GATE: PASS")
        return 0
    print("GATE: FAIL")
    for breach in verdict["breaches"]:
        print(f"  - {breach}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
