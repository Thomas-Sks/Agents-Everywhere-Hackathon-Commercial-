"""Adapters de communication en mode simulé.

Sélectionnés par le composition root quand le fournisseur correspondant n'est pas configuré.
Ils implémentent exactement les mêmes ports que les adapters réels : le domaine ne sait pas
qu'il tourne à vide, et le chemin de code exercé en démo est le même qu'en production.

Chaque envoi simulé est tracé avec un marqueur explicite — une démo ne doit jamais laisser
croire qu'un message est parti alors qu'il s'est arrêté dans un log.
"""

from __future__ import annotations

import logging
import uuid

from revenue_agent.domain.models import Opportunity

logger = logging.getLogger(__name__)


class ConsoleEmailAdapter:
    def send(self, *, to: str, subject: str, body: str) -> str:
        message_id = f"simulated-{uuid.uuid4().hex[:12]}"
        logger.info("[SIMULÉ] Email → %s | %s\n%s", to, subject, body)
        return message_id


class ConsoleWhatsAppAdapter:
    def send(self, *, to_phone_number: str, message: str) -> str:
        message_id = f"simulated-{uuid.uuid4().hex[:12]}"
        logger.info("[SIMULÉ] WhatsApp → %s\n%s", to_phone_number, message)
        return message_id


class ConsoleVoiceAdapter:
    def place_call(self, *, to_phone_number: str, opportunity: Opportunity, objective: str) -> str:
        call_id = f"simulated-{uuid.uuid4().hex[:12]}"
        logger.info(
            "[SIMULÉ] Appel → %s (opportunité %s) | objectif : %s",
            to_phone_number,
            opportunity.id,
            objective,
        )
        return call_id


class ConsoleHandoffAdapter:
    """Destination par défaut du passage de relais.

    Le brief est journalisé en entier : c'est la garantie qu'un humain reprenant le dossier
    n'a jamais à redemander le contexte, même sans intégration Slack ou CRM.
    """

    def escalate(
        self, *, opportunity: Opportunity, reason: str, urgency: str, context_brief: str
    ) -> None:
        logger.warning(
            "HANDOFF [%s] — opportunité %s (%s)\nRaison : %s\n\n%s",
            urgency.upper(),
            opportunity.id,
            opportunity.company,
            reason,
            context_brief,
        )
