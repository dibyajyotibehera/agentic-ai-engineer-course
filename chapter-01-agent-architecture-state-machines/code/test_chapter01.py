"""Tests for Chapter 1: agent architecture and state machines.

Run with ``python -m unittest test_chapter01 -v`` from this directory.
With the standard library only, the deterministic tests run: the three
control-flow shapes, state serialisation and migration, the file-backed
blob store, the receipt ledger, the Temporal simulator's replay, and —
when LangGraph is installed (``pip install -r requirements.txt``) — the
crash-and-resume cycle. Each deterministic test names the property it
covers, so a failing test reads as an architecture regression, not as a
generic assertion failure.

The live tests at the end need a configured model backend
(see "Connect a model" in the root README) and exercise
the real model calls: drafting, reviewing, and the free-form loop's
next-step decision, plus the LangGraph crash/resume cycle and the
Temporal simulator with a real model. When no backend is configured,
the live tests skip instead of failing: a skipped live test means
"not run", not "passed". The live tests take a few minutes — each model
call takes several seconds — and cost a few cents in total.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm_client import LLMClient  # noqa: E402
from live_review import decide_next_step, draft_reply, review_draft  # noqa: E402
from receipt_ledger import receipt_count, record_receipt  # noqa: E402

# The demo modules are named 01_... / 02_... / 04_..., which are not
# valid identifiers for an import statement, so load them by file path.
_CODE_DIR = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _CODE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # Register before executing: dataclasses resolves its own module
    # through sys.modules while the class body runs.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dag_vs_cycle = _load("01_dag_vs_cycle")
state_serialisation = _load("03_state_serialisation")
temporal_workflow = _load("04_temporal_workflow")

AgentState = state_serialisation.AgentState
BlobStore = state_serialisation.BlobStore
StateError = state_serialisation.StateError
TicketV2 = state_serialisation.TicketV2

try:
    import langgraph  # noqa: F401
    from langgraph.checkpoint.sqlite import SqliteSaver

    langgraph_demo = _load("02_langgraph_checkpoint_resume")
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False


def _llm_available() -> bool:
    return LLMClient.has_backend()


def _live_client() -> LLMClient:
    return LLMClient()


class TestThreeShapesDeterministic(unittest.TestCase):
    def test_dag_escalates_the_ticket_that_needs_a_second_draft(self) -> None:
        log = dag_vs_cycle.run_dag(dag_vs_cycle.TICKETS,
                                   dag_vs_cycle.DeterministicEngine())
        self.assertEqual(log.resolved, ["T-101", "T-103"])
        self.assertEqual(log.escalated, ["T-102"])

    def test_cyclic_graph_resolves_the_same_ticket_on_the_second_draft(self) -> None:
        log = dag_vs_cycle.run_cyclic(dag_vs_cycle.TICKETS,
                                      dag_vs_cycle.DeterministicEngine())
        self.assertEqual(log.resolved, ["T-101", "T-102", "T-103"])
        self.assertEqual(log.escalated, [])

    def test_free_form_loop_resolves_all_tickets(self) -> None:
        log = dag_vs_cycle.run_free_form(dag_vs_cycle.TICKETS,
                                         dag_vs_cycle.DeterministicEngine())
        self.assertEqual(log.resolved, ["T-101", "T-102", "T-103"])
        self.assertEqual(log.escalated, [])

    def test_code_owned_guard_overrules_a_model_that_always_redrafts(self) -> None:
        """A decider that never stops must still terminate: the guard owns it."""

        class StubbornEngine(dag_vs_cycle.DeterministicEngine):
            def decide(self, ticket, attempt, draft_text, passed, reason,
                       history):
                return "redraft"

        ticket = dag_vs_cycle.Ticket("T-102", "Export fails", needs_rework=True)
        log = dag_vs_cycle.run_free_form([ticket], StubbornEngine())
        self.assertEqual(log.escalated, ["T-102"])
        self.assertEqual(log.resolved, [])
        # Bounded: at most MAX_ATTEMPTS drafts plus the guard's escalation.
        drafts = [s for s in log.steps if s.startswith("draft")]
        self.assertEqual(len(drafts), dag_vs_cycle.MAX_ATTEMPTS)

    def test_deterministic_reviewer_fails_only_the_first_draft(self) -> None:
        ticket = dag_vs_cycle.Ticket("T-102", "Export fails", needs_rework=True)
        self.assertFalse(dag_vs_cycle.review(ticket, 1))
        self.assertTrue(dag_vs_cycle.review(ticket, 2))


class TestStateSerialisation(unittest.TestCase):
    def test_round_trip_preserves_the_state(self) -> None:
        state = AgentState(
            run_id="run-42",
            tickets=[TicketV2("T-101", "Refund for a duplicate charge")],
            log=["draft T-101", "review T-101: pass"],
        )
        self.assertEqual(AgentState.from_json(state.to_json()), state)

    def test_version_1_snapshot_migrates_to_version_2(self) -> None:
        raw = json.dumps({"version": 1, "run_id": "run-9",
                          "tickets": ["T-001"], "log": []})
        migrated = AgentState.from_json(raw)
        self.assertEqual(migrated.version, 2)
        self.assertEqual(migrated.tickets[0].ticket_id, "T-001")
        self.assertEqual(migrated.tickets[0].status, "open")

    def test_future_version_is_rejected_loudly(self) -> None:
        with self.assertRaises(StateError):
            AgentState.from_json(json.dumps({"version": 99, "tickets": []}))

    def test_snapshot_without_a_version_is_rejected(self) -> None:
        with self.assertRaises(StateError):
            AgentState.from_json(json.dumps({"tickets": []}))

    def test_secret_field_is_refused_at_write_time(self) -> None:
        state = AgentState(run_id="run-77")
        state.attachments["api_key"] = "sk-live-should-never-be-here"
        with self.assertRaises(StateError):
            state.to_json()

    def test_blob_store_writes_real_files_and_reads_them_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = BlobStore(tmpdir)
            key = store.put("blob://exports/report.csv", b"a,b\n1,2\n")
            self.assertTrue((Path(tmpdir) / "exports" / "report.csv").exists())
            self.assertEqual(store.get(key), b"a,b\n1,2\n")

    def test_blob_store_refuses_a_key_that_escapes_the_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = BlobStore(tmpdir)
            with self.assertRaises(ValueError):
                store.put("blob://../escape.txt", b"x")


class TestReceiptLedger(unittest.TestCase):
    def test_second_receipt_for_the_same_ticket_appends_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = os.path.join(tmpdir, "receipts.jsonl")
            first = record_receipt("T-102", ledger)
            second = record_receipt("T-102", ledger)
            self.assertEqual(first, second)
            self.assertEqual(receipt_count("T-102", ledger), 1)
            self.assertEqual(len(Path(ledger).read_text().splitlines()), 1)


class TestTemporalSimulator(unittest.TestCase):
    def _temp_paths(self, tmpdir: str):
        return (os.path.join(tmpdir, "event_log.json"),
                os.path.join(tmpdir, "receipts.jsonl"))

    def test_second_run_replays_every_activity_and_sends_one_receipt(self) -> None:
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path, ledger_path = self._temp_paths(tmpdir)
            first = io.StringIO()
            with redirect_stdout(first):
                result = temporal_workflow.simulate(
                    log_path=log_path, ledger_path=ledger_path)
            self.assertEqual(result, 0)
            self.assertIn("executed: draft_reply", first.getvalue())
            self.assertEqual(receipt_count("T-102", ledger_path), 1)
            second = io.StringIO()
            with redirect_stdout(second):
                result = temporal_workflow.simulate(
                    log_path=log_path, ledger_path=ledger_path)
            self.assertEqual(result, 0)
            self.assertIn("replay: draft_reply", second.getvalue())
            self.assertNotIn("executed:", second.getvalue())
            self.assertEqual(receipt_count("T-102", ledger_path), 1)

    def test_fresh_run_after_deleting_the_log_still_sends_one_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path, ledger_path = self._temp_paths(tmpdir)
            temporal_workflow.simulate(log_path=log_path, ledger_path=ledger_path)
            os.remove(log_path)
            temporal_workflow.simulate(log_path=log_path, ledger_path=ledger_path)
            self.assertEqual(receipt_count("T-102", ledger_path), 1)

    def test_deterministic_activities_match_the_rule(self) -> None:
        draft = temporal_workflow.draft_reply_sync("T-102", "Export fails", 1)
        self.assertIn("draft v1", draft.text)
        review = temporal_workflow.review_draft_sync("T-102", 1, True, draft.text)
        self.assertFalse(review.passed)
        review2 = temporal_workflow.review_draft_sync("T-102", 2, True, draft.text)
        self.assertTrue(review2.passed)


@unittest.skipUnless(HAS_LANGGRAPH, "needs langgraph installed")
class TestLangGraphCheckpointResume(unittest.TestCase):
    def test_crash_after_two_steps_resumes_with_no_reprocessing(self) -> None:
        import itertools

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "checkpoints.db")
            ledger_path = os.path.join(tmpdir, "receipts.jsonl")
            config = {"configurable": {"thread_id": "test-1"}}
            with SqliteSaver.from_conn_string(db_path) as saver:
                graph = langgraph_demo.build_graph(saver, ledger_path=ledger_path)
                # Take two super-steps, then stop iterating — the
                # in-process equivalent of the process dying mid-run.
                streamed = list(itertools.islice(
                    graph.stream(langgraph_demo.initial_state(), config,
                                 stream_mode="updates"), 2))
            self.assertEqual(len(streamed), 2)
            with SqliteSaver.from_conn_string(db_path) as saver:
                graph = langgraph_demo.build_graph(saver, ledger_path=ledger_path)
                result = graph.invoke(None, config)
            self.assertEqual(result["resolved"],
                             ["T-101", "T-102", "T-103", "T-104"])
            self.assertEqual(result["escalated"], [])
            self.assertEqual(len(result["resolved"]),
                             len(set(result["resolved"])))
            self.assertEqual(result["receipts_sent"], result["resolved"])
            for ticket_id in result["receipts_sent"]:
                self.assertEqual(receipt_count(ticket_id, ledger_path), 1)

    def test_history_holds_at_least_twenty_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "checkpoints.db")
            ledger_path = os.path.join(tmpdir, "receipts.jsonl")
            config = {"configurable": {"thread_id": "test-2"}}
            with SqliteSaver.from_conn_string(db_path) as saver:
                graph = langgraph_demo.build_graph(saver, ledger_path=ledger_path)
                graph.invoke(langgraph_demo.initial_state(), config)
                snapshots = list(graph.get_state_history(config))
            self.assertGreaterEqual(len(snapshots), 20)


@unittest.skipUnless(_llm_available(), "needs a configured LLM backend")
class TestLiveReview(unittest.TestCase):
    """Live tests: drafting and reviewing against the real model."""

    def test_first_draft_is_a_holding_reply_that_fails_review(self) -> None:
        client = _live_client()
        text = draft_reply(client, "T-102", "Export fails on large workspaces", 1,
                           needs_rework=True)
        self.assertTrue(text.strip())
        verdict = review_draft(client, "T-102", text, 1)
        self.assertFalse(verdict["passed"])
        self.assertTrue(verdict["reason"])

    def test_straightforward_ticket_passes_on_the_first_draft(self) -> None:
        client = _live_client()
        text = draft_reply(client, "T-101", "Refund for a duplicate charge", 1,
                           needs_rework=False)
        self.assertTrue(text.strip())
        verdict = review_draft(client, "T-101", text, 1)
        self.assertTrue(verdict["passed"])

    def test_second_draft_is_substantive_and_passes_review(self) -> None:
        client = _live_client()
        text = draft_reply(client, "T-102", "Export fails on large workspaces", 2)
        self.assertTrue(text.strip())
        verdict = review_draft(client, "T-102", text, 2)
        self.assertTrue(verdict["passed"])

    def test_decision_returns_a_valid_action(self) -> None:
        client = _live_client()
        verdict = decide_next_step(
            client, "T-102", "Export fails on large workspaces", 1,
            "We are looking into it.", False,
            "no workaround, next step, or timeline", [])
        self.assertIn(verdict["action"], ("resolve", "redraft", "escalate"))


@unittest.skipUnless(_llm_available(), "needs a configured LLM backend")
class TestLiveFreeFormLoop(unittest.TestCase):
    """Live test: the free-form loop against the real model."""

    def test_live_loop_resolves_a_ticket_that_needs_one_redraft(self) -> None:
        client = _live_client()
        ticket = dag_vs_cycle.Ticket("T-102", "Export fails on large workspaces",
                                     needs_rework=True)
        log = dag_vs_cycle.run_free_form(
            [ticket], dag_vs_cycle.LiveEngine(client))
        self.assertEqual(log.resolved, ["T-102"])
        self.assertEqual(log.escalated, [])


@unittest.skipUnless(_llm_available(), "needs a configured LLM backend")
class TestLiveTemporalSimulator(unittest.TestCase):
    """Live test: the simulator with a real model, then replay with none."""

    def test_second_run_replays_the_recorded_model_results(self) -> None:
        import io
        from contextlib import redirect_stdout

        client = _live_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "event_log.json")
            ledger_path = os.path.join(tmpdir, "receipts.jsonl")
            first = io.StringIO()
            with redirect_stdout(first):
                result = temporal_workflow.simulate(
                    live_client=client, log_path=log_path,
                    ledger_path=ledger_path)
            self.assertEqual(result, 0)
            self.assertIn("executed: draft_reply", first.getvalue())
            tokens_after_first = (client.total_prompt_tokens
                                  + client.total_completion_tokens)
            self.assertGreater(tokens_after_first, 0)
            second = io.StringIO()
            with redirect_stdout(second):
                result = temporal_workflow.simulate(
                    live_client=client, log_path=log_path,
                    ledger_path=ledger_path)
            self.assertEqual(result, 0)
            self.assertNotIn("executed:", second.getvalue())
            # The replay made no new model calls: the recorded results
            # were returned instead of re-executing the activities.
            self.assertEqual(client.total_prompt_tokens
                             + client.total_completion_tokens,
                             tokens_after_first)
            self.assertEqual(receipt_count("T-102", ledger_path), 1)


@unittest.skipUnless(_llm_available() and HAS_LANGGRAPH,
                     "needs a configured LLM backend and langgraph")
class TestLiveLangGraphCrashResume(unittest.TestCase):
    """Live test: the checkpoint cycle with a real model."""

    def test_live_crash_after_two_steps_resumes_and_resolves_all(self) -> None:
        import itertools

        client = _live_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "checkpoints.db")
            ledger_path = os.path.join(tmpdir, "receipts.jsonl")
            config = {"configurable": {"thread_id": "live-1"}}
            with SqliteSaver.from_conn_string(db_path) as saver:
                graph = langgraph_demo.build_graph(
                    saver, live_client=client, ledger_path=ledger_path)
                streamed = list(itertools.islice(
                    graph.stream(langgraph_demo.initial_state(), config,
                                 stream_mode="updates"), 2))
            self.assertEqual(len(streamed), 2)
            with SqliteSaver.from_conn_string(db_path) as saver:
                graph = langgraph_demo.build_graph(
                    saver, live_client=client, ledger_path=ledger_path)
                result = graph.invoke(None, config)
            self.assertEqual(result["resolved"],
                             ["T-101", "T-102", "T-103", "T-104"])
            self.assertEqual(result["escalated"], [])
            self.assertEqual(len(result["receipts_sent"]), 4)
            for ticket_id in result["receipts_sent"]:
                self.assertEqual(receipt_count(ticket_id, ledger_path), 1)


if __name__ == "__main__":
    unittest.main()
