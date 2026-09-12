"""Handoff to Microsoft Teams — where the sales rep actually looks.

A HubSpot task is durable but passive: it waits for someone to open the CRM. The Teams ping is
immediate but ephemeral. The two are complementary, hence the composite that combines them.

**Transport: a Power Automate workflow webhook.** The legacy Office 365 incoming connectors are
being retired; the supported path is a "When a Teams webhook request is received" workflow,
which provides a URL accepting an adaptive card. No app registration and no admin consent: a
single environment variable, consistent with the rest of the project's integrations.

The message carries an **arbitration link**: receiving the information without being able to act
on it would only create guilt.
"""

from __future__ import annotations

import logging

from revenue_agent.adapters.http import HttpClient
from revenue_agent.domain.errors import MessagingError
from revenue_agent.domain.models import Opportunity

logger = logging.getLogger(__name__)

_MAX_PREVIEW = 1200


class TeamsHandoffAdapter:
    def __init__(self, webhook_url: str, approval_url_builder) -> None:
        self._webhook_url = webhook_url
        self._approval_url = approval_url_builder
        self._http = HttpClient(
            base_url="",
            headers={"Content-Type": "application/json"},
            error_factory=lambda message: MessagingError("teams", message),
        )

    def escalate(
        self, *, opportunity: Opportunity, reason: str, urgency: str, context_brief: str
    ) -> None:
        self._post(
            title=f"Handover needed — {opportunity.company}",
            subtitle=f"Urgency {urgency} · stage {opportunity.stage}",
            facts=[("Reason", reason)],
            body=context_brief,
            action_label="Open the arbitration queue",
            action_url=self._approval_url(""),
        )

    def notify_pending_approval(
        self, *, opportunity: Opportunity, approval_id: str, channel: str, reason: str, preview: str
    ) -> None:
        self._post(
            title=f"Approval required — {opportunity.company}",
            subtitle=f"{channel} action held · reference {approval_id}",
            facts=[("Reason", reason)],
            body=preview,
            action_label="Review and arbitrate",
            action_url=self._approval_url(approval_id),
        )

    def _post(
        self,
        *,
        title: str,
        subtitle: str,
        facts: list[tuple[str, str]],
        body: str,
        action_label: str,
        action_url: str,
    ) -> None:
        card_body: list[dict] = [
            {
                "type": "TextBlock",
                "text": title,
                "weight": "Bolder",
                "size": "Medium",
                "wrap": True,
            },
            {"type": "TextBlock", "text": subtitle, "isSubtle": True, "wrap": True},
            {
                "type": "FactSet",
                "facts": [{"title": label, "value": value} for label, value in facts],
            },
            {"type": "TextBlock", "text": _truncate(body), "wrap": True},
        ]

        card: dict = {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": card_body,
        }
        if action_url:
            card["actions"] = [{"type": "Action.OpenUrl", "title": action_label, "url": action_url}]

        self._http.request(
            "POST",
            self._webhook_url,
            json={
                "type": "message",
                "attachments": [
                    {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
                ],
            },
        )
        logger.info("Teams notification sent: %s", title)


def _truncate(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= _MAX_PREVIEW:
        return cleaned
    return cleaned[: _MAX_PREVIEW - 1] + "…"
