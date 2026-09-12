"""Handoff over WhatsApp — reaching the colleague on the channel they actually read.

A HubSpot task is durable but passive: it waits for someone to open the CRM. A Teams card lands
where the team works. WhatsApp lands in a pocket. For an agent that holds a message back and
needs an answer within the hour, that last one is often the only channel that gets a reply.

Two design points deserve stating:

**It reuses the connection already in place.** The same Meta Cloud API credentials that message
prospects also message colleagues — only the recipient changes. No new integration, no new
credential.

**It deliberately bypasses the outbound policy.** `ActionRegistry` runs every prospect-facing
message through `domain/policy.py`: allow-list, cadence cap, price checks. None of that applies
here, and applying it would be a bug — a rate limit meant to stop us hounding a prospect must
never stop us warning a colleague that an action is waiting. The recipients are configured by
the operator, not chosen by the model, which is what makes the bypass safe.
"""

from __future__ import annotations

import logging

from revenue_agent.domain.models import Opportunity
from revenue_agent.ports.communication import WhatsAppPort

logger = logging.getLogger(__name__)

_MAX_PREVIEW = 600


class WhatsAppHandoffAdapter:
    def __init__(
        self,
        whatsapp: WhatsAppPort,
        recipients: tuple[str, ...],
        approval_url_builder,
    ) -> None:
        self._whatsapp = whatsapp
        self._recipients = recipients
        self._approval_url = approval_url_builder

    def escalate(
        self, *, opportunity: Opportunity, reason: str, urgency: str, context_brief: str
    ) -> None:
        self._broadcast(
            f"🤝 *Reprise nécessaire — {opportunity.company}*\n"
            f"Urgence {urgency} · stade {opportunity.stage}\n\n"
            f"*Motif* : {reason}\n\n"
            f"{_truncate(context_brief)}"
            + _link_line(self._approval_url(""))
        )

    def notify_pending_approval(
        self, *, opportunity: Opportunity, approval_id: str, channel: str, reason: str, preview: str
    ) -> None:
        self._broadcast(
            f"⏸️ *Validation requise — {opportunity.company}*\n"
            f"Action {channel} retenue · réf. {approval_id}\n\n"
            f"*Motif* : {reason}\n\n"
            f"_Message retenu :_\n{_truncate(preview)}"
            + _link_line(self._approval_url(approval_id))
        )

    def _broadcast(self, message: str) -> None:
        """Sends to every configured colleague.

        A failure on one recipient must not silence the others, so each send is independent;
        the last error is re-raised so the composite can tell whether the notification was
        delivered at all.
        """
        last_error: Exception | None = None
        delivered = 0

        for recipient in self._recipients:
            try:
                self._whatsapp.send(to_phone_number=recipient, message=message)
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - one unreachable colleague is not a failure
                logger.warning("WhatsApp handoff to %s failed: %s", recipient, exc)
                last_error = exc

        if delivered == 0 and last_error is not None:
            raise last_error
        logger.info("WhatsApp handoff delivered to %s recipient(s)", delivered)


def _truncate(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= _MAX_PREVIEW:
        return cleaned
    return cleaned[: _MAX_PREVIEW - 1] + "…"


def _link_line(url: str) -> str:
    return f"\n\n👉 {url}" if url else ""
