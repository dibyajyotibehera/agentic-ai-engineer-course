"""Demo: consensus voting when two agents disagree.

Run from the chapter directory:

    python code/demo_consensus.py

Three cases, one rule each: a majority vote that resolves a two-to-one
split while preserving the dissent; a tied vote that escalates instead of
picking a winner arbitrarily; and a factual disagreement that is resolved
by re-fetching the source of truth, never by ballot. See Exercise 3.
"""

from swarm import (
    Vote,
    resolve_fact_disagreement,
    resolve_majority,
    resolve_weighted,
)


def report(label: str, outcome) -> None:
    print(label)
    print(f"  rule:     {outcome.rule}")
    print(f"  decision: {outcome.decision!r}")
    print(f"  reason:   {outcome.reason}")
    if outcome.dissent:
        print("  dissent preserved:")
        for vote in outcome.dissent:
            print(f"    - {vote.voter}: {vote.verdict!r} "
                  f"(confidence {vote.confidence})")
    print()


def main() -> None:
    # Case 1: two agents disagree about priority; a third voter breaks it.
    votes = [
        Vote(voter="researcher", verdict="high", confidence=0.8,
             evidence=["account is past-due"]),
        Vote(voter="policy-checker", verdict="low", confidence=0.6,
             evidence=["no policy matches"]),
        Vote(voter="sentiment-scorer", verdict="high", confidence=0.7,
             evidence=["customer language is frustrated"]),
    ]
    report("Case 1 — majority vote (2 high, 1 low):",
           resolve_majority(votes))

    # Case 2: the third voter changes its mind and the vote ties.
    tied = [
        Vote(voter="researcher", verdict="high", confidence=0.8,
             evidence=["account is past-due"]),
        Vote(voter="policy-checker", verdict="low", confidence=0.6,
             evidence=["no policy matches"]),
    ]
    report("Case 2 — tied vote (1 high, 1 low):", resolve_majority(tied))

    # Case 3: weighted vote — the policy checker has the best track record
    # on the held-out evaluation set, so its vote counts double.
    weights = {"researcher": 1.0, "policy-checker": 2.0,
               "sentiment-scorer": 1.0}
    report("Case 3 — weighted vote (policy-checker weight 2.0):",
           resolve_weighted(votes, weights))

    # Case 4: a factual disagreement — two renewal dates. No vote is taken;
    # the source of truth is re-fetched and decides.
    def refetch() -> dict:
        return {"renewal_date": "2026-03-14"}  # the account database

    report(
        "Case 4 — factual disagreement over renewal_date "
        "(2026-03-14 vs 2027-03-14):",
        resolve_fact_disagreement(
            "renewal_date",
            {"researcher": "2026-03-14", "policy-checker": "2027-03-14"},
            refetch,
        ),
    )


if __name__ == "__main__":
    main()
