"""Demo: the same supervisor swarm, with real model-backed workers.

Run from the chapter directory, with a model connected as described
in the root README's "Connect a model" section — key-based:

    LLM_API_KEY=<redacted> LLM_BASE_URL=<your provider's API base URL> \
        LLM_MODEL=<your model name> python code/demo_live_swarm.py

or AWS Bedrock:

    LLM_BACKEND=bedrock LLM_BEDROCK_MODEL_ID=<inference profile ID> \
        python code/demo_live_swarm.py

This is the live version of ``demo_supervisor.py``: the researcher
still reads the real ``accounts.json`` file and the policy checker
still applies its written rule table, because those are deterministic
jobs — but the sentiment scorer and the drafter are real model calls
(``ModelSentimentWorker`` on the judge model, and
``ModelDrafterWorker`` on the full model). The model never returns
free text across a handoff: its structured JSON reply is parsed into
the typed payload and validated, so a malformed reply is a rejection,
not a silent corruption. The demo prints the supervisor's structured
trace, the sentiment verdict, the drafter's reply with its provenance
notes, and the model's token and cost totals for the run.
"""

import sys

from llm_client import LLMClient, LLMError
from swarm import (
    ModelDrafterWorker,
    ModelSentimentWorker,
    PolicyWorker,
    ResearcherWorker,
    Supervisor,
    TicketState,
)


def main() -> None:
    if not LLMClient.has_backend():
        print(
            "No model backend is configured. Set LLM_API_KEY, "
            "LLM_BASE_URL and LLM_MODEL (key-based), or "
            "LLM_BACKEND=bedrock with LLM_BEDROCK_MODEL_ID "
            "(AWS Bedrock) — see the root README's 'Connect a model' "
            "section.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        client = LLMClient()
    except LLMError as exc:
        print(f"Could not start the model client: {exc}", file=sys.stderr)
        raise SystemExit(2)

    ticket = TicketState(
        ticket_id="tkt-1001",
        customer_id="cust-001",
        subject="Refund for the annual plan",
        body="I was charged for the annual plan twice and I would like a "
        "refund for the duplicate charge, please.",
    )
    supervisor = Supervisor(
        workers=[
            ResearcherWorker(),
            PolicyWorker(),
            ModelSentimentWorker(client),
        ],
        drafter=ModelDrafterWorker(client),
    )
    result = supervisor.run(ticket)

    print(f"run:    {result.run_id}")
    print(f"status: {result.status}")
    print(f"handoffs: {result.handoffs}   tokens used: {result.tokens_used}")
    print()
    print("trace (one line per handoff):")
    print(result.trace_jsonl())
    print()
    if result.sentiment is not None:
        print(
            f"sentiment: {result.sentiment.label} "
            f"(score {result.sentiment.score})"
        )
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
    print()
    print(
        f"model usage: {client.total_prompt_tokens} prompt + "
        f"{client.total_completion_tokens} completion tokens, "
        f"${client.total_cost_usd:.4f}"
    )


if __name__ == "__main__":
    main()
