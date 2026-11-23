"""The loop watchdog: an observer that watches the loop, not the output.

The critic in ``critic_agent.py`` judges whether an output is *good*.
The healing loop in ``self_healing_loop.py`` stops when a failure
signature repeats or a budget runs out. This module sits one level above
both of them and asks a different question: is the loop itself *making
progress*? A loop can pass every hard cap in the book and still be
spinning — alternating between two errors, rewording the same wrong
answer, or handing a task back and forth between two agents forever —
and the only thing that catches that is an observer whose job is the
trajectory, not the latest draft.

The watchdog is deliberately small and deterministic, so it runs with no
model and no API key. It observes one attempt at a time — the attempt's
output text, an evaluator score if one exists, a failure signature if
one exists, and which agent produced the attempt — and after each
observation it returns a verdict: ``continue``, ``warn`` or ``stop``,
with the reasons recorded so a human can see exactly what fired.

Two honest labels, stated up front:

* The semantic-repetition check is a *proxy* for embedding similarity,
  not a real embedding. It combines token overlap (Jaccard similarity
  over word sets) with a normalised edit-distance measure
  (``difflib.SequenceMatcher``). In production, replace
  ``_semantic_similarity`` with cosine similarity over real embeddings;
  the thresholds and the verdict logic do not change, only the
  measurement gets better. The ``TODO`` marks the swap point.
* The evaluator score is whatever your verifier already produces — a
  test pass rate, a retrieval grade, a rubric score. The watchdog does
  not invent a score; if you pass ``score=None`` it simply skips the
  progress checks that need one, and says so in its reasons. Demo (d)
  below wires this up for real: it runs the critic loop from
  ``critic_agent.py`` against a real model and feeds each round's test
  pass rate into the watchdog as the score.

TODO: plug in a real model in one place only — replace
``_semantic_similarity`` with embedding cosine similarity (any embedding
model; the function signature stays ``(a, b) -> float`` in ``[0, 1]``).
Nothing else in this module changes. The similarity proxy is a labelled
stand-in, not a hidden one: the docstring on ``_semantic_similarity``
states exactly what it can and cannot tell apart, and the threshold is
marked for re-tuning when real embeddings replace it.

Run the module directly to see three loops through the watchdog's eyes:
a converging loop it lets finish, an oscillating loop it stops, and a
ping-pong delegation it stops — plus a fourth demo, live, in which the
watchdog observes a real critic loop repairing a median function:

    python loop_watchdog.py
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Verdicts and configuration
# ---------------------------------------------------------------------------


class WatchdogVerdictKind(Enum):
    """What the watchdog thinks the loop should do next."""

    CONTINUE = "continue"  # No sign of trouble; let the loop run.
    WARN = "warn"          # Something looks off; keep going, but log it.
    STOP = "stop"          # The loop is not making progress; end it now.


@dataclass
class WatchdogVerdict:
    """One verdict, with every reason that produced it recorded."""

    kind: WatchdogVerdictKind
    reasons: list[str] = field(default_factory=list)
    # A snapshot of the measurements behind the verdict, so a human (or a
    # later review, or Chapter 10's trajectory grading) can check the call.
    metrics: dict[str, float | int | str] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - formatting helper
        lines = [f"Watchdog: {self.kind.value.upper()}"]
        lines.extend(f"  - {reason}" for reason in self.reasons)
        return "\n".join(lines)


@dataclass
class WatchdogConfig:
    """Thresholds for the watchdog. All are worked-example values: tune
    them against your own loop's logs before trusting them in production,
    and record the tuned values next to the loop they guard.

    The defaults are chosen so the demo loops land on opposite sides of
    each line: the converging demo clears them, the oscillating and
    ping-pong demos trip them.
    """

    # A state hash seen this many times means the loop is reproducing an
    # exact earlier state, not exploring.
    max_repeated_state: int = 2
    # A failure signature seen this many times means no new information.
    max_repeated_signature: int = 2
    # Two outputs at or above this similarity are "the same answer in
    # different words". (Proxy threshold; re-tune when you swap in real
    # embeddings, which typically run higher than token overlap.)
    similarity_threshold: float = 0.85
    # How many consecutive attempts with no meaningful score gain count as
    # stagnation. A gain counts as meaningful only if it beats this floor.
    stagnation_window: int = 3
    min_score_gain: float = 0.02
    # How many full A-B-A-B delegation cycles before ping-pong is declared.
    max_ping_pong_cycles: int = 2
    # A relevance score (0-1, supplied by the caller) falling this far
    # below the first attempt's relevance signals goal drift.
    drift_drop: float = 0.30


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalise(text: str) -> str:
    """Lower-case and collapse whitespace, so trivial rewording and
    re-formatting do not hide a repeated state."""
    return " ".join(text.lower().split())


def state_key(text: str) -> str:
    """A stable hash of an attempt's output, used to spot exact repeats.

    Normalisation runs first, so two outputs that differ only in
    whitespace or capitalisation hash identically — that is the point:
    the loop has not moved. Hashing (rather than storing the full text)
    keeps the watchdog's memory small on long loops.
    """
    return hashlib.sha256(_normalise(text).encode("utf-8")).hexdigest()[:16]


def _semantic_similarity(a: str, b: str) -> float:
    """Cheap proxy for embedding similarity between two outputs.

    Returns a number in [0, 1]: the average of (1) Jaccard similarity
    over the two outputs' word sets and (2) ``difflib``'s ratio, a
    normalised edit-distance measure. It catches rewording — the same
    claim with synonyms swapped, or the same code with variables
    renamed — but it is *not* an embedding: it cannot tell that "the
    refund is denied" and "we cannot return your money" mean the same
    thing when they share almost no words, and it over-scores outputs
    that share boilerplate while differing on the one load-bearing
    sentence. Treat a high proxy score as a reason to look, not as
    proof of sameness.

    TODO: replace with cosine similarity over real embeddings, e.g.
    ``1 - cosine(embed(a), embed(b))`` mapped into [0, 1]. Keep the
    signature and the threshold semantics; re-tune the threshold.
    """
    words_a = set(_WORD_RE.findall(a.lower()))
    words_b = set(_WORD_RE.findall(b.lower()))
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    jaccard = len(words_a & words_b) / len(words_a | words_b)
    edit = difflib.SequenceMatcher(None, _normalise(a), _normalise(b)).ratio()
    return (jaccard + edit) / 2.0


# ---------------------------------------------------------------------------
# The watchdog
# ---------------------------------------------------------------------------


@dataclass
class AttemptObservation:
    """One attempt, as the watchdog sees it."""

    attempt: int
    output: str
    # Evaluator score in [0, 1] if the loop has one (test pass rate,
    # retrieval grade, rubric score); None if it does not.
    score: float | None = None
    # Failure signature from traceback_parser, e.g. ("KeyError", "app.py", 6).
    signature: tuple[str, str, int | None] | None = None
    # Which agent produced this attempt ("planner", "researcher", ...).
    # None for a single-agent loop, where ping-pong cannot occur.
    actor: str | None = None
    # Relevance of this attempt to the original task, in [0, 1], if the
    # caller can supply it (a small model judgement, or a keyword /
    # retrieval-overlap check). None means the drift check is skipped.
    relevance: float | None = None


class LoopWatchdog:
    """A separate observer for one self-correction loop.

    Feed it attempts in order with :meth:`observe`; after each one it
    returns a verdict. The watchdog never rewrites output and never
    judges quality — that is the critic's job. It only judges the
    *trajectory*: is this loop going somewhere, or is it travelling?
    """

    def __init__(self, config: WatchdogConfig | None = None):
        self.config = config or WatchdogConfig()
        self._state_counts: dict[str, int] = {}
        self._signature_counts: dict[tuple[str, str, int | None], int] = {}
        self._outputs: list[str] = []
        self._scores: list[float] = []
        self._actors: list[str] = []
        self._relevances: list[float] = []
        self._best_score: float | None = None
        self._attempts_since_gain: int = 0
        self.verdicts: list[WatchdogVerdict] = []

    # -- public API ----------------------------------------------------

    def observe(self, observation: AttemptObservation) -> WatchdogVerdict:
        """Record one attempt and return the watchdog's verdict on it."""
        reasons: list[str] = []
        stop = False
        warn = False
        metrics: dict[str, float | int | str] = {
            "attempt": observation.attempt,
        }

        # 1. Exact-state repetition, via the state hash.
        key = state_key(observation.output)
        self._state_counts[key] = self._state_counts.get(key, 0) + 1
        metrics["state_repeats"] = self._state_counts[key]
        if self._state_counts[key] >= self.config.max_repeated_state:
            stop = True
            reasons.append(
                f"Same output state seen {self._state_counts[key]} times "
                f"(state {key}): the loop is reproducing an earlier "
                "attempt, not exploring."
            )

        # 2. Repeated failure signature — the same rule the healing loop
        # already enforces, repeated here so the watchdog's report is
        # complete even when it guards a loop that lacks the rule.
        if observation.signature is not None:
            count = self._signature_counts.get(observation.signature, 0) + 1
            self._signature_counts[observation.signature] = count
            metrics["signature_repeats"] = count
            if count >= self.config.max_repeated_signature:
                stop = True
                reasons.append(
                    f"Failure signature {observation.signature} seen "
                    f"{count} times: no new information is being produced."
                )

        # 3. Semantic repetition: near-identical to an earlier output
        # even though the text is not byte-identical.
        for earlier_index, earlier in enumerate(self._outputs, start=1):
            similarity = _semantic_similarity(observation.output, earlier)
            if similarity >= self.config.similarity_threshold:
                metrics["max_similarity_to_earlier"] = round(similarity, 3)
                warn = True
                reasons.append(
                    f"Output is {similarity:.2f} similar (proxy) to "
                    f"attempt {earlier_index}: same answer in different "
                    "words, not a new attempt."
                )
                break
        self._outputs.append(observation.output)

        # 4. Oscillation: the last three states read A-B-A (or the last
        # four read A-B-A-B), so the loop is travelling in a circle.
        # State keys are used here, so oscillation is caught even when
        # the two alternating outputs are textually identical each time
        # they recur.
        state_history = [
            state_key(output) for output in self._outputs
        ]
        if len(state_history) >= 3 and (
            state_history[-1] == state_history[-3]
            and state_history[-1] != state_history[-2]
        ):
            stop = True
            reasons.append(
                "Oscillation detected: the loop returned to the state "
                "from two attempts ago (A-B-A). Fixing one error "
                "reintroduced the other."
            )
            metrics["oscillation"] = "A-B-A"

        # 5. Progress: is the evaluator score actually improving?
        # Convergence is a rising best score with shrinking changes;
        # stagnation is a flat best score over a full window. A score
        # that merely wobbles — up a little, down a little — is
        # stagnation wearing a disguise, and the window is what exposes
        # it: wobble never sets a new best by more than the gain floor.
        if observation.score is not None:
            score = observation.score
            self._scores.append(score)
            if self._best_score is None or score > self._best_score + self.config.min_score_gain:
                self._best_score = score
                self._attempts_since_gain = 0
            else:
                self._attempts_since_gain += 1
            metrics["best_score"] = round(self._best_score, 3)
            metrics["attempts_since_gain"] = self._attempts_since_gain
            if self._attempts_since_gain >= self.config.stagnation_window:
                stop = True
                reasons.append(
                    f"No meaningful score gain for "
                    f"{self._attempts_since_gain} attempts "
                    f"(best {self._best_score:.2f}): the loop is "
                    "stagnating, not converging."
                )
        else:
            metrics["best_score"] = "no evaluator score supplied"

        # 6. Ping-pong delegation: the work bounces between two agents
        # with neither one finishing it.
        if observation.actor is not None:
            self._actors.append(observation.actor)
            cycles = self._ping_pong_cycles()
            metrics["ping_pong_cycles"] = cycles
            if cycles >= self.config.max_ping_pong_cycles:
                stop = True
                reasons.append(
                    f"Ping-pong delegation: the task has bounced between "
                    f"agents {cycles} full cycles "
                    f"({ ' -> '.join(self._actors[-4:])}) with no agent "
                    "finishing it."
                )

        # 7. Goal drift: relevance to the original task is falling.
        if observation.relevance is not None:
            self._relevances.append(observation.relevance)
            first = self._relevances[0]
            metrics["relevance"] = round(observation.relevance, 3)
            if first - observation.relevance >= self.config.drift_drop:
                warn = True
                reasons.append(
                    f"Goal drift: relevance to the original task fell "
                    f"from {first:.2f} to {observation.relevance:.2f}."
                )

        # 8. Amplification (context growth): each attempt's output is
        # longer than the last by a growing margin *while the score is
        # flat* (or unscored), so the loop is accumulating text — usually
        # its own history — faster than it is accumulating answers.
        # Length alone does not prove the loop is lost: a converging
        # answer legitimately grows as it gets more complete, so this
        # check stays silent while the score is still climbing. This is
        # a warn, not a stop: a legitimately thorough answer is also long.
        if len(self._outputs) >= 3:
            lengths = [len(output) for output in self._outputs[-3:]]
            score_flat = (
                not self._scores or self._attempts_since_gain >= 2
            )
            if (lengths[0] < lengths[1] < lengths[2]
                    and lengths[2] > 2 * lengths[0] and score_flat):
                warn = True
                reasons.append(
                    f"Amplification: output length grew "
                    f"{lengths[0]} -> {lengths[1]} -> {lengths[2]} "
                    "characters over three attempts; the loop is "
                    "accumulating text faster than answers."
                )

        kind = (
            WatchdogVerdictKind.STOP if stop
            else WatchdogVerdictKind.WARN if warn
            else WatchdogVerdictKind.CONTINUE
        )
        if not reasons:
            reasons.append("No runaway signal: state is new, score is "
                           "moving or unscored, and no delegation cycle.")
        verdict = WatchdogVerdict(kind=kind, reasons=reasons, metrics=metrics)
        self.verdicts.append(verdict)
        return verdict

    def summary(self) -> str:
        """A one-paragraph report on the whole observed trajectory."""
        kinds = [verdict.kind.value for verdict in self.verdicts]
        lines = [
            f"Watchdog observed {len(self.verdicts)} attempt(s): "
            f"{kinds.count('continue')} continue, "
            f"{kinds.count('warn')} warn, {kinds.count('stop')} stop.",
        ]
        if self._best_score is not None:
            lines.append(f"Best evaluator score: {self._best_score:.2f}.")
        stops = [v for v in self.verdicts if v.kind is WatchdogVerdictKind.STOP]
        if stops:
            lines.append("Stop reasons:")
            lines.extend(f"  - {reason}" for reason in stops[0].reasons)
        return "\n".join(lines)

    # -- internals -----------------------------------------------------

    def _ping_pong_cycles(self) -> int:
        """Count full A-B-A-B cycles in the actor history.

        A cycle is four consecutive attempts alternating between exactly
        two actors (A, B, A, B). Overlapping windows each count, so a
        five-long A-B-A-B-A history holds one full cycle plus the start
        of the next — the count only grows as the bouncing continues.
        """
        actors = self._actors
        cycles = 0
        for i in range(len(actors) - 3):
            window = actors[i:i + 4]
            if (
                len(set(window)) == 2
                and window[0] == window[2]
                and window[1] == window[3]
                and window[0] != window[1]
            ):
                cycles += 1
        return cycles


# ---------------------------------------------------------------------------
# Demos — fixed example trajectories, no model, no API key
# ---------------------------------------------------------------------------
# Demos (a)–(c) feed the watchdog observations directly. That is not a
# stand-in for a model: observations are the watchdog's input, and the
# trajectories are the worked examples the chapter text reasons about —
# a converging loop, an oscillating loop, a ping-pong delegation. Demo
# (d) below is the live counterpart: the same watchdog observing a real
# critic loop, with the test pass rate as the evaluator score.

# (a) A converging loop: each draft is genuinely different, the score
# climbs 0.40 -> 0.65 -> 0.85 -> 0.95, and the watchdog lets it finish.
CONVERGING = [
    ("Sort the list, then take the middle element.", 0.40),
    ("Sort the list; for even lengths average the two middle values.", 0.65),
    ("Sort a copy so the input is untouched; average the two middle "
     "values for even lengths, take the middle for odd.", 0.85),
    ("Sort a copy; raise ValueError on an empty list; average the two "
     "middle values for even lengths, take the middle for odd.", 0.95),
]

# (b) An oscillating loop: the maker fixes error A, introduces error B,
# fixes B, reintroduces A — two drafts, alternating forever.
OSCILLATING_A = "Handle empty lists by returning 0 for median([])."
OSCILLATING_B = "Handle empty lists by raising ValueError, but sort the list in place."

# (c) A ping-pong delegation: planner and researcher hand the same
# question back and forth, each claiming the other should answer it.
# The wording varies slightly each round — so no state repeats and no
# score is supplied — and the *actor sequence* is what gives it away.
PING_PONG = [
    ("planner", "Researcher: find the refund policy for this order, please."),
    ("researcher", "Planner: I need the order's region before I can search."),
    ("planner", "Researcher: please look up the refund policy for this order."),
    ("researcher", "Planner: I still need the order's region to search."),
    ("planner", "Researcher: could you find the refund policy for this order?"),
]


def _run_demo(title: str, observations: list[AttemptObservation]) -> None:
    print(f"=== {title} ===")
    watchdog = LoopWatchdog()
    for observation in observations:
        verdict = watchdog.observe(observation)
        print(f"Attempt {observation.attempt}: {verdict.kind.value.upper()}")
        for reason in verdict.reasons:
            print(f"  - {reason}")
        if verdict.kind is WatchdogVerdictKind.STOP:
            print("  -> loop stopped by the watchdog.")
            break
    print(watchdog.summary())
    print()


def _run_demo_d_live() -> None:
    """Demo (d): the watchdog observes a real critic loop, live.

    The critic loop from ``critic_agent.py`` runs against a real model,
    repairing the buggy median draft; after each round the watchdog
    observes the draft with the round's test pass rate as the evaluator
    score. A converging repair should earn continue verdicts all the
    way through. Needs a configured model backend; skipped otherwise,
    so the deterministic demos above still run with no model and no key.
    """
    print("=== Demo (d): the watchdog watches a real critic loop ===")
    from llm_client import LLMClient  # noqa: PLC0415 - live demo only

    if not LLMClient.has_backend():
        print("Skipped — needs a configured model backend (LLM_API_KEY, or "
              "LLM_BACKEND=opencheap-connector).")
        print()
        return
    from critic_agent import (  # noqa: PLC0415 - live demo only
        DRAFT_BUGGY,
        MEDIAN_SPEC,
        MEDIAN_TESTS,
        Critic,
        LLMMaker,
        run_maker_checker,
    )

    client = LLMClient()
    watchdog = LoopWatchdog()

    def observe(round_no: int, draft: str, critique) -> None:
        score = (
            critique.tests_passed / critique.tests_run
            if critique.tests_run else None
        )
        verdict = watchdog.observe(AttemptObservation(
            attempt=round_no, output=draft, score=score))
        print(f"  Round {round_no}: test pass rate {score:.2f} — "
              f"watchdog says {verdict.kind.value.upper()}")
        for reason in verdict.reasons:
            print(f"    - {reason}")

    report = run_maker_checker(
        MEDIAN_SPEC, LLMMaker(client), Critic(MEDIAN_SPEC, MEDIAN_TESTS),
        max_rounds=3, first_draft=DRAFT_BUGGY, on_round=observe,
    )
    print(f"  Critic loop outcome: "
          f"{'PASS' if report.succeeded else 'FAILED CLOSED'} "
          f"in {report.rounds_used} round(s).")
    print(watchdog.summary())
    print(
        f"  Model usage for this demo: {client.total_prompt_tokens} prompt "
        f"tokens, {client.total_completion_tokens} completion tokens"
        + (f", ${client.total_cost_usd:.4f}" if client.total_cost_usd else "")
    )
    print()


if __name__ == "__main__":
    _run_demo(
        "Demo (a): a converging loop — the watchdog lets it finish",
        [
            AttemptObservation(attempt=i + 1, output=text, score=score)
            for i, (text, score) in enumerate(CONVERGING)
        ],
    )
    _run_demo(
        "Demo (b): an oscillating loop — the watchdog stops it",
        [
            AttemptObservation(
                attempt=i + 1,
                output=OSCILLATING_A if i % 2 == 0 else OSCILLATING_B,
                score=0.50 if i % 2 == 0 else 0.60,
            )
            for i in range(5)
        ],
    )
    _run_demo(
        "Demo (c): ping-pong delegation — the watchdog stops it",
        [
            AttemptObservation(attempt=i + 1, output=text, actor=actor)
            for i, (actor, text) in enumerate(PING_PONG)
        ],
    )
    _run_demo_d_live()
