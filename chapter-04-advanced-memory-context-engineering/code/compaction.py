"""Agentic compaction, tool-result pruning, and context-rot detection.

Companion to ``token_budget.py`` for Chapter 4, covering the long-run
defences from the last section of the lesson: compaction, tool-result
pruning, context rot, and lazy loading.

The three pieces answer three different questions, so they are kept
separate:

* :func:`should_compact` -- *when* to compact: a token-count check
  against the MemGPT pressure point (warn at about 70% of the budget,
  arXiv:2310.08560). It does not decide *what* survives;
  that is the caller's job, via :func:`compact`.
* :func:`compact` -- *what* survives a compaction: the load-bearing
  facts the caller names must appear verbatim in the compacted summary,
  and the function refuses to call the result complete unless they do.
  A decision silently dropped is the failure mode this guards against.
* :func:`prune_tool_result` -- *how* to shrink a large tool output:
  summarise it, keep only the fields the next step needs, pass a
  pointer to the stored full result, or expire it after a single use.
* :func:`detect_rot` -- *whether* a transcript has rotted: a simple
  heuristic over how much of the context is older than N turns, plus a
  flag for instructions known to be stale (superseded). It is a
  heuristic, not a diagnosis: it tells you to look, not what is wrong.

Token counts here use the same rough estimate as ``memory_store.py``
(about four characters per token). It is an estimate for budgeting in
the exercises, not a tokenizer; replace it with a real tokenizer when
you wire in a model. The 5,000-token tool output summarised to roughly
500 tokens in the lesson is a worked example, not a measured figure.

Two summarisation paths live in this module, and they are not the
same thing. :func:`compact` and the default ``summarise`` strategy
of :func:`prune_tool_result` are deterministic and offline: they keep
verbatim turns and reduce the rest to a head excerpt, which is
labelled as an excerpt, not a summary. :func:`compact_with_llm` and
:func:`prune_tool_result_with_llm` call a real model through
``llm_summariser.py`` (judge model, temperature zero, small
``max_tokens``) and need a connected model (key-based, the default,
or ``LLM_BACKEND=bedrock`` — see "Connect a model" in the root
README). Run the
live paths with ``python compaction.py --live``.

No API key and no paid service are needed to run the deterministic
paths in this module. Standard library only, plus ``llm_client.py``
and ``llm_summariser.py`` in this directory for the live paths.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable

from llm_client import LLMClient
from llm_summariser import summarise_tool_result, summarise_transcript_turns

# MemGPT's pressure constant (arXiv:2310.08560): warn at
# about 70% of the context window. Compaction is triggered at that
# point, not at 100%, because compacting under duress leaves the model
# choosing what to keep while it is already under token pressure.
COMPACTION_WARNING_FRACTION = 0.70

# Default summary length for the summarise strategy, in characters.
SUMMARY_HEAD_CHARS = 120


def estimate_tokens(text: str) -> int:
    """Rough token estimate: about four characters per token.

    Mirrors ``memory_store.estimate_tokens`` so the two modules agree;
    both are estimates, not tokenizers.
    """
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Compaction: when, and what survives
# ---------------------------------------------------------------------------


@dataclass
class TranscriptTurn:
    """One turn of the live transcript or scratchpad.

    ``load_bearing`` marks a turn the plan still depends on -- a decision
    and its reason, an open question, a tool result the next step needs,
    or a failure signature (an approach already tried that did not work).
    Load-bearing turns survive compaction verbatim; everything else is
    summarised or dropped. ``is_instruction`` marks a turn that tells the
    agent what to do, as opposed to one that reports what happened:
    instructions are what rot contaminates, so the rot detector treats
    them separately.
    """

    text: str
    role: str = "assistant"  # "user" | "assistant" | "tool" | "system"
    load_bearing: bool = False
    is_instruction: bool = False


@dataclass
class CompactionDecision:
    should_compact: bool
    pressure: float  # fraction of the budget consumed
    reason: str


def should_compact(
    total_tokens: int,
    budget_tokens: int,
    *,
    warning_fraction: float = COMPACTION_WARNING_FRACTION,
) -> CompactionDecision:
    """Check whether the transcript should be compacted now.

    Returns a decision naming the pressure and the reason, so the
    trigger is auditable rather than a silent threshold crossing.
    """
    if budget_tokens <= 0:
        raise ValueError("budget_tokens must be positive")
    pressure = total_tokens / budget_tokens
    if pressure >= warning_fraction:
        return CompactionDecision(
            True,
            pressure,
            f"pressure {pressure:.0%} at or above the {warning_fraction:.0%} "
            "compaction point: rewrite the transcript into a compact state "
            "summary and restart the loop from it",
        )
    return CompactionDecision(
        False,
        pressure,
        f"pressure {pressure:.0%} below the {warning_fraction:.0%} compaction point",
    )


@dataclass
class CompactedState:
    """The result of a compaction: a state summary plus an audit trail."""

    summary: str
    tokens_before: int
    tokens_after: int
    turns_kept_verbatim: int
    turns_summarised: int
    turns_dropped: int
    load_bearing_preserved: list[str]  # the load-bearing facts, as kept
    load_bearing_missing: list[str]  # named facts that did not survive

    @property
    def complete(self) -> bool:
        """True only if every named load-bearing fact survived.

        A compaction that silently drops a decision is not a successful
        compaction, however small the summary is.
        """
        return not self.load_bearing_missing


def compact(
    transcript: list[TranscriptTurn],
    load_bearing_facts: list[str],
    *,
    keep_recent_turns: int = 2,
) -> CompactedState:
    """Rewrite the transcript into a compact state summary.

    Load-bearing turns are kept verbatim. The most recent turns are
    kept verbatim too, because the next step is usually continuing
    them. Every other turn is reduced to a one-line summary, and
    verbatim tool outputs that have already been consumed are dropped
    rather than summarised -- their conclusions, if they matter, are
    load-bearing facts and survive as facts, not as raw output.

    ``load_bearing_facts`` is the caller's explicit list of facts that
    must survive (decisions, open questions, still-needed tool results,
    failure signatures). Each one is checked against the summary, and
    any that is missing is reported in ``load_bearing_missing`` and
    makes ``complete`` False. Pass the list; do not let the compactor
    guess what matters.
    """
    tokens_before = sum(estimate_tokens(turn.text) for turn in transcript)
    lines: list[str] = ["STATE SUMMARY (compacted transcript):"]
    kept_verbatim = 0
    summarised = 0
    dropped = 0

    def flush_run(run_role: str, run_text: str, run_count: int) -> None:
        """Emit one summarised line for a run of identical non-verbatim
        turns, collapsing repeats (a repeated instruction or status
        update is one line, not one line per repetition)."""
        nonlocal summarised, dropped
        if run_role == "tool":
            # Verbatim tool output already consumed: drop it. If its
            # conclusion matters, it survives as a load-bearing fact.
            dropped += run_count
            return
        if run_count > 1:
            lines.append(
                f"- [{run_role}] (earlier, x{run_count}) "
                f"{run_text[:SUMMARY_HEAD_CHARS]}..."
            )
        else:
            lines.append(f"- [{run_role}] (earlier) {run_text[:SUMMARY_HEAD_CHARS]}...")
        summarised += run_count

    run_role: str | None = None
    run_text: str | None = None
    run_count = 0
    for index, turn in enumerate(transcript):
        is_recent = index >= len(transcript) - keep_recent_turns
        if turn.load_bearing or is_recent:
            if run_role is not None:
                flush_run(run_role, run_text or "", run_count)
                run_role = run_text = None
                run_count = 0
            lines.append(f"- [{turn.role}] {turn.text}")
            kept_verbatim += 1
        elif run_role == turn.role and run_text == turn.text:
            run_count += 1
        else:
            if run_role is not None:
                flush_run(run_role, run_text or "", run_count)
            run_role, run_text, run_count = turn.role, turn.text, 1
    if run_role is not None:
        flush_run(run_role, run_text or "", run_count)

    lines.append("LOAD-BEARING FACTS:")
    for fact in load_bearing_facts:
        lines.append(f"- {fact}")
    summary = "\n".join(lines)

    missing = [fact for fact in load_bearing_facts if fact not in summary]
    return CompactedState(
        summary=summary,
        tokens_before=tokens_before,
        tokens_after=estimate_tokens(summary),
        turns_kept_verbatim=kept_verbatim,
        turns_summarised=summarised,
        turns_dropped=dropped,
        load_bearing_preserved=[f for f in load_bearing_facts if f not in missing],
        load_bearing_missing=missing,
    )


def compact_with_llm(
    client: LLMClient,
    transcript: list[TranscriptTurn],
    load_bearing_facts: list[str],
    *,
    keep_recent_turns: int = 2,
    max_tokens: int = 1500,
) -> CompactedState:
    """Rewrite the transcript into a compact state summary, with a real model.

    The live version of :func:`compact`, with the same contract and the
    same audit: load-bearing turns and the most recent turns are kept
    verbatim, and the load-bearing facts the caller named are appended
    verbatim and checked, so ``complete`` is False if any is missing.
    The difference is the middle: instead of reducing every other turn
    to a head excerpt, the non-verbatim turns are summarised by a real
    model call (``llm_summariser.summarise_transcript_turns``, cheap
    judge route, temperature zero). The model never sees the
    load-bearing facts as material to paraphrase -- they are appended
    by this function after the model replies -- so a fact cannot be
    silently reworded away. A failed or empty model reply raises
    instead of falling back to the excerpt path.
    """
    tokens_before = sum(estimate_tokens(turn.text) for turn in transcript)
    verbatim: list[TranscriptTurn] = []
    compressible: list[TranscriptTurn] = []
    dropped = 0
    for index, turn in enumerate(transcript):
        is_recent = index >= len(transcript) - keep_recent_turns
        if turn.load_bearing or is_recent:
            verbatim.append(turn)
        elif turn.role == "tool":
            # Verbatim tool output already consumed: dropped, not
            # summarised. Its conclusions survive as load-bearing
            # facts, not as raw output -- same rule as compact().
            dropped += 1
        else:
            compressible.append(turn)

    lines: list[str] = ["STATE SUMMARY (compacted transcript):"]
    for turn in verbatim:
        lines.append(f"- [{turn.role}] {turn.text}")
    summarised = 0
    if compressible:
        turns_text = "\n".join(f"[{turn.role}] {turn.text}" for turn in compressible)
        model_summary = summarise_transcript_turns(
            client, turns_text, max_tokens=max_tokens
        )
        lines.append("EARLIER TURNS (model summary):")
        for line in model_summary.strip().splitlines():
            lines.append(f"- {line}" if not line.startswith("-") else line)
        summarised = len(compressible)

    lines.append("LOAD-BEARING FACTS:")
    for fact in load_bearing_facts:
        lines.append(f"- {fact}")
    summary = "\n".join(lines)

    missing = [fact for fact in load_bearing_facts if fact not in summary]
    return CompactedState(
        summary=summary,
        tokens_before=tokens_before,
        tokens_after=estimate_tokens(summary),
        turns_kept_verbatim=len(verbatim),
        turns_summarised=summarised,
        turns_dropped=dropped,
        load_bearing_preserved=[f for f in load_bearing_facts if f not in missing],
        load_bearing_missing=missing,
    )


# ---------------------------------------------------------------------------
# Tool-result pruning
# ---------------------------------------------------------------------------


@dataclass
class PrunedResult:
    """A pruned tool result: what enters the prompt, and what it cost."""

    text: str  # what goes into the prompt
    strategy: str  # "summarise" | "fields" | "pointer" | "expiry"
    tokens_before: int
    tokens_after: int
    stored_full_text: str | None = None  # the full result, kept out of context

    @property
    def tokens_saved(self) -> int:
        return self.tokens_before - self.tokens_after


def prune_tool_result(
    text: str,
    *,
    strategy: str = "summarise",
    keep_fields: list[str] | None = None,
    pointer_id: str | None = None,
    expired: bool = False,
    summariser: Callable[[str], str] | None = None,
) -> PrunedResult:
    """Prune one tool result before it enters the prompt.

    * ``summarise`` -- with ``summariser`` given (a callable taking
      the full text and returning a summary, for example a partial
      application of ``llm_summariser.summarise_tool_result`` bound
      to a client -- see :func:`prune_tool_result_with_llm`), the
      result is summarised by a real model: the conclusion and the
      figures the plan depends on. Without one, the head of the
      output is kept and labelled as a head excerpt, not a summary:
      the head is a placeholder, as in ``TokenBudget.compress``,
      and calling it a summary would overstate what it is.
    * ``fields`` -- keep only the lines naming one of ``keep_fields``;
      the rest of the record is dropped. Use when the next step needs
      two fields of a large record.
    * ``pointer`` -- store the full result out of context and pass only
      a reference. The full text is returned in ``stored_full_text`` for
      the caller to file in the episodic tier or an external store; the
      prompt carries the pointer and a one-line description.
    * ``expiry`` -- the result was only needed for one step. If
      ``expired`` is True the result is dropped entirely (zero tokens
      in the prompt); otherwise it is returned unchanged, because
      expiring a result the next step still needs is a silent loss.
    """
    tokens_before = estimate_tokens(text)
    if strategy == "summarise":
        if summariser is not None:
            pruned = summariser(text)
            return PrunedResult(pruned, strategy, tokens_before, estimate_tokens(pruned))
        pruned = (
            f"HEAD EXCERPT (not a model summary): {text[:SUMMARY_HEAD_CHARS]}... "
            "[pass a summariser for a real summary]"
        )
        return PrunedResult(pruned, strategy, tokens_before, estimate_tokens(pruned))
    if strategy == "fields":
        if not keep_fields:
            raise ValueError("fields strategy needs keep_fields")
        wanted = [field_name.lower() for field_name in keep_fields]
        kept_lines = [
            line for line in text.splitlines()
            if any(field_name in line.lower() for field_name in wanted)
        ]
        pruned = "\n".join(kept_lines) if kept_lines else "[no requested fields found]"
        return PrunedResult(pruned, strategy, tokens_before, estimate_tokens(pruned))
    if strategy == "pointer":
        if not pointer_id:
            raise ValueError("pointer strategy needs pointer_id")
        pruned = (
            f"[tool result stored as {pointer_id}; "
            f"{tokens_before} tokens held outside the prompt, "
            "retrieve it if a later step needs the detail]"
        )
        return PrunedResult(
            pruned, strategy, tokens_before, estimate_tokens(pruned),
            stored_full_text=text,
        )
    if strategy == "expiry":
        if expired:
            return PrunedResult("[expired tool result: dropped after its single use]",
                                 strategy, tokens_before, 0)
        return PrunedResult(text, strategy, tokens_before, tokens_before)
    raise ValueError(f"unknown pruning strategy: {strategy!r}")


def prune_tool_result_with_llm(
    client: LLMClient,
    text: str,
    *,
    strategy: str = "summarise",
    keep_fields: list[str] | None = None,
    pointer_id: str | None = None,
    expired: bool = False,
    max_tokens: int = 1000,
) -> PrunedResult:
    """Prune one tool result with a real model for the ``summarise`` strategy.

    Identical to :func:`prune_tool_result` for the other strategies;
    for ``summarise`` it calls the model (cheap judge route,
    temperature zero) instead of keeping a head excerpt. Needs a
    configured model backend.
    """
    return prune_tool_result(
        text,
        strategy=strategy,
        keep_fields=keep_fields,
        pointer_id=pointer_id,
        expired=expired,
        summariser=(lambda t: summarise_tool_result(client, t, max_tokens=max_tokens))
        if strategy == "summarise"
        else None,
    )


# ---------------------------------------------------------------------------
# Context rot: a heuristic detector
# ---------------------------------------------------------------------------


@dataclass
class RotReport:
    """What the rot heuristic found. A heuristic, not a diagnosis."""

    old_token_fraction: float  # fraction of tokens older than max_age_turns
    stale_instructions: list[str]  # stale instruction texts found in context
    rot_suspected: bool
    reasons: list[str]


def detect_rot(
    transcript: list[TranscriptTurn],
    *,
    max_age_turns: int = 10,
    stale_instructions: list[str] | None = None,
) -> RotReport:
    """Flag a transcript that may have rotted.

    Two signals, matching the two mechanisms in the lesson:

    * Attention dilution: the fraction of context tokens sitting in
      turns older than ``max_age_turns``. A transcript whose tokens are
      mostly old is one where the relevant facts are buried under
      volume, and the model may stop using them.
    * Behavioural contamination: any instruction turn whose text
      matches a known-stale instruction (a superseded plan, an old
      policy, a wrong intermediate conclusion the caller has named).
      A stale instruction in context is a live instruction to do the
      wrong thing, and no amount of volume reduction fixes it -- the
      instruction has to be superseded or removed.

    Rot is suspected when either signal fires: more than half the
    tokens are old, or any stale instruction is present. The thresholds
    (half the tokens, ``max_age_turns``) are worked-example defaults;
    calibrate them against your own traffic.
    """
    stale_instructions = stale_instructions or []
    total_tokens = sum(estimate_tokens(turn.text) for turn in transcript) or 1
    old_tokens = sum(
        estimate_tokens(turn.text)
        for index, turn in enumerate(transcript)
        if len(transcript) - 1 - index >= max_age_turns
    )
    old_fraction = old_tokens / total_tokens

    found_stale = [
        stale for stale in stale_instructions
        if any(stale in turn.text for turn in transcript if turn.is_instruction)
    ]

    reasons: list[str] = []
    if old_fraction > 0.5:
        reasons.append(
            f"{old_fraction:.0%} of context tokens are older than "
            f"{max_age_turns} turns (attention dilution: relevant facts "
            "are buried under volume)"
        )
    if found_stale:
        reasons.append(
            f"{len(found_stale)} stale instruction(s) still in context "
            "(behavioural contamination: a superseded instruction is "
            "still steering later steps)"
        )
    return RotReport(
        old_token_fraction=old_fraction,
        stale_instructions=found_stale,
        rot_suspected=bool(reasons),
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Lazy loading: the counterweight, as a one-function discipline
# ---------------------------------------------------------------------------


def lazy_load(needed_now: list[str], deferred: dict[str, str]) -> tuple[list[str], list[str]]:
    """Load only what the current step needs; defer the rest by pointer.

    ``needed_now`` is the material this step actually asks for;
    ``deferred`` maps a pointer name to the material held out of the
    prompt. Returns the material loaded and the pointers left deferred,
    so the caller can see exactly what was stuffed eagerly and what was
    left lazy. The discipline is the point: every entry in ``deferred``
    is a retrieval the agent has not yet paid for.
    """
    return needed_now, [f"pointer:{name}" for name in deferred]


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


def demo() -> None:
    """Run the support-copilot long-run scenario end to end."""
    print("=== Compaction trigger ===")
    budget = 4000
    used = 3100  # a long support case, mostly tool output
    decision = should_compact(used, budget)
    print(f"{used}/{budget} tokens ({decision.pressure:.0%}): {decision.reason}")

    print("\n=== Tool-result pruning ===")
    billing_record = "\n".join(
        [
            "plan: business",
            "renewal date: 2027-03-01",
            "seats: 42",
            "past invoices: " + "INV-1001 $480, " * 40,
            "usage notes: " + "daily sync at 02:00, " * 40,
        ]
    )
    for strategy, kwargs in [
        ("summarise", {}),
        ("fields", {"keep_fields": ["plan", "renewal date"]}),
        ("pointer", {"pointer_id": "billing-record-7"}),
        ("expiry", {"expired": True}),
    ]:
        pruned = prune_tool_result(billing_record, strategy=strategy, **kwargs)
        print(f"{strategy}: {pruned.tokens_before} -> {pruned.tokens_after} tokens "
              f"(saved {pruned.tokens_saved})")

    print("\n=== Context rot ===")
    transcript = [
        TranscriptTurn("Refunds within 30 days need no approval.",
                       role="system", is_instruction=True)
        for _ in range(12)
    ] + [
        TranscriptTurn("Customer asks about a refund on day 20.",
                       role="user", load_bearing=True),
        TranscriptTurn("Checking the billing record.", role="assistant"),
    ]
    # The March rule is stale: June superseded it with a 14-day rule.
    report = detect_rot(
        transcript, max_age_turns=10,
        stale_instructions=["Refunds within 30 days need no approval."],
    )
    print(f"old-token fraction: {report.old_token_fraction:.0%}")
    print(f"rot suspected: {report.rot_suspected}")
    for reason in report.reasons:
        print(f"  - {reason}")

    print("\n=== Compaction ===")
    facts = [
        "Refund policy is 14 days with manager approval (June, supersedes March).",
        "Customer's plan is business, renewal 2027-03-01.",
        "Tried re-issuing the invoice; it did not resolve the billing error.",
    ]
    state = compact(transcript, facts)
    print(f"{state.tokens_before} -> {state.tokens_after} tokens; "
          f"kept {state.turns_kept_verbatim} verbatim, "
          f"summarised {state.turns_summarised}, dropped {state.turns_dropped}")
    print(f"compaction complete (all load-bearing facts preserved): {state.complete}")

    print("\n=== Lazy loading ===")
    loaded, deferred = lazy_load(
        ["current goal: resolve the refund question"],
        {"past episodes": "12 similar tickets", "full billing record": "billing-record-7"},
    )
    print(f"loaded now: {loaded}")
    print(f"deferred: {deferred}")


def demo_live() -> None:
    """Run the support-copilot long-run scenario against a real model.

    Same scenario as :func:`demo`, but the compaction and the
    tool-result summary are real model calls, and the run reports the
    model's token usage and cost. Needs a configured model backend.
    """
    client = LLMClient()
    transcript = [
        TranscriptTurn("Refunds within 30 days need no approval.",
                       role="system", is_instruction=True)
        for _ in range(12)
    ] + [
        TranscriptTurn("Customer asks about a refund on day 20.",
                       role="user", load_bearing=True),
        TranscriptTurn("Checking the billing record.", role="assistant"),
    ]
    facts = [
        "Refund policy is 14 days with manager approval (June, supersedes March).",
        "Customer's plan is business, renewal 2027-03-01.",
        "Tried re-issuing the invoice; it did not resolve the billing error.",
    ]
    print("=== Live compaction (real model) ===")
    state = compact_with_llm(client, transcript, facts)
    print(f"{state.tokens_before} -> {state.tokens_after} tokens; "
          f"kept {state.turns_kept_verbatim} verbatim, "
          f"summarised {state.turns_summarised}, dropped {state.turns_dropped}")
    print(f"compaction complete (all load-bearing facts preserved): {state.complete}")
    print("--- compacted summary ---")
    print(state.summary)

    print("\n=== Live tool-result pruning (real model) ===")
    billing_record = "\n".join(
        [
            "plan: business",
            "renewal date: 2027-03-01",
            "seats: 42",
            "past invoices: " + "INV-1001 $480, " * 40,
            "usage notes: " + "daily sync at 02:00, " * 40,
        ]
    )
    pruned = prune_tool_result_with_llm(client, billing_record, strategy="summarise")
    print(f"summarise: {pruned.tokens_before} -> {pruned.tokens_after} tokens "
          f"(saved {pruned.tokens_saved})")
    print(f"summary: {pruned.text}")

    print(f"\nModel usage for this run: {client.total_prompt_tokens} prompt + "
          f"{client.total_completion_tokens} completion tokens, "
          f"cost ${client.total_cost_usd:.6f}")


if __name__ == "__main__":
    if "--live" in sys.argv:
        demo_live()
    else:
        demo()
