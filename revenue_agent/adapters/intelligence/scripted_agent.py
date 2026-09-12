"""Fallback agent with no LLM — implements `DecisionAgentPort`.

Selected when no OpenRouter key is configured. It decides nothing: it records the event and
says so explicitly. That is deliberate — a fallback agent that faked plausible decisions would
be far worse than one announcing its own incapacity, in a demo just as much as in production.

It also doubles as a test stub for exercising the use cases without calling a model.
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
            "No LLM configured — event recorded without a decision (opportunity %s)",
            opportunity.id,
        )
        self._crm.record_interaction(
            opportunity.id,
            Interaction(
                channel=Channel.SIGNAL,
                summary=f"[no LLM] Event received: {event}",
                occurred_at=datetime.now(UTC),
            ),
        )
        channels = ", ".join(c.value for c in opportunity.reachable_channels().available) or "aucun"
        return (
            "No decision taken: OPENROUTER_API_KEY is not configured. "
            f"Event recorded in the CRM. Available channels: {channels}."
        )
