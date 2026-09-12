"""Agent de repli sans LLM — implémente `DecisionAgentPort`.

Sélectionné quand aucune clé OpenRouter n'est configurée. Il ne décide rien : il consigne
l'événement et le dit explicitement. C'est volontaire — un agent de repli qui simulerait des
décisions plausibles serait bien pire qu'un agent qui annonce son incapacité, en démo comme en
production.

Sert aussi de double de test pour exercer les use cases sans appeler de modèle.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from revenue_agent.domain.models import Channel, Interaction, Opportunity
from revenue_agent.ports.crm import CrmPort

logger = logging.getLogger(__name__)


class ScriptedDecisionAgent:
    def __init__(self, crm: CrmPort) -> None:
        self._crm = crm

    def decide(self, *, opportunity: Opportunity, event: str, strategic: bool = False) -> str:
        logger.warning(
            "Aucun LLM configuré — événement consigné sans décision (opportunité %s)",
            opportunity.id,
        )
        self._crm.record_interaction(
            opportunity.id,
            Interaction(
                channel=Channel.SIGNAL,
                summary=f"[sans LLM] Événement reçu : {event}",
                occurred_at=datetime.now(UTC),
            ),
        )
        channels = ", ".join(c.value for c in opportunity.reachable_channels().available) or "aucun"
        return (
            "Aucune décision prise : OPENROUTER_API_KEY n'est pas configurée. "
            f"Événement consigné dans le CRM. Canaux disponibles : {channels}."
        )
