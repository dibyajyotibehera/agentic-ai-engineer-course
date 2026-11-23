"""Demo: a supervisor topology with three worker agents and typed state.

Run from the chapter directory:

    python code/demo_supervisor.py

No model API key is needed: the workers are deterministic on purpose
(see ``swarm.py``) — the lesson here is the topology and the handoff
mechanics, and deterministic workers keep that lesson runnable and
repeatable offline. The researcher reads the real ``accounts.json``
file, so the lookup is real local execution, not an in-memory stand-in.
For the same swarm with real model-backed workers (a model sentiment
scorer and a model drafter), run ``code/demo_live_swarm.py`` instead,
which needs a configured model backend.
"""

from swarm import (
    DrafterWorker,
    PolicyWorker,
    ResearcherWorker,
    SentimentWorker,
    Supervisor,
    TicketState,
)


def main() -> None:
    ticket = TicketState(
        ticket_id="tkt-1001",
        customer_id="cust-001",
        subject="Refund for the annual plan",
        body="I was charged for the annual plan twice and I would like a "
        "refund for the duplicate charge, please.",
    )
    supervisor = Supervisor(
        workers=[ResearcherWorker(), PolicyWorker(), SentimentWorker()],
        drafter=DrafterWorker(),
    )
    result = supervisor.run(ticket)

    print(f"run:    {result.run_id}")
    print(f"status: {result.status}")
    print(f"handoffs: {result.handoffs}   tokens used: {result.tokens_used}")
    print()
    print("trace (one line per handoff):")
    print(result.trace_jsonl())
    print()
    if result.draft is not None:
        print("draft reply:")
        print(result.draft.reply_text)
        print()
        print("claims and their provenance:")
        for claim in result.draft.claims:
            print(f"  - {claim['claim']}  [{claim['provenance']}]")
    else:
        print("no draft was produced; the run ended partial.")
        print(f"limit hit: {result.limit_hit}")


if __name__ == "__main__":
    main()
