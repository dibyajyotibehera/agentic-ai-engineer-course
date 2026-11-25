"""Language-model-as-a-judge grading, with its biases named up front.

A judge is a second model given a rubric and asked to grade a run.
It is the only grader in this chapter that can judge things code
cannot see — tone, helpfulness, whether an explanation actually
answers the question — and it is also the least trustworthy grader
here, for reasons that are structural rather than fixable by a better
prompt:

- **Verbosity bias.** Judges systematically prefer longer answers,
  even when the extra words add nothing.
- **Position bias.** Given two candidate answers, a judge favours
  whichever is presented first; swap the order and the verdict
  often flips.
- **Self-preference.** A judge rates outputs from its own model
  family more highly than identical outputs from another family.
- **Anchoring.** A judge that has seen a high score early in a
  batch grades the rest of the batch more generously.

The defences are boring and they work: grade against a fixed rubric
with named criteria instead of an open-ended "is this good?", keep
the judge's temperature at zero, run the same case more than once
and look at the variance, and — the one that matters most — measure
the judge against human labels on a held-out set before you trust
it, and re-measure it whenever the judge model or the rubric
changes. :func:`measure_agreement` does that measurement.

Cost is the reason a judge is worth building at all. In LangChain's
September 2026 head-to-head study of judges for agent evaluations,
a specialised typed judge (TypeSafe AI's Jev) matched the human
pass/fail label on all 500 repeated decisions at a total cost of
$0.34, against $28.17 for Claude Sonnet 4.6 as judge — roughly 83
times cheaper — with 92 to 913 times lower variance than the
large-language-model judges. The study was narrow (one weather
agent, five cases, one human reviewer), so treat the numbers as
evidence that the *shape* of the solution matters — a small,
calibrated, typed decision instead of free-text reasoning — not as
proof that any cheap judge is good. See the chapter references for
the study itself.
"""

from __future__ import annotations

from llm_client import LLMClient

# The judge grades on the configured judge model (LLM_JUDGE_MODEL,
# defaulting to the main model): a verdict is a short
# classification, not a reasoning task. The budget is kept
# generous anyway, because a model can spend a large share of its
# token budget on hidden reasoning and a small budget can come back
# empty — the verdict itself is only a few dozen tokens.
_JUDGE_MAX_TOKENS = 2000


class MockJudge:
    """A deterministic stand-in judge for offline work.

    It grades only what can be decided by rules: the final message
    must mention the refund amount, must not contain a placeholder,
    and must not apologise for an error that did not happen. Use it
    to develop the grading pipeline and the continuous-integration
    gate without spending anything; swap in :class:`LLMJudge` when
    you need judgement rather than pattern matching.

    This stand-in is deliberate and it stays. The chapter measures
    judges — :func:`measure_agreement` scores a judge against human
    labels — and that measurement needs a judge whose verdicts are
    fixed and repeatable, so the agreement arithmetic can be read
    and tested without a model call whose verdict might change
    between runs. A real model judge is the thing being measured
    here, not the thing doing the measuring; the fixed judge is the
    instrument, and an instrument that wobbles cannot calibrate
    anything. The real judge lives next to it in :class:`LLMJudge`.
    """

    def grade(self, trajectory_text: str, rubric: str) -> dict:
        del rubric  # the mock grades fixed, named criteria, not prose
        problems = []
        if "$" not in trajectory_text:
            problems.append("final message states no refund amount")
        if "TODO" in trajectory_text or "[insert" in trajectory_text:
            problems.append("final message contains a placeholder")
        if "sorry for the error" in trajectory_text.lower():
            problems.append("apologises for an error that did not happen")
        return {
            "label": "fail" if problems else "pass",
            "score": 0.0 if problems else 1.0,
            "rationale": (
                "; ".join(problems) if problems else "amount stated, no placeholders"
            ),
        }


def trajectory_to_text(trajectory) -> str:
    """Render a trajectory as the plain text a judge reads.

    One line per step — the tool called and what came back — plus the
    final outcome, so the judge sees the run in the order it happened
    rather than a summary of it.
    """
    lines = [f"Input: {trajectory.input}"]
    for step in trajectory.steps:
        if step.kind == "tool_call":
            lines.append(
                f"- called {step.tool} with {step.args}, "
                f"result: {step.result}"
            )
        elif step.text:
            lines.append(f"- {step.kind}: {step.text}")
        else:
            lines.append(f"- {step.kind}")
    lines.append(f"Final outcome: {trajectory.final_outcome}")
    return "\n".join(lines)


class LLMJudge:
    """A judge that grades through a real model via :class:`LLMClient`.

    It uses the shared client from ``llm_client.py`` — the same client
    Chapter 7's guardrails use — so it reads its configuration from
    the environment (key-based: ``LLM_API_KEY`` / ``LLM_BASE_URL`` /
    ``LLM_MODEL``; or AWS Bedrock: ``LLM_BACKEND=bedrock`` with
    ``LLM_BEDROCK_MODEL_ID`` — see "Connect a model" in the root
    README) and raises a clear error when no backend is configured
    instead of silently grading nothing.
    The judge grades on the configured judge model (``LLM_JUDGE_MODEL``,
    defaulting to the main model), temperature zero,
    and returns a JSON verdict with a label, a score and a rationale.

    The judge model should be pinned by exact model id, and it should
    not be the same model that produced the trajectories being graded,
    or self-preference bias will inflate the scores.

    TODO for production use: log the judge's raw response alongside
    the verdict, so a disputed grade can be replayed, and run each
    case twice with the candidate order swapped when you use the
    judge for pairwise comparisons.
    """

    def __init__(self, client: LLMClient | None = None):
        self.client = client or LLMClient()

    def grade(self, trajectory_text: str, rubric: str) -> dict:
        """Grade one trajectory against a rubric, temperature zero."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a strict evaluator. Grade the agent "
                    "trajectory below against the rubric. Reply with "
                    "JSON only, in exactly this shape: "
                    '{"label": "pass" or "fail", "score": a number '
                    'from 0 to 1, "rationale": "one sentence"}.'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"RUBRIC:\n{rubric}\n\n"
                    f"TRAJECTORY:\n{trajectory_text}"
                ),
            },
        ]
        verdict = self.client.structured(
            messages,
            model=self.client.judge_model,
            temperature=0.0,
            max_tokens=_JUDGE_MAX_TOKENS,
            required_fields=("label", "score", "rationale"),
        )
        verdict.setdefault("score", 1.0 if verdict.get("label") == "pass" else 0.0)
        return verdict


def measure_agreement(judge_labels: list[str], human_labels: list[str]) -> dict:
    """Agreement between a judge and human labels on the same cases.

    Returns the agreement rate and the two error directions separately,
    because they mean different things: judge false passes let bad runs
    ship, while judge false fails waste a human's time on re-review.
    A judge is only trusted for a gate once its agreement with humans
    is measured on cases it has not seen, and the false-pass rate is
    the number that decides whether it may block a release.
    """
    if len(judge_labels) != len(human_labels):
        raise ValueError("judge and human label lists must be the same length")
    total = len(human_labels)
    if total == 0:
        return {"agreement": 0.0, "false_passes": 0, "false_fails": 0, "n": 0}
    agree = sum(j == h for j, h in zip(judge_labels, human_labels))
    false_passes = sum(
        j == "pass" and h == "fail" for j, h in zip(judge_labels, human_labels)
    )
    false_fails = sum(
        j == "fail" and h == "pass" for j, h in zip(judge_labels, human_labels)
    )
    return {
        "agreement": agree / total,
        "false_passes": false_passes,
        "false_fails": false_fails,
        "n": total,
    }
