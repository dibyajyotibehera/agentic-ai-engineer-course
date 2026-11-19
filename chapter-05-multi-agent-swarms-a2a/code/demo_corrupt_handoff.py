"""Demo: a corrupted state payload is detected and rejected.

Run from the chapter directory:

    python code/demo_corrupt_handoff.py

Each mutation below takes a valid, sealed ``TaskEnvelope`` and tampers with
it. The receiver validates before acting, so every mutation is rejected with
the name of the exact check that failed — and no corrupted payload ever
reaches a worker. See Exercise 2 for the two mutations left to you
(wrong recipient and stale replay) and for the retry path.
"""

from dataclasses import replace

from swarm import (
    PolicyResult,
    ResearchResult,
    compute_checksum,
    make_envelope,
    validate_envelope,
)


def show(label: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - demo prints every rejection
        print(f"  REJECTED [{type(exc).__name__}]: {exc}")
    else:
        print(f"  ACCEPTED (this should not happen for: {label})")


def main() -> None:
    good_payload = ResearchResult(
        customer_id="cust-001",
        plan="annual",
        renewal_date="2026-03-14",
        account_status="active",
    )
    good = make_envelope(
        task_id="tkt-1001",
        sender="researcher",
        recipient="supervisor",
        payload=good_payload,
    )
    print("valid envelope passes validation:")
    typed = validate_envelope(good, expected_recipient="supervisor")
    print(f"  ACCEPTED: {typed!r}\n")

    print("1. flipped character in a text field (checksum no longer matches):")
    # Delivered with the tampered payload but the ORIGINAL checksum, exactly
    # as a transport corruption would deliver it.
    flipped = replace(good, payload={**good.payload, "plan": "annuak"})
    show("flipped character", lambda: validate_envelope(
        flipped, expected_recipient="supervisor"))

    print("2. priority value outside the allowed set:")
    bad_policy = make_envelope(
        task_id="tkt-1001",
        sender="policy-checker",
        recipient="supervisor",
        payload=PolicyResult(
            applies=True, policy_name="refund-window-30d",
            priority="pretty urgent",  # not one of low/medium/high/urgent
            reason="Refund requested.",
        ),
    )
    show("bad priority", lambda: validate_envelope(
        bad_policy, expected_recipient="supervisor"))

    print("3. missing required field:")
    dropped = {k: v for k, v in good.payload.items() if k != "renewal_date"}
    missing_field = replace(good, payload=dropped,
                            checksum=compute_checksum(dropped))
    show("missing field", lambda: validate_envelope(
        missing_field, expected_recipient="supervisor"))

    print("4. schema version the receiver does not accept:")
    future = make_envelope(
        task_id="tkt-1001",
        sender="researcher",
        recipient="supervisor",
        payload=good_payload,
        schema_version=99,
    )
    show("bad version", lambda: validate_envelope(
        future, expected_recipient="supervisor"))

    print("5. checksum that does not match the payload:")
    wrong_checksum = replace(good, checksum="0" * 64)
    show("wrong checksum", lambda: validate_envelope(
        wrong_checksum, expected_recipient="supervisor"))

    print("6. envelope addressed to a different recipient:")
    show("wrong recipient", lambda: validate_envelope(
        good, expected_recipient="drafter"))


if __name__ == "__main__":
    main()
