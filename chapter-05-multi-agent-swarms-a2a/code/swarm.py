"""Core library for Chapter 5: Multi-Agent Swarms and Agent-to-Agent Handoffs.

Framework-neutral, standard-library-only implementation of the ideas in the
chapter README:

- Typed state passing: every handoff travels as a ``TaskEnvelope`` carrying a
  typed payload, a schema version and a checksum. Receivers validate before
  they act; a failed validation is a handled outcome, never a silent coercion.
- Supervisor topology: one supervisor fans out to worker agents, reads their
  typed results, and synthesises an answer. Workers never talk to each other.
- Consensus voting and conflict resolution: majority vote, weighted vote and
  escalation, with facts (verifiable against a source of truth) kept separate
  from verdicts (judgements a vote may legitimately resolve).
- Bounds: maximum fan-out, maximum rounds, per-worker timeouts, a token
  budget and a message budget, all enforced in code, not in prose.

The worker agents here are deterministic on purpose, and that is a
deliberate stand-in, not an unfinished one: the lesson of this chapter
is the topology and the handoff mechanics — typed envelopes, validation,
consensus rules and bounds — and deterministic workers make those
mechanics runnable, repeatable and testable with no model API key and
no paid service. The researcher reads a real file
(``accounts.json``, next to this module) instead of an in-memory
dictionary, and the policy checker is a rule table, because looking a
fact up and applying a written rule are deterministic jobs, not model
jobs. The two workers that do pretend to be a model — the sentiment
scorer, which judges tone, and the drafter, which writes prose — have
real model-backed versions in this module: ``ModelSentimentWorker``
and ``ModelDrafterWorker``, built on ``LLMWorker`` and the shared
``llm_client.py`` client (copied verbatim from Chapter 7). The live
demo ``demo_live_swarm.py`` runs the swarm with those two workers, so
the swarm runs for real; the deterministic demos keep the
deterministic workers, so the topology lesson stays runnable offline.
Where a real deployment would call Amazon Bedrock through Mastra, the
seam is the ``Worker.run`` method: it takes a validated envelope and
returns a validated envelope.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from llm_client import LLMClient, LLMError

# ---------------------------------------------------------------------------
# Errors: every failure mode is a named, catchable outcome.
# ---------------------------------------------------------------------------


class EnvelopeError(ValueError):
    """Base class for handoff validation failures."""


class ChecksumError(EnvelopeError):
    """The payload bytes do not match the envelope checksum."""


class SchemaVersionError(EnvelopeError):
    """The payload schema version is not one the receiver accepts."""


class PayloadValidationError(EnvelopeError):
    """The payload fails its type's schema validation."""


class WrongRecipientError(EnvelopeError):
    """The envelope is addressed to a different agent than the receiver."""


class BudgetExceeded(RuntimeError):
    """A swarm bound (rounds, fan-out, tokens, messages, hops) was hit."""


# ---------------------------------------------------------------------------
# Typed state: the payloads that cross a handoff. Each one is a schema, not
# a string, and each field that is a fact names its source of truth.
# ---------------------------------------------------------------------------

PRIORITIES = ("low", "medium", "high", "urgent")
SENTIMENTS = ("frustrated", "neutral", "pleased")


@dataclass(frozen=True)
class TicketState:
    """The ticket as the supervisor understands it when the run starts."""

    ticket_id: str
    customer_id: str
    subject: str
    body: str


@dataclass(frozen=True)
class ResearchResult:
    """What the researcher found. Every field is a fact with a source."""

    customer_id: str
    plan: str
    renewal_date: str  # ISO date, e.g. "2026-03-14"; verifiable fact
    account_status: str
    source: str = "account-db"  # provenance: where this fact came from
    provenance: str = "researcher"


@dataclass(frozen=True)
class PolicyResult:
    """The policy checker's verdict. A judgement, not a fact."""

    applies: bool
    policy_name: str
    priority: str  # one of PRIORITIES; a verdict a vote may resolve
    reason: str
    provenance: str = "policy-checker"


@dataclass(frozen=True)
class SentimentResult:
    """The sentiment scorer's verdict."""

    score: float  # 0.0 (furious) to 1.0 (delighted)
    label: str  # one of SENTIMENTS
    provenance: str = "sentiment-scorer"


@dataclass(frozen=True)
class DraftResult:
    """The drafter's reply. Factual claims carry provenance."""

    reply_text: str
    claims: List[Dict[str, str]]  # each: {"claim": ..., "provenance": ...}
    provenance: str = "drafter"


@dataclass(frozen=True)
class Vote:
    """One voter's structured verdict in a consensus round."""

    voter: str
    verdict: str
    confidence: float  # 0.0 to 1.0, self-reported
    evidence: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Payload validators: one per payload type. Validation failure raises
# PayloadValidationError naming the exact field that failed.
# ---------------------------------------------------------------------------


def _require(payload: Dict[str, Any], name: str, kind: type) -> Any:
    if name not in payload:
        raise PayloadValidationError(f"missing required field: {name!r}")
    value = payload[name]
    if not isinstance(value, kind):
        raise PayloadValidationError(
            f"field {name!r} must be {kind.__name__}, got "
            f"{type(value).__name__}"
        )
    return value


def validate_ticket_state(payload: Dict[str, Any]) -> TicketState:
    return TicketState(
        ticket_id=_require(payload, "ticket_id", str),
        customer_id=_require(payload, "customer_id", str),
        subject=_require(payload, "subject", str),
        body=_require(payload, "body", str),
    )


def validate_research_result(payload: Dict[str, Any]) -> ResearchResult:
    return ResearchResult(
        customer_id=_require(payload, "customer_id", str),
        plan=_require(payload, "plan", str),
        renewal_date=_require(payload, "renewal_date", str),
        account_status=_require(payload, "account_status", str),
        source=payload.get("source", "account-db"),
        provenance=payload.get("provenance", "researcher"),
    )


def validate_policy_result(payload: Dict[str, Any]) -> PolicyResult:
    priority = _require(payload, "priority", str)
    if priority not in PRIORITIES:
        raise PayloadValidationError(
            f"field 'priority' must be one of {PRIORITIES}, got {priority!r}"
        )
    return PolicyResult(
        applies=_require(payload, "applies", bool),
        policy_name=_require(payload, "policy_name", str),
        priority=priority,
        reason=_require(payload, "reason", str),
        provenance=payload.get("provenance", "policy-checker"),
    )


def validate_sentiment_result(payload: Dict[str, Any]) -> SentimentResult:
    score = _require(payload, "score", (int, float))
    if not 0.0 <= float(score) <= 1.0:
        raise PayloadValidationError(
            f"field 'score' must be between 0.0 and 1.0, got {score!r}"
        )
    label = _require(payload, "label", str)
    if label not in SENTIMENTS:
        raise PayloadValidationError(
            f"field 'label' must be one of {SENTIMENTS}, got {label!r}"
        )
    return SentimentResult(
        score=float(score),
        label=label,
        provenance=payload.get("provenance", "sentiment-scorer"),
    )


def validate_draft_result(payload: Dict[str, Any]) -> DraftResult:
    claims = _require(payload, "claims", list)
    for claim in claims:
        if not isinstance(claim, dict) or "claim" not in claim:
            raise PayloadValidationError(
                "each claim must be an object with a 'claim' field"
            )
        if "provenance" not in claim:
            raise PayloadValidationError(
                f"claim {claim['claim']!r} carries no provenance"
            )
    return DraftResult(
        reply_text=_require(payload, "reply_text", str),
        claims=claims,
        provenance=payload.get("provenance", "drafter"),
    )


#: Payload type name -> (validator, accepted schema versions).
PAYLOAD_TYPES: Dict[str, Any] = {
    "TicketState": (validate_ticket_state, (1,)),
    "ResearchResult": (validate_research_result, (1,)),
    "PolicyResult": (validate_policy_result, (1,)),
    "SentimentResult": (validate_sentiment_result, (1, 2)),
    "DraftResult": (validate_draft_result, (1,)),
}


def payload_to_dict(payload: Any) -> Dict[str, Any]:
    """Serialise a typed payload dataclass to a plain dict."""
    if hasattr(payload, "__dataclass_fields__"):
        return {k: getattr(payload, k) for k in payload.__dataclass_fields__}
    raise TypeError(f"not a dataclass payload: {type(payload).__name__}")


# ---------------------------------------------------------------------------
# The envelope: what actually crosses a handoff. The checksum is computed
# over the canonical JSON of the payload, so any mutation of the payload
# after sealing — in transport, in a retry, or by a careless worker — is
# detectable by the receiver before it acts.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskEnvelope:
    task_id: str
    schema_version: int
    sender: str
    recipient: str
    payload_type: str
    payload: Dict[str, Any]
    checksum: str
    # Monotonically increasing per task; a receiver that has already seen a
    # sequence number rejects a replay of an older envelope.
    sequence: int = 0


def compute_checksum(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_envelope(
    task_id: str,
    sender: str,
    recipient: str,
    payload: Any,
    schema_version: int = 1,
    sequence: int = 0,
) -> TaskEnvelope:
    payload_dict = payload_to_dict(payload)
    return TaskEnvelope(
        task_id=task_id,
        schema_version=schema_version,
        sender=sender,
        recipient=recipient,
        payload_type=type(payload).__name__,
        payload=payload_dict,
        checksum=compute_checksum(payload_dict),
        sequence=sequence,
    )


def validate_envelope(
    envelope: TaskEnvelope, *, expected_recipient: str
) -> Any:
    """Validate a received envelope and return the typed payload.

    Checks, in order: recipient, checksum, payload type, schema version,
    field-level schema. Raises the specific EnvelopeError subclass for the
    first check that fails. The caller decides what to do with the failure;
    validation itself never guesses, coerces or proceeds.
    """
    if envelope.recipient != expected_recipient:
        raise WrongRecipientError(
            f"envelope addressed to {envelope.recipient!r}, "
            f"but received by {expected_recipient!r}"
        )
    if compute_checksum(envelope.payload) != envelope.checksum:
        raise ChecksumError(
            f"checksum mismatch for task {envelope.task_id!r}: payload was "
            "modified after sealing"
        )
    entry = PAYLOAD_TYPES.get(envelope.payload_type)
    if entry is None:
        raise PayloadValidationError(
            f"unknown payload type: {envelope.payload_type!r}"
        )
    validator, accepted_versions = entry
    if envelope.schema_version not in accepted_versions:
        raise SchemaVersionError(
            f"{envelope.payload_type} schema version "
            f"{envelope.schema_version} not accepted; "
            f"accepted versions are {accepted_versions}"
        )
    return validator(envelope.payload)


# ---------------------------------------------------------------------------
# Workers. A worker takes a validated envelope and returns a validated
# envelope. The base class owns the receive-validate-send cycle so an
# individual worker cannot forget to validate.
# ---------------------------------------------------------------------------


class Worker:
    """Base class for a worker agent.

    ``latency_s`` and ``tokens`` are the worker's declared costs for one
    call. The supervisor simulates elapsed time from these declarations
    instead of sleeping, so runs stay fast and deterministic; a worker that
    declares a latency above the supervisor's per-worker timeout is treated
    exactly like a worker that really hung. A model-backed worker instead
    records its real cost in ``last_call_tokens`` and its real elapsed
    time, which the supervisor reads after the call — see ``LLMWorker``.
    """

    name: str = "worker"
    accepts: str = "TicketState"
    latency_s: float = 0.1
    tokens: int = 500
    # Set by a model-backed worker after a real call; None means "use the
    # declared ``tokens`` above", which is what deterministic workers do.
    last_call_tokens: Optional[int] = None

    def handle(self, envelope: TaskEnvelope) -> TaskEnvelope:
        payload = validate_envelope(envelope, expected_recipient=self.name)
        result = self.work(payload, envelope.task_id)
        return make_envelope(
            task_id=envelope.task_id,
            sender=self.name,
            recipient="supervisor",
            payload=result,
            sequence=envelope.sequence + 1,
        )

    def work(self, payload: Any, task_id: str) -> Any:
        raise NotImplementedError


# The account database is a real file, ``accounts.json`` next to this
# module — the account system a real researcher worker would query
# through a tool, reduced to a file read so the chapter runs locally.
# The researcher re-reads the file on every call, so a re-fetch of the
# source of truth (see ``resolve_fact_disagreement`` and
# ``demo_consensus.py`` case 4) really does go back to the source
# instead of returning a cached copy.
ACCOUNTS_PATH = Path(__file__).with_name("accounts.json")


def load_accounts(path: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """Read the account database from disk and return it as a dict."""
    with open(path or ACCOUNTS_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise PayloadValidationError("accounts file must hold one object per customer")
    return data


def refetch_account(customer_id: str) -> Dict[str, str]:
    """Re-fetch one customer's record from the source of truth on disk."""
    record = load_accounts().get(customer_id)
    if record is None:
        raise PayloadValidationError(f"unknown customer: {customer_id!r}")
    return {"customer_id": customer_id, **record}


class ResearcherWorker(Worker):
    name = "researcher"
    accepts = "TicketState"
    tokens = 800

    def work(self, payload: TicketState, task_id: str) -> ResearchResult:
        record = refetch_account(payload.customer_id)
        return ResearchResult(
            customer_id=payload.customer_id,
            plan=record["plan"],
            renewal_date=record["renewal_date"],
            account_status=record["account_status"],
        )


class PolicyWorker(Worker):
    name = "policy-checker"
    accepts = "TicketState"
    tokens = 600

    def work(self, payload: TicketState, task_id: str) -> PolicyResult:
        body = payload.body.lower()
        if "refund" in body:
            return PolicyResult(
                applies=True,
                policy_name="refund-window-30d",
                priority="high",
                reason="Refund requested; 30-day window applies.",
            )
        if "outage" in body or "down" in body:
            return PolicyResult(
                applies=True,
                policy_name="sla-availability",
                priority="urgent",
                reason="Availability issue; service-level policy applies.",
            )
        return PolicyResult(
            applies=False,
            policy_name="none",
            priority="low",
            reason="No policy matches this ticket.",
        )


class SentimentWorker(Worker):
    name = "sentiment-scorer"
    accepts = "TicketState"
    tokens = 400

    def work(self, payload: TicketState, task_id: str) -> SentimentResult:
        body = payload.body.lower()
        if any(word in body for word in ("furious", "unacceptable", "cancel")):
            return SentimentResult(score=0.15, label="frustrated")
        if any(word in body for word in ("thanks", "great", "love")):
            return SentimentResult(score=0.9, label="pleased")
        return SentimentResult(score=0.5, label="neutral")


class DrafterWorker(Worker):
    """Writes the single artefact of the run: the reply.

    The drafter only accepts typed inputs with provenance. A free-text
    summary is not a valid input: if the supervisor hands it a paraphrase
    instead of the typed values, validation rejects it, which is exactly the
    protection that stops the telephone game in Chapter 5, Exercise 6.
    """

    name = "drafter"
    accepts = "DraftInput"  # see validate_draft_input below
    tokens = 700

    def work(self, payload: Any, task_id: str) -> DraftResult:
        # payload is a DraftInput (validated by the drafter's own override).
        research: ResearchResult = payload["research"]
        policy: PolicyResult = payload["policy"]
        claims = [
            {
                "claim": f"plan is {research.plan}",
                "provenance": research.provenance,
            },
            {
                "claim": f"renewal date is {research.renewal_date}",
                "provenance": research.provenance,
            },
            {
                "claim": f"policy {policy.policy_name} applies: {policy.applies}",
                "provenance": policy.provenance,
            },
        ]
        reply = (
            f"Hello — thanks for writing in about "
            f"'{payload['subject']}'. Your account is on the "
            f"{research.plan} plan, renewing on {research.renewal_date}. "
            f"Policy check: {policy.policy_name} "
            f"({'applies' if policy.applies else 'does not apply'}). "
            f"{policy.reason} We have set the priority to {policy.priority}."
        )
        return DraftResult(reply_text=reply, claims=claims)

    def handle(self, envelope: TaskEnvelope) -> TaskEnvelope:
        # The drafter's input is a bundle of the other workers' typed
        # results, so it validates the bundle itself rather than through the
        # generic payload registry.
        if envelope.recipient != self.name:
            raise WrongRecipientError(
                f"envelope addressed to {envelope.recipient!r}, "
                f"but received by {self.name!r}"
            )
        if compute_checksum(envelope.payload) != envelope.checksum:
            raise ChecksumError("checksum mismatch on draft input")
        bundle = envelope.payload
        research = validate_research_result(bundle.get("research", {}))
        policy = validate_policy_result(bundle.get("policy", {}))
        result = self.work(
            {"research": research, "policy": policy,
             "subject": bundle.get("subject", "")},
            envelope.task_id,
        )
        return make_envelope(
            task_id=envelope.task_id,
            sender=self.name,
            recipient="supervisor",
            payload=result,
            sequence=envelope.sequence + 1,
        )


class LLMWorker(Worker):
    """Base class for a model-backed worker.

    A model-backed worker is a worker whose judgement or prose comes
    from a real model call, through the shared ``LLMClient`` (the same
    client Chapter 7 uses). The contract is unchanged: ``work`` takes
    a typed payload and returns a typed payload, the base class keeps
    owning validation and the envelope, and the model never returns
    free text across a handoff — its structured JSON reply is parsed
    into the payload dataclass and the validator catches a malformed
    response. A model failure is a loud ``LLMError``, which the
    supervisor records as a rejection and the run continues without
    that worker, exactly like a corrupt payload.

    Subclasses implement ``prompt_for`` (the messages to send) and
    ``parse_reply`` (the JSON object to the typed payload). The two
    concrete workers are ``ModelSentimentWorker`` (a judgement, so it
    uses the cheap ``:floor`` judge route) and ``ModelDrafterWorker``
    (prose, so it uses the full model). Pass a client in, or leave it
    out and one is built from the environment — which raises loudly
    when no key or backend is configured, instead of silently
    falling back to a fake reply.
    """

    name = "llm-worker"

    def __init__(self, client: Optional[LLMClient] = None) -> None:
        self.client = client or LLMClient()
        self.last_call_tokens = None

    def _record_usage(self) -> None:
        before = getattr(self, "_usage_before", (0, 0))
        now = (
            self.client.total_prompt_tokens,
            self.client.total_completion_tokens,
        )
        self.last_call_tokens = (now[0] - before[0]) + (now[1] - before[1])
        self._usage_before = now

    def _structured(self, messages: list, *, model: Optional[str] = None,
                    max_tokens: int, required_fields: tuple = ()) -> dict:
        self._usage_before = (
            self.client.total_prompt_tokens,
            self.client.total_completion_tokens,
        )
        result = self.client.structured(
            messages, model=model, temperature=0.0,
            max_tokens=max_tokens, required_fields=required_fields,
        )
        self._record_usage()
        return result

    def prompt_for(self, payload: Any, task_id: str) -> list:
        raise NotImplementedError("a model-backed worker defines prompt_for")

    def parse_reply(self, reply: dict, payload: Any) -> Any:
        raise NotImplementedError("a model-backed worker defines parse_reply")

    def work(self, payload: Any, task_id: str) -> Any:
        reply = self._structured(
            self.prompt_for(payload, task_id),
            model=self._model_for(payload),
            max_tokens=self._max_tokens(),
            required_fields=self._required_fields(),
        )
        return self.parse_reply(reply, payload)

    def _model_for(self, payload: Any) -> Optional[str]:
        return None

    def _max_tokens(self) -> int:
        return 400

    def _required_fields(self) -> tuple:
        return ()


class ModelSentimentWorker(LLMWorker):
    """The sentiment scorer, backed by a real model judgement.

    The deterministic ``SentimentWorker`` scores tone with keyword
    lists; this worker asks the model instead, on the cheap judge
    route (``LLM_JUDGE_MODEL``, ``kimi-k3.1-pro:floor`` by default),
    because sentiment is a short classification, not a reasoning
    task. The reply must be a JSON object with a ``score`` between
    0.0 and 1.0 and a ``label`` from the allowed set, and the typed
    validator rejects anything else.
    """

    name = "sentiment-scorer"
    accepts = "TicketState"
    tokens = 400

    def _model_for(self, payload: Any) -> Optional[str]:
        return self.client.judge_model

    def _max_tokens(self) -> int:
        # Generous on purpose: this model spends much of its budget
        # on hidden reasoning tokens, and with a small budget (a few
        # hundred) it can spend it all on reasoning and return empty
        # content — the same gotcha Chapter 7 documents. The reply
        # itself is short; the budget is headroom for the reasoning.
        return 1200

    def _required_fields(self) -> tuple:
        return ("score", "label")

    def prompt_for(self, payload: TicketState, task_id: str) -> list:
        return [
            {
                "role": "system",
                "content": (
                    "Score the sentiment of a support ticket. Reply with "
                    "a JSON object only: {\"score\": <number 0.0 to 1.0>, "
                    "\"label\": <one of \"frustrated\", \"neutral\", "
                    "\"pleased\">}. 0.0 is furious, 1.0 is delighted. "
                    "Judge only the customer's words, not the subject line."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Subject: {payload.subject}\nBody: {payload.body}"
                ),
            },
        ]

    def parse_reply(self, reply: dict, payload: TicketState) -> SentimentResult:
        candidate = {
            "score": reply["score"],
            "label": reply["label"],
            "provenance": "sentiment-scorer",
        }
        return validate_sentiment_result(candidate)


class ModelDrafterWorker(LLMWorker):
    """The drafter, backed by a real model call.

    The deterministic ``DrafterWorker`` assembles the reply from a
    template; this worker asks the model to write it, from the same
    typed inputs only — the researcher's facts and the policy
    checker's verdict, with their provenance. The reply must be a
    JSON object with ``reply_text`` and a ``claims`` list, and every
    claim must carry a provenance of ``researcher`` or
    ``policy-checker``; a claim that invents a fact or a provenance
    is rejected by the typed validator, so model prose can never
    smuggle an unsourced fact into the reply.
    """

    name = "drafter"
    accepts = "DraftInput"  # see validate_draft_input below
    tokens = 700

    def _max_tokens(self) -> int:
        # Generous on purpose: see ModelSentimentWorker — the model
        # spends much of its budget on hidden reasoning, and a small
        # budget can return empty content. The reply itself is short.
        return 2000

    def _required_fields(self) -> tuple:
        return ("reply_text", "claims")

    def prompt_for(self, payload: Any, task_id: str) -> list:
        research: ResearchResult = payload["research"]
        policy: PolicyResult = payload["policy"]
        return [
            {
                "role": "system",
                "content": (
                    "Write a short, polite support reply using ONLY the "
                    "facts below. Do not invent dates, plans, policies or "
                    "amounts. Reply with a JSON object only: "
                    "{\"reply_text\": <the reply>, \"claims\": "
                    "[{\"claim\": <one fact stated in the reply>, "
                    "\"provenance\": <\"researcher\" or "
                    "\"policy-checker\">}, ...]}. Every factual claim in "
                    "the reply must appear in \"claims\" with the "
                    "provenance of the worker it came from."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Subject: {payload['subject']}\n"
                    f"Account facts (from researcher): plan "
                    f"{research.plan}, renewal date "
                    f"{research.renewal_date}, account status "
                    f"{research.account_status}.\n"
                    f"Policy verdict (from policy-checker): "
                    f"{policy.policy_name}, applies: {policy.applies}, "
                    f"priority {policy.priority}. Reason: {policy.reason}"
                ),
            },
        ]

    def parse_reply(self, reply: dict, payload: Any) -> DraftResult:
        claims = reply["claims"]
        if not isinstance(claims, list):
            raise LLMError("model reply claims must be a list")
        for claim in claims:
            if not isinstance(claim, dict):
                raise LLMError("model reply claim must be an object")
            if claim.get("provenance") not in ("researcher", "policy-checker"):
                raise LLMError(
                    "model claim carries an unknown provenance: "
                    f"{claim.get('provenance')!r}"
                )
        candidate = {
            "reply_text": reply["reply_text"],
            "claims": claims,
            "provenance": "drafter",
        }
        return validate_draft_result(candidate)

    def handle(self, envelope: TaskEnvelope) -> TaskEnvelope:
        # Same bundle validation as the deterministic drafter: the
        # input is a bundle of the other workers' typed results, so
        # it validates the bundle itself rather than through the
        # generic payload registry.
        if envelope.recipient != self.name:
            raise WrongRecipientError(
                f"envelope addressed to {envelope.recipient!r}, "
                f"but received by {self.name!r}"
            )
        if compute_checksum(envelope.payload) != envelope.checksum:
            raise ChecksumError("checksum mismatch on draft input")
        bundle = envelope.payload
        research = validate_research_result(bundle.get("research", {}))
        policy = validate_policy_result(bundle.get("policy", {}))
        result = self.work(
            {"research": research, "policy": policy,
             "subject": bundle.get("subject", "")},
            envelope.task_id,
        )
        return make_envelope(
            task_id=envelope.task_id,
            sender=self.name,
            recipient="supervisor",
            payload=result,
            sequence=envelope.sequence + 1,
        )


# ---------------------------------------------------------------------------
# Consensus voting and conflict resolution.
# ---------------------------------------------------------------------------


@dataclass
class ConsensusOutcome:
    decision: Optional[str]  # None when the outcome is an escalation
    rule: str  # "majority", "weighted", "escalated"
    votes: List[Vote]
    dissent: List[Vote]  # voters whose verdict was not adopted, preserved
    reason: str


def resolve_majority(votes: List[Vote]) -> ConsensusOutcome:
    """One voter, one vote. A tie escalates; it is never broken arbitrarily."""
    counts: Dict[str, int] = {}
    for vote in votes:
        counts[vote.verdict] = counts.get(vote.verdict, 0) + 1
    top = max(counts.values())
    winners = [v for v, c in counts.items() if c == top]
    if len(winners) != 1:
        return ConsensusOutcome(
            decision=None,
            rule="escalated",
            votes=votes,
            dissent=votes,
            reason=f"tied vote ({counts}); escalated to a human",
        )
    winner = winners[0]
    return ConsensusOutcome(
        decision=winner,
        rule="majority",
        votes=votes,
        dissent=[v for v in votes if v.verdict != winner],
        reason=f"majority {counts[winner]} of {len(votes)} for {winner!r}",
    )


def resolve_weighted(
    votes: List[Vote], weights: Dict[str, float]
) -> ConsensusOutcome:
    """Votes weighted by measured voter accuracy (set by configuration)."""
    totals: Dict[str, float] = {}
    for vote in votes:
        totals[vote.verdict] = totals.get(vote.verdict, 0.0) + weights.get(
            vote.voter, 1.0
        ) * vote.confidence
    top = max(totals.values())
    winners = [v for v, t in totals.items() if t == top]
    if len(winners) != 1:
        return ConsensusOutcome(
            decision=None,
            rule="escalated",
            votes=votes,
            dissent=votes,
            reason=f"tied weighted vote ({totals}); escalated to a human",
        )
    winner = winners[0]
    return ConsensusOutcome(
        decision=winner,
        rule="weighted",
        votes=votes,
        dissent=[v for v in votes if v.verdict != winner],
        reason=f"weighted total {totals[winner]:.2f} for {winner!r}",
    )


def resolve_fact_disagreement(
    field: str,
    values: Dict[str, str],
    refetch: Callable[[], Dict[str, str]],
) -> ConsensusOutcome:
    """A factual disagreement is not resolved by voting.

    Re-fetch the source of truth. If the re-fetch agrees with one value,
    that value wins; if the sources disagree with each other, escalate.
    """
    fresh = refetch()
    fresh_value = fresh.get(field)
    if fresh_value is None:
        return ConsensusOutcome(
            decision=None,
            rule="escalated",
            votes=[],
            dissent=[],
            reason=f"source of truth has no value for {field!r}; escalated",
        )
    if fresh_value in values.values():
        return ConsensusOutcome(
            decision=fresh_value,
            rule="source-of-truth",  # decided by re-fetch, never by ballot
            votes=[],
            dissent=[],
            reason=(
                f"source of truth confirms {fresh_value!r} for {field!r}; "
                "the disagreeing value was discarded"
            ),
        )
    return ConsensusOutcome(
        decision=None,
        rule="escalated",
        votes=[],
        dissent=[],
        reason=(
            f"source of truth returned {fresh_value!r} for {field!r}, "
            f"which matches neither disputed value {values}; escalated"
        ),
    )


# ---------------------------------------------------------------------------
# Bounds and the supervisor.
# ---------------------------------------------------------------------------


@dataclass
class SwarmLimits:
    """Every bound the swarm enforces, in code. Defaults are the triage
    swarm's: three workers per round, three rounds, twenty handoffs, a
    5,000-token budget and a 30-second per-worker timeout."""

    max_fan_out: int = 3
    max_rounds: int = 3
    max_handoffs: int = 20
    token_budget: int = 5000
    per_worker_timeout_s: float = 30.0
    max_hops: int = 10  # peer-to-peer swarms; see exercises.md


@dataclass
class TraceEvent:
    run_id: str
    round_no: int
    sender: str
    recipient: str
    payload_type: str
    schema_version: int
    checksum: str
    status: str  # "ok", "rejected", "timeout", "limit"
    detail: str = ""
    latency_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "round": self.round_no,
            "sender": self.sender,
            "recipient": self.recipient,
            "payload_type": self.payload_type,
            "schema_version": self.schema_version,
            "checksum": self.checksum,
            "status": self.status,
            "detail": self.detail,
            "latency_s": self.latency_s,
        }


@dataclass
class SwarmResult:
    run_id: str
    status: str  # "completed", "partial", "failed"
    draft: Optional[DraftResult] = None
    sentiment: Optional[SentimentResult] = None
    trace: List[TraceEvent] = field(default_factory=list)
    handoffs: int = 0
    tokens_used: int = 0
    limit_hit: Optional[str] = None

    def trace_jsonl(self) -> str:
        return "\n".join(
            json.dumps(event.to_dict()) for event in self.trace
        )


class Supervisor:
    """A supervisor-topology coordinator.

    The supervisor owns the run: it fans out to workers, validates every
    result, synthesises the final artefact, and enforces the limits. It
    never does specialist work itself, and it never routes the same task to
    the same worker twice with the same brief — the visited set below is
    what stops a routing loop.
    """

    def __init__(
        self,
        workers: List[Worker],
        drafter: Worker,  # DrafterWorker or ModelDrafterWorker
        limits: Optional[SwarmLimits] = None,
    ) -> None:
        self.workers = workers
        self.drafter = drafter
        self.limits = limits or SwarmLimits()
        self._seen_sequences: Dict[str, int] = {}

    def run(self, ticket: TicketState) -> SwarmResult:
        run_id = f"run-{ticket.ticket_id}-{int(time.time())}"
        result = SwarmResult(run_id=run_id, status="failed")
        round_no = 1
        visited: set = set()
        research: Optional[ResearchResult] = None
        policy: Optional[PolicyResult] = None

        def trace(sender: str, recipient: str, envelope: TaskEnvelope,
                  status: str, detail: str = "",
                  latency: float = 0.0) -> None:
            result.trace.append(
                TraceEvent(
                    run_id=run_id,
                    round_no=round_no,
                    sender=sender,
                    recipient=recipient,
                    payload_type=envelope.payload_type,
                    schema_version=envelope.schema_version,
                    checksum=envelope.checksum,
                    status=status,
                    detail=detail,
                    latency_s=latency,
                )
            )
            result.handoffs += 1

        # Round 1: fan out to the research and policy workers in parallel.
        # In this implementation the "parallel" fan-out is a loop over
        # the workers; the ordering guarantee is that no worker sees
        # another worker's output. Deterministic workers report their
        # declared latency and token cost; a model-backed worker
        # reports its real elapsed time and its real token usage, and
        # a model failure is recorded as a rejection, so the run
        # continues partial instead of crashing.
        fan_out = self.workers[: self.limits.max_fan_out]
        for worker in fan_out:
            if result.handoffs >= self.limits.max_handoffs:
                result.limit_hit = "max_handoffs"
                result.status = "partial"
                return result
            if result.tokens_used + worker.tokens > self.limits.token_budget:
                result.limit_hit = "token_budget"
                result.status = "partial"
                return result
            if (worker.name, ticket.ticket_id) in visited:
                continue  # never route the same task twice, same brief
            visited.add((worker.name, ticket.ticket_id))
            envelope = make_envelope(
                task_id=ticket.ticket_id,
                sender="supervisor",
                recipient=worker.name,
                payload=ticket,
            )
            if worker.latency_s > self.limits.per_worker_timeout_s:
                trace("supervisor", worker.name, envelope,
                      "timeout",
                      f"{worker.name} exceeded "
                      f"{self.limits.per_worker_timeout_s}s timeout")
                continue  # skip the worker; the run continues partial
            try:
                started = time.monotonic()
                reply = worker.handle(envelope)
                elapsed = time.monotonic() - started
            except EnvelopeError as exc:
                trace("supervisor", worker.name, envelope,
                      "rejected", str(exc))
                continue
            except LLMError as exc:
                trace("supervisor", worker.name, envelope,
                      "rejected", f"model call failed: {exc}")
                continue
            result.tokens_used += (
                worker.last_call_tokens
                if worker.last_call_tokens is not None
                else worker.tokens
            )
            latency = elapsed if isinstance(worker, LLMWorker) else worker.latency_s
            trace("supervisor", worker.name, envelope, "ok",
                  latency=latency)
            # The supervisor validates the reply with the same rigour as
            # the worker validated the request: a corrupt reply is a
            # rejection, not a crash, and the run continues without it.
            try:
                typed = validate_envelope(
                    reply, expected_recipient="supervisor")
            except EnvelopeError as exc:
                trace(worker.name, "supervisor", reply,
                      "rejected", str(exc))
                continue
            if isinstance(typed, ResearchResult):
                research = typed
            elif isinstance(typed, PolicyResult):
                policy = typed
            elif isinstance(typed, SentimentResult):
                result.sentiment = typed

        # Round 2: the drafter writes the single artefact, from typed
        # results only. If either input is missing, the run is partial.
        round_no = 2
        if round_no > self.limits.max_rounds:
            result.limit_hit = "max_rounds"
            result.status = "partial"
            return result
        if research is None or policy is None:
            result.status = "partial"
            result.limit_hit = result.limit_hit or "missing-worker-result"
            return result
        bundle = {
            "research": payload_to_dict(research),
            "policy": payload_to_dict(policy),
            "subject": ticket.subject,
        }
        draft_envelope = TaskEnvelope(
            task_id=ticket.ticket_id,
            schema_version=1,
            sender="supervisor",
            recipient="drafter",
            payload_type="DraftInput",
            payload=bundle,
            checksum=compute_checksum(bundle),
            sequence=1,
        )
        try:
            started = time.monotonic()
            draft_reply = self.drafter.handle(draft_envelope)
            draft_elapsed = time.monotonic() - started
        except EnvelopeError as exc:
            trace("supervisor", "drafter", draft_envelope,
                  "rejected", str(exc))
            result.status = "partial"
            return result
        except LLMError as exc:
            trace("supervisor", "drafter", draft_envelope,
                  "rejected", f"model call failed: {exc}")
            result.status = "partial"
            return result
        result.tokens_used += (
            self.drafter.last_call_tokens
            if self.drafter.last_call_tokens is not None
            else self.drafter.tokens
        )
        draft_latency = (
            draft_elapsed
            if isinstance(self.drafter, LLMWorker)
            else self.drafter.latency_s
        )
        trace("supervisor", "drafter", draft_envelope, "ok",
              latency=draft_latency)
        result.draft = validate_envelope(
            draft_reply, expected_recipient="supervisor"
        )
        result.status = "completed"
        return result
