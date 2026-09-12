"""Communication ports — the means of acting on the outside world.

The decision engine picks a channel; it has no idea that email goes out through Resend, that
WhatsApp goes through Meta or that the call is placed by Retell. Replacing a vendor means
writing an adapter, not touching the domain.
"""

from __future__ import annotations

from typing import Protocol

from revenue_agent.domain.models import Opportunity


class EmailPort(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> str:
        """Returns a message identifier. Raises `MessagingError` on failure."""
        ...


class WhatsAppPort(Protocol):
    def send(self, *, to_phone_number: str, message: str) -> str: ...


class VoicePort(Protocol):
    def place_call(
        self, *, to_phone_number: str, opportunity: Opportunity, objective: str
    ) -> str:
        """Triggers an outbound call and returns its identifier.

        The implementation is responsible for passing the opportunity's context to the voice
        platform, in whatever format that platform imposes.
        """
        ...


class HandoffPort(Protocol):
    """Reaching a human about an opportunity.

    Deliberately a separate port from the prospect-facing channels: here the recipient is a
    colleague, not a customer, and the requirement is not the same. A message to a prospect that
    fails can be retried later; **a lost handoff is a deal abandoned without anyone knowing**.
    Every implementation must therefore guarantee delivery, or fail loudly.

    Two distinct moments, both aimed at a human:
    """

    def escalate(
        self, *, opportunity: Opportunity, reason: str, urgency: str, context_brief: str
    ) -> None:
        """Full handoff: the agent steps back, a sales rep takes over the deal."""
        ...

    def notify_pending_approval(
        self, *, opportunity: Opportunity, approval_id: str, channel: str, reason: str, preview: str
    ) -> None:
        """An action is awaiting arbitration.

        Without this notification, the approval queue would only ever be consulted by someone
        who thought to consult it — and a held action would stay pending indefinitely.
        """
        ...
