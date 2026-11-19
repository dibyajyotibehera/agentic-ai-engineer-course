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
the pipeline runs offline. :class:`HHEMFaithfulness` is the stub
where the real classifier plugs in.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9]+")


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


def context_recall_score(reference: str, retrieved_contexts: list[str]) -> dict:
    """Score retrieval against a reference answer.

    Context recall is the fraction of claims in the *reference* that
    the retrieved context supports, so a low score blames retrieval,
    not the generator: the answer could not have been grounded because
    the evidence was never fetched.
    """
    context = " ".join(retrieved_contexts)
    claims = split_claims(reference)
    verdicts = [(claim, mock_entails(claim, context)) for claim in claims]
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
            "mock_entails with the classifier. See references.md."
        )
