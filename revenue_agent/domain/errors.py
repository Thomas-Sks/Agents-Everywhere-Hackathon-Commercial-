"""Domain and adapter errors.

Principle: an adapter **raises** when it fails, it does not silently return `None`. Graceful
degradation is an application-level decision, taken in the use cases, not an implicit convention
scattered across every integration.
"""

from __future__ import annotations


class RevenueAgentError(Exception):
    """Root of every application-level error."""


class AdapterError(RevenueAgentError):
    """Failure of an external system (CRM, email, voice, enrichment)."""

    def __init__(self, adapter: str, message: str) -> None:
        super().__init__(f"[{adapter}] {message}")
        self.adapter = adapter
        self.message = message


class CrmError(AdapterError):
    pass


class MessagingError(AdapterError):
    pass


class VoiceError(AdapterError):
    pass


class EnrichmentError(AdapterError):
    pass


class OpportunityNotFound(RevenueAgentError):
    def __init__(self, opportunity_id: str) -> None:
        super().__init__(f"Opportunity not found: {opportunity_id}")
        self.opportunity_id = opportunity_id


class ChannelUnavailable(RevenueAgentError):
    """The agent picked a channel whose contact details do not exist for this prospect."""

    def __init__(self, opportunity_id: str, channel: str) -> None:
        super().__init__(
            f"Channel '{channel}' unavailable for opportunity {opportunity_id} "
            "(contact details missing from the CRM)"
        )
        self.opportunity_id = opportunity_id
        self.channel = channel


class AuthorizationRequired(RevenueAgentError):
    """The action exceeds the agent's autonomy limits."""

    def __init__(self, action: str, reason: str) -> None:
        super().__init__(f"Action '{action}' refused: {reason}")
        self.action = action
        self.reason = reason
