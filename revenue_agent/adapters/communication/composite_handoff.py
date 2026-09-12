"""Fanning the handoff out to several destinations, with a delivery guarantee.

A message to the prospect that fails can be retried on the next cycle. **A lost handoff is a
deal abandoned without anyone knowing** — the agent has stepped back, and no human was warned.
That asymmetry justifies different handling:

* each destination is attempted independently — a Teams outage must not take down the HubSpot
  task, which is the durable trace;
* if **all** of them fail, we log an error and dump the full brief into the logs. It is a poor
  channel, but it is a channel: nothing may disappear silently.
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
                "HANDOFF NOT DELIVERED — no destination accepted the brief for opportunity %s. "
                "It is reproduced below so it is not lost.",
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
                "Approval notification not delivered for %s — action %s remains pending with "
                "nobody informed.",
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
        """Attempts every destination. Returns True if at least one succeeded."""
        delivered = False
        for destination in self._destinations:
            try:
                call(destination)
                delivered = True
            except Exception:  # noqa: BLE001 - une destination en panne n'en condamne pas une autre
                logger.exception(
                    "Destination %s failed for %s",
                    type(destination).__name__,
                    operation,
                )
        return delivered
