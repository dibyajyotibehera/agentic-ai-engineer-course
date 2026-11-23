"""Claim-based faithfulness scoring for retrieval-grounded answers.

Faithfulness, as defined by the Ragas evaluation library, is the
fraction of claims in a response that the retrieved context supports:

    faithfulness = supported claims / total claims

The worked example in the Ragas documentation makes the arithmetic
concrete: an answer that makes two claims, one of which the context
supports, scores 1 / 2 = 0.5. The companion metric, context recall,
runs the fraction the other way: it is the fraction of claims in the
*reference* answer that the retrieved context supports, so it
measures whether retrieval found what the answer needed, rather than
whether the answer stayed inside what retrieval found.

Two pieces of machinery sit underneath the formula. First, the
response has to be broken into claims, which is a language-model job
in Ragas. Second, each claim has to be checked against the context,
which is where a small classifier earns its keep: Vectara's
HHEM-2.1-Open is a free, small, open-source T5 classifier trained to
detect hallucinations, and Ragas ships a ``FaithfulnesswithHHEM``
variant that uses it for exactly this claim-check step instead of
another language-model call. That matters in a continuous-integration
gate, because a classifier runs locally, costs nothing per call, and
gives the same answer every time, while a language-model judge does
not.

This module implements the formula with a deliberately naive
stand-in for both pieces — sentence splitting for claim extraction,
keyword overlap for entailment — so the arithmetic is visible and
the pipeline runs offline. The keyword-overlap check,
:func:`mock_entails`, stays as the deterministic default because the
scoring arithmetic has to be readable and testable without a model
call, and because a continuous-integration gate needs a grader that
gives the same answer every time. But the claim check is also the
place where a real model belongs, so :class:`LLMClaimChecker` does
the same check with a real model call through the shared
``llm_client.py`` client: pass its ``entails`` method into
:func:`faithfulness_score` and the same arithmetic runs over real
judgements instead of keyword overlap. :class:`HHEMFaithfulness`
remains the stub where the local classifier plugs in.
"""

from __future__ import annotations

import re

from llm_client import LLMClient

_WORD = re.compile(r"[a-z0-9]+")

# Same reasoning as the judge in judge.py: the verdict is short, but
# this model spends much of its token budget on hidden reasoning, so
# a small budget can come back empty.
_CLAIM_MAX_TOKENS = 1500


def split_claims(response: str) -> list[str]:
    """Split a response into claims, one per sentence.

    This is a stand-in, not a serious claim extractor: real claim
    extraction needs a model, because a single sentence can carry
    two claims and a claim can span two sentences. It is kept here so
    the scoring arithmetic can be read and tested without a model.
    """
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", response.strip()) if s.strip()]


def _keywords(text: str) -> set[str]:
    stopwords = {
        "the", "a", "an", "is", "was", "are", "were", "of", "on", "in",
        "to", "and", "or", "for", "with", "by", "at", "it", "its",
    }
    return {w for w in _WORD.findall(text.lower()) if w not in stopwords}


def mock_entails(claim: str, context: str) -> bool:
    """Toy entailment check: a claim is supported if its keywords appear.

    Keyword overlap is a weak proxy for entailment — it cannot tell a
    supported claim from a contradicted one that happens to reuse the
    same words — and it is here only so the faithfulness arithmetic
    runs offline. In the real pipeline this function is replaced by
    HHEM-2.1-Open or a language-model claim check.
    """
    claim_words = _keywords(claim)
    if not claim_words:
        return True
    context_words = _keywords(context)
    return len(claim_words & context_words) / len(claim_words) >= 0.5


class LLMClaimChecker:
    """Claim checking with a real model, through the shared client.

    This is the claim-check step the way Ragas runs it: a model reads
    one claim and the retrieved context and decides whether the
    context supports the claim. It grades on the cheap ``:floor``
    route at temperature zero, and it returns a boolean, so it plugs
    straight into :func:`faithfulness_score` and
    :func:`context_recall_score` as the ``entails`` argument — the
    same arithmetic, over real judgements instead of keyword overlap.

    A claim check is a narrow yes-or-no question, which is also why
    the deterministic keyword check stays the default: a gate that
    runs on every pull request needs the same answer every time, and
    a model judge does not give that. Use the model check when you
    are measuring the quality of a run, not when you are gating on it.
    """

    def __init__(self, client: LLMClient | None = None):
        self.client = client or LLMClient()

    def entails(self, claim: str, context: str) -> bool:
        """True when the context supports the claim, judged by a model."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You decide whether a piece of retrieved context "
                    "supports a claim. Reply with JSON only, in exactly "
                    'this shape: {"supported": true or false, '
                    '"reason": "one sentence"}. Supported means the '
                    "context states the claim, or the claim follows "
                    "directly from what the context states. A claim "
                    "that adds a fact the context never mentions is "
                    "not supported, even if it is true in the world."
                ),
            },
            {
                "role": "user",
                "content": f"CONTEXT:\n{context}\n\nCLAIM:\n{claim}",
            },
        ]
        verdict = self.client.structured(
            messages,
            model=self.client.judge_model,
            temperature=0.0,
            max_tokens=_CLAIM_MAX_TOKENS,
            required_fields=("supported", "reason"),
        )
        return bool(verdict["supported"])


def faithfulness_score(
    response: str, retrieved_contexts: list[str], entails=mock_entails
) -> dict:
    """Score a response against its retrieved context.

    Returns the score, the per-claim verdicts, and the counts, so a
    failing case can show exactly which claim was unsupported rather
    than hiding behind a single number.
    """
    context = " ".join(retrieved_contexts)
    claims = split_claims(response)
    verdicts = [(claim, entails(claim, context)) for claim in claims]
    supported = sum(1 for _, ok in verdicts if ok)
    total = len(verdicts)
    return {
        "faithfulness": (supported / total) if total else 1.0,
        "supported": supported,
        "total": total,
        "claims": [{"claim": claim, "supported": ok} for claim, ok in verdicts],
    }


def context_recall_score(
    reference: str, retrieved_contexts: list[str], entails=mock_entails
) -> dict:
    """Score retrieval against a reference answer.

    Context recall is the fraction of claims in the *reference* that
    the retrieved context supports, so a low score blames retrieval,
    not the generator: the answer could not have been grounded because
    the evidence was never fetched. Pass an
    :class:`LLMClaimChecker`'s ``entails`` method to score with a real
    model instead of the keyword check, the same way as for
    :func:`faithfulness_score`.
    """
    context = " ".join(retrieved_contexts)
    claims = split_claims(reference)
    verdicts = [(claim, entails(claim, context)) for claim in claims]
    supported = sum(1 for _, ok in verdicts if ok)
    total = len(verdicts)
    return {
        "context_recall": (supported / total) if total else 1.0,
        "supported": supported,
        "total": total,
        "claims": [{"claim": claim, "supported": ok} for claim, ok in verdicts],
    }


class HHEMFaithfulness:
    """Faithfulness scoring with Vectara's HHEM-2.1-Open classifier.

    TODO: this needs the ``transformers`` package and the model
    weights (``vectara/hallucination_evaluation_model`` on Hugging
    Face, Apache 2.0 licence). The intended implementation loads the
    sequence-classification model once, then scores each
    (context, claim) pair and treats a score above 0.5 as supported.
    It is a stub here because downloading a model is not something a
    chapter's core exercise should require; the mock scorer above
    covers the same arithmetic offline.
    """

    MODEL_ID = "vectara/hallucination_evaluation_model"

    def __init__(self) -> None:
        raise RuntimeError(
            "HHEMFaithfulness is a stub in this chapter: install "
            "`transformers` and `torch`, then load "
            f"{self.MODEL_ID!r} and implement score() to replace "
            "mock_entails with the classifier. See references.md. "
            "For a claim check that works now, use LLMClaimChecker "
            "in this file, which grades claims with a real model."
        )
