"""Évaluation de l'enjeu d'une décision — détermine la profondeur de raisonnement à acheter.

C'est le pendant économique de la thèse produit : si la décision commerciale est le produit,
alors *combien de raisonnement cette décision mérite* est elle-même une décision. Une relance
de routine sur un petit deal ne justifie pas le même modèle qu'une négociation tardive sur un
gros contrat avec des objections non résolues.

Fonction pure : testable, et surtout auditable — on peut expliquer pourquoi tel cycle a coûté
plus cher que tel autre.
"""

from __future__ import annotations

from revenue_agent.domain.models import Opportunity
from revenue_agent.domain.triage import TriggerKind

STRATEGIC_AMOUNT_THRESHOLD = 50_000.0

# Stades tardifs : l'erreur y coûte bien plus cher qu'en découverte, où une maladresse se
# rattrape. Les libellés couvrent les valeurs HubSpot par défaut et nos libellés internes.
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
    """Vrai si la décision justifie le modèle le plus capable."""
    if opportunity.amount is not None and opportunity.amount >= STRATEGIC_AMOUNT_THRESHOLD:
        return True

    if _normalise(opportunity.stage) in LATE_STAGES:
        return True

    if opportunity.unresolved_objections():
        return True

    # Un changement de stade est un moment charnière : c'est là que le deal bascule.
    return trigger_kind is TriggerKind.STAGE_CHANGED


def explain(opportunity: Opportunity, trigger_kind: TriggerKind | None = None) -> str:
    """Motif lisible du routage — journalisé pour que le coût reste explicable."""
    if opportunity.amount is not None and opportunity.amount >= STRATEGIC_AMOUNT_THRESHOLD:
        return f"montant élevé ({opportunity.amount:,.0f})"
    if _normalise(opportunity.stage) in LATE_STAGES:
        return f"stade tardif ({opportunity.stage})"
    if opportunity.unresolved_objections():
        return f"{len(opportunity.unresolved_objections())} objection(s) non résolue(s)"
    if trigger_kind is TriggerKind.STAGE_CHANGED:
        return "changement de stade"
    return "décision de routine"


def _normalise(stage: str) -> str:
    return stage.strip().lower().replace(" ", "").replace("-", "_")
