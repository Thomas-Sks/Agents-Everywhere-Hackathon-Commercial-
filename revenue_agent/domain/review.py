"""Review of an outbound message — a sales director's eye before it goes out.

Lexical detection catches what names itself ("discount", "-20%"). It does not see what merely
phrases itself: "I'll match their price", "we'll find common ground on the budget", "I guarantee
you a return on investment within six months". Those sentences commit the company just as much
as an announced discount, and no dictionary will ever cover them all.

Hence this semantic review. It does not replace the deterministic rules: it adds to them. A
model's judgement can be wrong or unavailable; a lexical rule cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReviewCategory(StrEnum):
    """Grounds for escalation, as a sales director would phrase them."""

    NONE = "none"
    PRICE_COMMITMENT = "price_commitment"
    CONTRACTUAL_COMMITMENT = "contractual_commitment"
    UNBACKED_PROMISE = "unbacked_promise"
    PREMATURE_CONCESSION = "premature_concession"
    EXCESSIVE_PRESSURE = "excessive_pressure"
    CLIENT_REFERENCE = "client_reference"
    STAGE_MISMATCH = "stage_mismatch"
    LEGAL_EXPOSURE = "legal_exposure"


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """The review's verdict.

    `quote` carries the exact sentence that is problematic: a sales director does not say "this
    message bothers me", they point at the line. It is also what makes the approval queue
    actionable in a matter of seconds.
    """

    requires_human: bool
    category: ReviewCategory = ReviewCategory.NONE
    rationale: str = ""
    quote: str = ""
    source: str = ""

    @classmethod
    def clear(cls, source: str = "") -> ReviewFinding:
        return cls(requires_human=False, source=source)

    @classmethod
    def escalate(
        cls, category: ReviewCategory, rationale: str, quote: str = "", source: str = ""
    ) -> ReviewFinding:
        return cls(
            requires_human=True,
            category=category,
            rationale=rationale,
            quote=quote,
            source=source,
        )

    def describe(self) -> str:
        parts = [self.rationale or self.category.value]
        if self.quote:
            parts.append(f'Passage at issue: "{self.quote.strip()}"')
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class MessageUnderReview:
    """What we submit to the reviewer: the message, and the context needed to judge it.

    Without the context, the same sentence is either harmless or serious: "we can work something
    out on price" at the end of a negotiation with an engaged decision maker is not the same
    thing as on first contact.
    """

    channel: str
    company: str
    stage: str
    content: str
    amount: float | None = None
    open_objections: tuple[str, ...] = ()
    interactions_count: int = 0
