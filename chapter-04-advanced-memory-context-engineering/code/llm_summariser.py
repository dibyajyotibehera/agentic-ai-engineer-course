"""Real model summarisation for Chapter 4.

This is the module that replaces the two places in this chapter
where a head-truncation used to stand in for a summary:

* compaction of a long transcript (``compaction.compact_with_llm``),
* the ``summarise`` strategy of tool-result pruning
  (``compaction.prune_tool_result``) and stage 2 of
  ``token_budget.TokenBudget.compress``.

Both paths call a real model through ``llm_client.LLMClient``,
following the same pattern as Chapter 7's ``injection_judge.py``:
the judge model, a fixed prompt, temperature zero, and a small
``max_tokens`` cap, because a summary is a short rewriting task,
not a reasoning task. The summariser uses the judge model
(``LLM_JUDGE_MODEL``, which defaults to the configured model) for
exactly that reason.

Two guarantees the callers rely on, and that the summariser itself
does not provide:

* Load-bearing facts are appended verbatim by the *caller*
  (``compact_with_llm``), not by the model. The model is asked to
  summarise only the non-load-bearing turns, so a fact the caller
  named can never be paraphrased away by the model; the caller then
  audits the assembled summary against the named list.
* A failed or empty model reply raises ``LLMError`` instead of
  falling back to a truncated head. A compaction that silently
  pretends is worse than one that fails loudly.

Needs a connected model (key-based, the default, or
``LLM_BACKEND=bedrock`` — see "Connect a model" in the root README).
The deterministic, offline
paths in ``compaction.py`` and ``token_budget.py`` still work
without one; they label their output as a head excerpt, not a
summary.
"""

from __future__ import annotations

from llm_client import LLMClient, LLMError


def summarise_text(
    client: LLMClient,
    text: str,
    *,
    instruction: str,
    max_tokens: int = 1000,
) -> str:
    """Summarise ``text`` with a real model call, per ``instruction``.

    Temperature zero and the judge (cheapest) route: a summary must
    be faithful and short, and there is nothing to sample for.
    ``max_tokens`` is kept generous on purpose: this model spends
    part of its budget on hidden reasoning tokens, and a small
    budget can come back with empty content. An empty reply is
    retried once with a doubled budget; if it is still empty the
    call raises ``LLMError`` instead of returning one.
    """
    budget = max_tokens
    for _attempt in range(2):
        reply = client.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You summarise agent context faithfully and briefly. "
                        "Keep every decision, open question, figure and "
                        "failure the text states. Never invent a fact, a "
                        "figure or a decision that is not in the text. "
                        "Reply with the summary only, no preamble."
                    ),
                },
                {"role": "user", "content": instruction + "\n\nTEXT:\n" + text},
            ],
            model=client.judge_model,
            temperature=0.0,
            max_tokens=budget,
        )
        summary = reply.strip()
        if summary:
            return summary
        budget *= 2
    raise LLMError("Model returned an empty summary after a retry")


def summarise_transcript_turns(
    client: LLMClient,
    turns_text: str,
    *,
    max_tokens: int = 1500,
) -> str:
    """Summarise the non-load-bearing turns of a transcript.

    The caller passes only the turns the model is allowed to
    compress (load-bearing and recent turns stay verbatim in the
    caller). The model is told to keep decisions, open questions,
    still-needed tool conclusions and approaches already tried that
    failed, and to drop consumed tool output and chatter.
    """
    return summarise_text(
        client,
        turns_text,
        instruction=(
            "Summarise these earlier turns of a support-agent "
            "transcript into a compact state note of a few short "
            "bullet lines. Keep every decision and its reason, every "
            "open question, every tool conclusion the plan still "
            "needs, and every approach already tried that did not "
            "work. Drop verbatim tool output that has already been "
            "consumed, superseded plans, and chatter."
        ),
        max_tokens=max_tokens,
    )


def summarise_tool_result(
    client: LLMClient,
    text: str,
    *,
    max_tokens: int = 1000,
) -> str:
    """Summarise one tool result with a real model call.

    Keeps the conclusion and the figures the next step depends on
    (plan names, dates, amounts, identifiers); drops the bulk.
    """
    return summarise_text(
        client,
        text,
        instruction=(
            "Summarise this tool output in two or three short lines: "
            "its conclusion, and the exact figures, names, dates and "
            "identifiers the next step depends on. Keep figures "
            "exact; do not round them and do not invent any."
        ),
        max_tokens=max_tokens,
    )
