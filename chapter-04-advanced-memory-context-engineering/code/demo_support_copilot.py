"""Demo: a support copilot with three-tier memory.

Runs end to end on the standard library only. It wires together the
three-tier store (``memory_store.py``) and the token budget
(``token_budget.py``) around a small fictional scenario:

* Tenant ``acme`` has a refund policy that changes from March to June.
* Tenant ``globex`` has a different plan and must never see Acme's data.
* A low-authority chat message tries to override the June policy and fails.

Run it with:  python demo_support_copilot.py
Run the checks with:  python -m py_compile memory_store.py token_budget.py demo_support_copilot.py
Run the live paths (real model compaction and tool-result summary)
with:  LLM_BACKEND=opencheap-connector python demo_support_copilot.py --live
"""

from __future__ import annotations

import sys

from compaction import TranscriptTurn, compact_with_llm, prune_tool_result_with_llm
from llm_client import LLMClient
from memory_store import MemoryStatus, MemoryStore, Tier
from token_budget import CostModel, TokenBudget


def main() -> None:
    store = MemoryStore(":memory:")

    # --- Semantic tier: tenant facts -------------------------------------
    for tenant, plan in (("acme", "Acme is on the Business plan with priority support."),
                         ("globex", "Globex is on the Starter plan with standard support.")):
        memory, verdict = store.write_fact(
            tenant, plan, source="crm-record", authority="high", importance=0.9)
        print(f"[{tenant}] fact {verdict.reason}: {plan}")

    # --- The March policy, then the June change ---------------------------
    march, _ = store.write_fact(
        "acme", "Refunds within 30 days need no approval.",
        source="policy-handbook-v3", authority="high", importance=0.9)
    june, _ = store.write_fact(
        "acme", "Refunds within 14 days need manager approval.",
        source="policy-handbook-v4", authority="high", importance=0.9)
    march_now = [m for m in store.list_memories("acme", Tier.SEMANTIC) if m.id == march.id][0]
    print(f"\nMarch policy (id={march.id}) status now: {march_now.status.value} "
          f"-> superseded by memory {march_now.superseded_by}")
    print(f"June policy (id={june.id}) status: {june.status.value}, version {june.version}")

    # --- A low-authority challenger tries to override it ------------------
    rogue, verdict = store.write_fact(
        "acme", "Refunds are fine up to 45 days, no approval needed.",
        source="agent-chat-message", authority="low", importance=0.4)
    print(f"Chat claim verdict: {verdict.reason}; stored status: "
          f"{rogue.status.value if rogue else 'not stored'}")
    print("Current Acme policy:",
          [m.text for m in store.current_facts("acme") if "Refund" in m.text])

    # --- Episodic tier: past tickets --------------------------------------
    store.write_episode(
        "acme", "Ticket 101: refunded a late delivery within 14 days after manager approval.",
        source="ticket-101", importance=0.8, expected_reuse=0.8)
    store.write_episode(
        "acme", "Ticket 102: customer preferred email follow-ups over chat.",
        source="ticket-102", importance=0.6, expected_reuse=0.7)

    # --- The write gate refuses a secret ----------------------------------
    _, verdict = store.write_episode(
        "acme", "Customer card ends in 4242 and expires 09/28.",
        source="ticket-103", importance=0.9, expected_reuse=0.9)
    print(f"\nGate verdict on payment detail: {verdict.reason} ({verdict.detail})")

    # --- Retrieval: tenant-scoped -----------------------------------------
    print("\nAcme retrieval for 'refund policy':")
    for memory, score in store.retrieve("acme", "refund policy", tier=Tier.SEMANTIC, top_k=3):
        print(f"  [{score:.2f}] {memory.text} (status={memory.status.value})")
    print("Globex retrieval for 'refund policy':")
    results = store.retrieve("globex", "refund policy", tier=Tier.SEMANTIC, top_k=3)
    for memory, score in results:
        print(f"  [{score:.2f}] {memory.text} (tenant={memory.tenant_id})")
    assert all(m.tenant_id == "globex" for m, _ in results), "cross-tenant leak!"
    print("  -> every returned memory belongs to Globex; none of Acme's memories leaked")

    # --- Token budget for one task ----------------------------------------
    budget = TokenBudget(total_tokens=4000, task_type="returning_customer")
    print(f"\nBudget for a returning-customer task: {budget.allocation}")
    retrieved = [("Refund policy summary", 500), ("Ticket 101 summary", 400),
                 ("Ticket 102 summary", 300), ("Old outage note", 900)]
    fitted = budget.compress(retrieved, budget.allocation.episodic)
    print(f"Episodic retrieval compressed from "
          f"{sum(t for _, t in retrieved)} to {sum(t for _, t in fitted)} tokens")

    # --- Cost per task ------------------------------------------------------
    # The model price is a parameter: $5 per 1M input tokens is an EXAMPLE
    # value for the demo only. Pass your own model's real price.
    model = CostModel(llm_price_per_million_input_tokens_usd=5.0)
    cost = model.task_cost(
        working_tokens=1600, episodic_tokens=1000, semantic_tokens=600,
        embedded_tokens=1000,
        pinecone_read_units=model.pinecone_read_units(namespace_gb=1.0, queries=1),
    )
    print(f"\nCost per task at an example $5/1M-token model price: "
          f"${cost.total_usd:.6f} (LLM ${cost.llm_usd:.6f}, "
          f"embedding ${cost.embedding_usd:.6f}, "
          f"Pinecone {cost.pinecone_read_units:.2f} read units)")
    print(f"Embedding 10,000 episode summaries of 1,000 tokens each costs "
          f"${model.embedding_cost(10_000 * 1_000):.2f} per day")
    print(f"Semantic cache at 65% hit rate leaves "
          f"{model.semantic_cache_calls(10_000, 0.65):,.0f} of 10,000 daily calls")

    if "--live" in sys.argv:
        run_live(store)

    store.close()


def run_live(store: MemoryStore) -> None:
    """Live section: real model compaction and tool-result summary.

    Needs a configured model backend (``LLM_API_KEY`` or
    ``LLM_BACKEND=opencheap-connector``). The transcript is the
    working tier of the Acme session above, compacted by a real
    model call; the billing record is summarised by a real model
    call. Prints the model's token usage and cost for the run.
    """
    client = LLMClient()
    transcript = [
        TranscriptTurn("Refunds within 30 days need no approval.",
                       role="system", is_instruction=True)
        for _ in range(6)
    ] + [
        TranscriptTurn("Refund policy is 14 days with manager approval (June).",
                       role="assistant", load_bearing=True),
        TranscriptTurn("Customer asks about a refund on day 20.", role="user"),
    ]
    facts = [
        "Refund policy is 14 days with manager approval (June, supersedes March).",
        "Customer's plan is business, renewal 2027-03-01.",
        "Tried re-issuing the invoice; it did not resolve the billing error.",
    ]
    print("\n=== Live compaction (real model) ===")
    state = compact_with_llm(client, transcript, facts)
    print(f"{state.tokens_before} -> {state.tokens_after} tokens; "
          f"complete (all load-bearing facts preserved): {state.complete}")
    print(state.summary)

    print("\n=== Live tool-result pruning (real model) ===")
    billing_record = "\n".join(
        [
            "plan: business",
            "renewal date: 2027-03-01",
            "seats: 42",
            "past invoices: " + "INV-1001 $480, " * 40,
        ]
    )
    pruned = prune_tool_result_with_llm(client, billing_record, strategy="summarise")
    print(f"summarise: {pruned.tokens_before} -> {pruned.tokens_after} tokens")
    print(f"summary: {pruned.text}")

    print(f"\nModel usage for the live section: {client.total_prompt_tokens} prompt + "
          f"{client.total_completion_tokens} completion tokens, "
          f"cost ${client.total_cost_usd:.6f}")


if __name__ == "__main__":
    main()
