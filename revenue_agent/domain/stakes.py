"""Assessing the stakes of a decision — determines how much reasoning to buy.

This is the economic counterpart of the product thesis: if the sales decision is the product,
then *how much reasoning that decision deserves* is itself a decision. A routine follow-up on a
small deal does not warrant the same model as a late-stage negotiation on a large contract with
unresolved objections.

A pure function: testable, and above all auditable — we can explain why one cycle cost more
than another.
"""

from __future__ import annotations

from revenue_agent.domain.models import Opportunity
from revenue_agent.domain.triage import TriggerKind

STRATEGIC_AMOUNT_THRESHOLD = 50_000.0

# Late stages: a mistake costs far more here than in discovery, where a clumsy move can be
# recovered from. The labels cover HubSpot's default values as well as our internal ones.
LATE_STAGES = frozenset(
    {
        "negotiation",
        "negociation",
        "contractsent",
        "contract_sent",
        "proposal",
        "proposition",
        "decisionmakerboughtin",
        "closedwon",
    }
)


def requires_strategic_reasoning(
    opportunity: Opportunity, trigger_kind: TriggerKind | None = None
) -> bool:
    """True if the decision warrants the most capable model."""
    if opportunity.amount is not None and opportunity.amount >= STRATEGIC_AMOUNT_THRESHOLD:
        return True

    if _normalise(opportunity.stage) in LATE_STAGES:
        return True

    if opportunity.unresolved_objections():
        return True

    # A stage change is a pivotal moment: that is where the deal tips one way or the other.
    return trigger_kind is TriggerKind.STAGE_CHANGED


def explain(opportunity: Opportunity, trigger_kind: TriggerKind | None = None) -> str:
    """Human-readable routing rationale — logged so that the cost stays explainable."""
    if opportunity.amount is not None and opportunity.amount >= STRATEGIC_AMOUNT_THRESHOLD:
        return f"high amount ({opportunity.amount:,.0f})"
    if _normalise(opportunity.stage) in LATE_STAGES:
        return f"late stage ({opportunity.stage})"
    if opportunity.unresolved_objections():
        return f"{len(opportunity.unresolved_objections())} unresolved objection(s)"
    if trigger_kind is TriggerKind.STAGE_CHANGED:
        return "stage change"
    return "routine decision"


def _normalise(stage: str) -> str:
    return stage.strip().lower().replace(" ", "").replace("-", "_")
