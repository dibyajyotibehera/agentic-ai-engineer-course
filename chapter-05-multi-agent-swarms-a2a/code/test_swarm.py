"""Tests for Chapter 5: Multi-Agent Swarms and Agent-to-Agent Handoffs.

Run from the chapter directory:

    python -m unittest discover -s code -v

Standard library only; no model API key needed.
"""

import json
import unittest
from dataclasses import replace

from swarm import (
    BudgetExceeded,  # noqa: F401  (re-exported for exercise use)
    ChecksumError,
    DrafterWorker,
    PayloadValidationError,
    PolicyResult,
    PolicyWorker,
    ResearchResult,
    ResearcherWorker,
    SchemaVersionError,
    SentimentResult,
    SentimentWorker,
    Supervisor,
    SwarmLimits,
    TicketState,
    Vote,
    Worker,
    WrongRecipientError,
    compute_checksum,
    make_envelope,
    resolve_fact_disagreement,
    resolve_majority,
    resolve_weighted,
    validate_envelope,
    validate_policy_result,
)


def make_ticket(**overrides) -> TicketState:
    fields = {
        "ticket_id": "tkt-1001",
        "customer_id": "cust-001",
        "subject": "Refund for the annual plan",
        "body": "I was charged twice and would like a refund, please.",
    }
    fields.update(overrides)
    return TicketState(**fields)


def make_supervisor(**limit_overrides) -> Supervisor:
    return Supervisor(
        workers=[ResearcherWorker(), PolicyWorker(), SentimentWorker()],
        drafter=DrafterWorker(),
        limits=SwarmLimits(**limit_overrides),
    )


class TestTypedStatePassing(unittest.TestCase):
    def test_valid_envelope_round_trips(self):
        envelope = make_envelope(
            "tkt-1", "researcher", "supervisor",
            ResearchResult(customer_id="cust-001", plan="annual",
                           renewal_date="2026-03-14",
                           account_status="active"),
        )
        typed = validate_envelope(envelope, expected_recipient="supervisor")
        self.assertEqual(typed.renewal_date, "2026-03-14")

    def test_flipped_character_rejected_by_checksum(self):
        envelope = make_envelope(
            "tkt-1", "researcher", "supervisor",
            ResearchResult(customer_id="cust-001", plan="annual",
                           renewal_date="2026-03-14",
                           account_status="active"),
        )
        tampered = replace(envelope, payload={**envelope.payload,
                                              "plan": "annuak"})
        with self.assertRaises(ChecksumError):
            validate_envelope(tampered, expected_recipient="supervisor")

    def test_wrong_checksum_rejected(self):
        envelope = make_envelope(
            "tkt-1", "researcher", "supervisor",
            ResearchResult(customer_id="cust-001", plan="annual",
                           renewal_date="2026-03-14",
                           account_status="active"),
        )
        with self.assertRaises(ChecksumError):
            validate_envelope(replace(envelope, checksum="0" * 64),
                              expected_recipient="supervisor")

    def test_unknown_schema_version_rejected(self):
        envelope = make_envelope(
            "tkt-1", "researcher", "supervisor",
            ResearchResult(customer_id="cust-001", plan="annual",
                           renewal_date="2026-03-14",
                           account_status="active"),
            schema_version=99,
        )
        with self.assertRaises(SchemaVersionError):
            validate_envelope(envelope, expected_recipient="supervisor")

    def test_wrong_recipient_rejected(self):
        envelope = make_envelope(
            "tkt-1", "researcher", "supervisor",
            ResearchResult(customer_id="cust-001", plan="annual",
                           renewal_date="2026-03-14",
                           account_status="active"),
        )
        with self.assertRaises(WrongRecipientError):
            validate_envelope(envelope, expected_recipient="drafter")

    def test_priority_outside_allowed_set_rejected(self):
        with self.assertRaises(PayloadValidationError):
            validate_policy_result({
                "applies": True, "policy_name": "refund-window-30d",
                "priority": "pretty urgent", "reason": "Refund requested.",
            })

    def test_missing_required_field_rejected(self):
        envelope = make_envelope(
            "tkt-1", "researcher", "supervisor",
            ResearchResult(customer_id="cust-001", plan="annual",
                           renewal_date="2026-03-14",
                           account_status="active"),
        )
        dropped = {k: v for k, v in envelope.payload.items()
                   if k != "renewal_date"}
        missing = replace(envelope, payload=dropped,
                          checksum=compute_checksum(dropped))
        with self.assertRaises(PayloadValidationError):
            validate_envelope(missing, expected_recipient="supervisor")

    def test_sentiment_score_out_of_range_rejected(self):
        envelope = make_envelope(
            "tkt-1", "sentiment-scorer", "supervisor",
            SentimentResult(score=1.5, label="pleased"),
        )
        with self.assertRaises(PayloadValidationError):
            validate_envelope(envelope, expected_recipient="supervisor")


class TestSupervisorTopology(unittest.TestCase):
    def test_full_run_completes_with_three_workers(self):
        result = make_supervisor().run(make_ticket())
        self.assertEqual(result.status, "completed")
        self.assertIsNotNone(result.draft)
        self.assertIn("2026-03-14", result.draft.reply_text)
        # Every handoff is in the trace with a valid checksum.
        for event in result.trace:
            self.assertEqual(len(event.checksum), 64)

    def test_draft_claims_carry_provenance(self):
        result = make_supervisor().run(make_ticket())
        assert result.draft is not None
        for claim in result.draft.claims:
            self.assertIn("provenance", claim)
            self.assertIn(claim["provenance"],
                          ("researcher", "policy-checker"))

    def test_corrupt_payload_never_reaches_drafter(self):
        # A researcher that returns a tampered envelope must not produce a
        # draft built from corrupted state: the supervisor records the
        # rejection and ends partial.
        class CorruptResearcher(ResearcherWorker):
            def handle(self, envelope):
                reply = super().handle(envelope)
                return replace(reply, payload={**reply.payload,
                                               "renewal_date": "1999-01-01"})

        supervisor = Supervisor(
            workers=[CorruptResearcher(), PolicyWorker()],
            drafter=DrafterWorker(),
        )
        result = supervisor.run(make_ticket())
        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.draft)
        self.assertTrue(any(e.status == "rejected" for e in result.trace))

    def test_slow_worker_is_skipped_and_run_continues_partial(self):
        class SlowResearcher(ResearcherWorker):
            latency_s = 120.0  # far above the 30s default timeout

        supervisor = Supervisor(
            workers=[SlowResearcher(), PolicyWorker()],
            drafter=DrafterWorker(),
        )
        result = supervisor.run(make_ticket())
        self.assertEqual(result.status, "partial")
        self.assertTrue(any(e.status == "timeout" for e in result.trace))


class TestConsensus(unittest.TestCase):
    def test_majority_vote_resolves_two_to_one(self):
        outcome = resolve_majority([
            Vote("researcher", "high", 0.8, ["past-due"]),
            Vote("policy-checker", "low", 0.6, ["no policy"]),
            Vote("sentiment-scorer", "high", 0.7, ["frustrated"]),
        ])
        self.assertEqual(outcome.decision, "high")
        self.assertEqual(outcome.rule, "majority")
        # The dissent is preserved, not discarded.
        self.assertEqual(len(outcome.dissent), 1)
        self.assertEqual(outcome.dissent[0].voter, "policy-checker")

    def test_tied_vote_escalates(self):
        outcome = resolve_majority([
            Vote("researcher", "high", 0.8),
            Vote("policy-checker", "low", 0.6),
        ])
        self.assertIsNone(outcome.decision)
        self.assertEqual(outcome.rule, "escalated")

    def test_weighted_vote_uses_configured_weights(self):
        votes = [
            Vote("researcher", "high", 0.8),
            Vote("policy-checker", "low", 0.6),
            Vote("sentiment-scorer", "high", 0.7),
        ]
        outcome = resolve_weighted(
            votes,
            {"researcher": 1.0, "policy-checker": 2.0,
             "sentiment-scorer": 1.0},
        )
        # Weighted: high 1.5 vs low 1.2 — high still wins, but the margin
        # is the weights' doing; with a weight of 3.0 the result flips.
        self.assertEqual(outcome.decision, "high")
        flipped = resolve_weighted(
            votes,
            {"researcher": 1.0, "policy-checker": 3.0,
             "sentiment-scorer": 1.0},
        )
        self.assertEqual(flipped.decision, "low")

    def test_factual_disagreement_refetches_source_of_truth(self):
        outcome = resolve_fact_disagreement(
            "renewal_date",
            {"researcher": "2026-03-14",
             "policy-checker": "2027-03-14"},
            lambda: {"renewal_date": "2026-03-14"},
        )
        self.assertEqual(outcome.decision, "2026-03-14")

    def test_factual_disagreement_escalates_when_sources_disagree(self):
        outcome = resolve_fact_disagreement(
            "renewal_date",
            {"researcher": "2026-03-14",
             "policy-checker": "2027-03-14"},
            lambda: {"renewal_date": "2028-03-14"},
        )
        self.assertIsNone(outcome.decision)
        self.assertEqual(outcome.rule, "escalated")


class TestBounds(unittest.TestCase):
    def test_token_budget_ends_run_partial(self):
        result = make_supervisor(token_budget=100).run(make_ticket())
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.limit_hit, "token_budget")

    def test_max_rounds_ends_run_partial(self):
        result = make_supervisor(max_rounds=1).run(make_ticket())
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.limit_hit, "max_rounds")

    def test_max_handoffs_ends_run_partial(self):
        result = make_supervisor(max_handoffs=1).run(make_ticket())
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.limit_hit, "max_handoffs")

    def test_routing_loop_never_repeats_same_worker(self):
        # The supervisor's visited set means a worker is never routed the
        # same ticket twice; the trace shows each worker at most once in
        # round 1.
        result = make_supervisor().run(make_ticket())
        round_one = [e for e in result.trace if e.round_no == 1]
        recipients = [e.recipient for e in round_one]
        self.assertEqual(len(recipients), len(set(recipients)))


class TestTrace(unittest.TestCase):
    def test_trace_serialises_to_jsonl(self):
        result = make_supervisor().run(make_ticket())
        lines = result.trace_jsonl().splitlines()
        self.assertEqual(len(lines), len(result.trace))
        for line in lines:
            record = json.loads(line)
            self.assertIn("run_id", record)
            self.assertIn("checksum", record)
            self.assertIn("status", record)


if __name__ == "__main__":
    unittest.main()
