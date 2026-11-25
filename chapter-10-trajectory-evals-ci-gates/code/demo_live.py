"""Live demo: one real trajectory graded by the real judge.

This is the chapter's small live path — the deterministic suite in
``test_trajectory.py`` stays the default, because a gate has to give
the same verdict on the same code twice, and a model judge does not.
This script shows what the model layer actually does, on one real
trajectory and one real faithfulness check, and reports what it cost.

Run from this directory with a connected model — see "Connect a
model" in the root README — key-based:

    LLM_API_KEY=<redacted> LLM_BASE_URL=<your provider's API base URL> LLM_MODEL=<your model name> python demo_live.py

or with AWS Bedrock:

    LLM_BACKEND=bedrock LLM_BEDROCK_MODEL_ID=<your inference profile ID> python demo_live.py

The client is shared with Chapter 7 (``llm_client.py``), so the
configuration is the same.
"""

from __future__ import annotations

import json
from pathlib import Path

from faithfulness import LLMClaimChecker, faithfulness_score
from judge import LLMJudge, trajectory_to_text
from llm_client import LLMClient
from trajectory import Trajectory

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

RUBRIC = (
    "The run must check eligibility before issuing any refund, "
    "the refund amount and order must match what eligibility "
    "authorised, and the run must send a confirmation after the "
    "refund. A run that refunds before the eligibility check fails, "
    "even when the amount is correct."
)

# The trajectories in data/trajectories.json carry no customer-facing
# message text — they are tool-call records — so the judge here
# grades the run itself: its order, its arguments and its
# confirmation. The message criteria the MockJudge grades (an amount
# stated in the final message, no placeholder, no false apology) are
# for final messages, and they do not apply to a bare tool record;
# a judge asked to grade a message that is not there can only fail
# the run for its absence, which is a rubric error, not a run error.


def main() -> None:
    client = LLMClient()

    dataset = json.loads((DATA / "trajectories.json").read_text(encoding="utf-8"))
    cases = {c["case_id"]: Trajectory.from_dict(c) for c in dataset["cases"]}

    judge = LLMJudge(client)
    for case_id in ("refund-golden", "refund-ordering-error"):
        verdict = judge.grade(trajectory_to_text(cases[case_id]), RUBRIC)
        print(f"Real judge on {case_id}:")
        print(f"  label: {verdict['label']}")
        print(f"  score: {verdict['score']}")
        print(f"  rationale: {verdict['rationale']}\n")

    checker = LLMClaimChecker(client)
    result = faithfulness_score(
        response=(
            "Einstein was born in Germany. "
            "Einstein won the Nobel Prize in 1955."
        ),
        retrieved_contexts=[
            "Albert Einstein (born 14 March 1879) was a German-born "
            "theoretical physicist."
        ],
        entails=checker.entails,
    )
    print("\nReal claim checker on the Ragas worked example:")
    for claim in result["claims"]:
        mark = "supported" if claim["supported"] else "unsupported"
        print(f"  [{mark}] {claim['claim']}")
    print(
        f"  faithfulness: {result['faithfulness']} "
        f"({result['supported']}/{result['total']} claims supported)"
    )

    print(
        f"\nTokens: {client.total_prompt_tokens} prompt + "
        f"{client.total_completion_tokens} completion"
    )
    print(f"Cost: ${client.total_cost_usd:.4f}")


if __name__ == "__main__":
    main()
