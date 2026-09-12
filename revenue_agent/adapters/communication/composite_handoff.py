"""Diffusion du handoff vers plusieurs destinations, avec garantie de remise.

Un message au prospect qui échoue peut être réessayé au prochain cycle. **Un handoff perdu est
un deal abandonné sans que personne ne le sache** — l'agent s'est retiré, et aucun humain n'a
été prévenu. L'asymétrie justifie un traitement différent :

* chaque destination est tentée indépendamment — une panne Teams ne doit pas emporter la
  tâche HubSpot, qui est la trace durable ;
* si **toutes** échouent, on journalise en erreur et on déverse le brief intégral dans les
  logs. C'est un mauvais canal, mais c'est un canal : rien ne doit disparaître silencieusement.
"""

from __future__ import annotations

import logging

from revenue_agent.domain.models import Opportunity
from revenue_agent.ports.communication import HandoffPort

logger = logging.getLogger(__name__)


class CompositeHandoffAdapter:
    def __init__(self, destinations: list[HandoffPort], fallback: HandoffPort) -> None:
        self._destinations = destinations
        self._fallback = fallback

    def escalate(
        self, *, opportunity: Opportunity, reason: str, urgency: str, context_brief: str
    ) -> None:
        delivered = self._fan_out(
            "escalate",
            lambda destination: destination.escalate(
                opportunity=opportunity,
                reason=reason,
                urgency=urgency,
                context_brief=context_brief,
            ),
        )
        if not delivered:
            logger.error(
                "HANDOFF NON REMIS — aucune destination n'a accepté le brief de l'opportunité %s. "
                "Il est reproduit ci-dessous pour ne pas être perdu.",
                opportunity.id,
            )
            self._fallback.escalate(
                opportunity=opportunity,
                reason=reason,
                urgency=urgency,
                context_brief=context_brief,
            )

    def notify_pending_approval(
        self, *, opportunity: Opportunity, approval_id: str, channel: str, reason: str, preview: str
    ) -> None:
        delivered = self._fan_out(
            "notify_pending_approval",
            lambda destination: destination.notify_pending_approval(
                opportunity=opportunity,
                approval_id=approval_id,
                channel=channel,
                reason=reason,
                preview=preview,
            ),
        )
        if not delivered:
            logger.error(
                "Notification de validation non remise pour %s — l'action %s reste en attente "
                "sans que personne n'en soit informé.",
                opportunity.id,
                approval_id,
            )
            self._fallback.notify_pending_approval(
                opportunity=opportunity,
                approval_id=approval_id,
                channel=channel,
                reason=reason,
                preview=preview,
            )

    def _fan_out(self, operation: str, call) -> bool:
        """Tente chaque destination. Retourne True si au moins une a réussi."""
        delivered = False
        for destination in self._destinations:
            try:
                call(destination)
                delivered = True
            except Exception:  # noqa: BLE001 - une destination en panne n'en condamne pas une autre
                logger.exception(
                    "Destination %s en échec pour %s",
                    type(destination).__name__,
                    operation,
                )
        return delivered
