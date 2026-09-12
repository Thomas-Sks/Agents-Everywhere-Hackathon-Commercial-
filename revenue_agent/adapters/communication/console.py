"""Communication adapters in simulated mode.

Selected by the composition root when the corresponding vendor is not configured. They
implement exactly the same ports as the real adapters: the domain does not know it is running
dry, and the code path exercised in a demo is the same one that runs in production.

Every simulated send is logged with an explicit marker — a demo must never leave anyone
believing a message went out when it actually stopped inside a log line.
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
    """Default destination for a handoff.

    The brief is logged in full: that is what guarantees a human picking the deal back up never
    has to ask for the context again, even without a Slack or CRM integration.
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

    def notify_pending_approval(
        self, *, opportunity, approval_id: str, channel: str, reason: str, preview: str
    ) -> None:
        logger.warning(
            "VALIDATION REQUISE [%s] — opportunité %s (%s)\nMotif : %s\n\n%s",
            approval_id,
            opportunity.id,
            opportunity.company,
            reason,
            preview,
        )
