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

import json
import os
import urllib.request


class MockJudge:
    """A deterministic stand-in judge for offline work.

    It grades only what can be decided by rules: the final message
    must mention the refund amount, must not contain a placeholder,
    and must not apologise for an error that did not happen. Use it
    to develop the grading pipeline and the continuous-integration
    gate without spending anything; swap in :class:`LLMJudge` when
    you need judgement rather than pattern matching.
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


class LLMJudge:
    """A judge that calls a hosted model through an OpenAI-compatible API.

    This is a real implementation, written against the standard
    library only, and it needs an API key to do anything: set
    ``JUDGE_API_KEY`` and, if your provider is not OpenAI, set
    ``JUDGE_BASE_URL`` to its chat-completions endpoint. Without a
    key it raises a clear error instead of silently grading nothing.
    The judge model should be pinned by exact model id, and it should
    not be the same model that produced the trajectories being graded,
    or self-preference bias will inflate the scores.

    TODO for production use: log the judge's raw response alongside
    the verdict, so a disputed grade can be replayed, and run each
    case twice with the candidate order swapped when you use the
    judge for pairwise comparisons.
    """

    def __init__(
        self,
        model: str = "gpt-5-mini",
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        self.model = model
        self.base_url = base_url or os.environ.get(
            "JUDGE_BASE_URL", "https://api.openai.com/v1"
        )
        self.api_key = api_key or os.environ.get("JUDGE_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "LLMJudge needs an API key: set the JUDGE_API_KEY "
                "environment variable, or use MockJudge for offline work."
            )

    def grade(self, trajectory_text: str, rubric: str) -> dict:
        """Grade one trajectory against a rubric, temperature zero."""
        prompt = (
            "You are a strict evaluator. Grade the agent trajectory below "
            "against the rubric. Reply with JSON only, in exactly this shape: "
            '{"label": "pass" or "fail", "score": a number from 0 to 1, '
            '"rationale": "one sentence"}.\n\n'
            f"RUBRIC:\n{rubric}\n\nTRAJECTORY:\n{trajectory_text}"
        )
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        verdict = json.loads(content)
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
