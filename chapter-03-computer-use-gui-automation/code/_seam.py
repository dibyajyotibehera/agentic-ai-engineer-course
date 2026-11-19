"""Shared seam logic for Exercise 5 (05_hybrid_seam.py).

Kept separate so the failure policy, the mark allow-list and the
verification verdict are importable and testable without a browser
(`python 05_hybrid_seam.py --self-test`).

Nothing in this module touches a browser or a model. It is pure policy:
what the actor hands over, what may be clicked, when to retry, and when
to stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MarkedElement:
    """One labelled element from the Set-of-Mark overlay."""
    number: int
    role: str
    name: str
    centre: tuple[float, float]


@dataclass
class Handoff:
    """What the deterministic actor passes to the delegated agent."""
    url: str
    marks: list[MarkedElement]
    screenshot_path: Path

    def describe(self) -> str:
        labels = ", ".join(f"{m.number}={m.name!r}" for m in self.marks)
        return (f"url={self.url} marks=[{labels}] "
                f"screenshot={self.screenshot_path}")


@dataclass
class HandoffRecord:
    """The audit trail for one delegated step."""
    step: str
    attempts: list[dict] = field(default_factory=list)

    def attempt(self, verified: bool, evidence: str) -> None:
        self.attempts.append({"verified": verified, "evidence": evidence})

    def summary(self) -> str:
        return (f"{self.step}: {len(self.attempts)} attempt(s), "
                f"verified={any(a['verified'] for a in self.attempts)}")


@dataclass(frozen=True)
class DelegationPolicy:
    """Step cap and retry policy for a delegated step."""
    max_steps: int = 10
    max_retries: int = 1

    def should_retry(self, record: HandoffRecord) -> bool:
        """Retry only while attempts remain and the step is unverified."""
        failed = [a for a in record.attempts if not a["verified"]]
        return len(failed) <= self.max_retries and len(record.attempts) < self.max_steps

    def outcome(self, record: HandoffRecord) -> str:
        evidence = ", ".join(a["evidence"] for a in record.attempts)
        return (f"STOP — {record.step} still unverified after "
                f"{len(record.attempts)} attempt(s); evidence attached "
                f"({evidence})")


def choose_mark(target: str, marks: list[MarkedElement]) -> int | None:
    """Select a mark number for a target, or None if nothing matches.

    The allow-list rule lives here: the agent may only ever click a marked
    element. A target with no matching mark returns None, and the caller
    must treat that as a delegation failure, never as permission to guess
    a coordinate.
    """
    for mark in marks:
        if target.lower() in mark.name.lower():
            return mark.number
    return None


def verify_dismissed(dialog_present: bool) -> bool:
    """The differ's verdict, reduced to one boolean: is the dialog gone?

    In the full run the caller derives `dialog_present` from the screenshot
    diff (Exercise 3); here the verdict itself is the unit under test.
    """
    return not dialog_present
