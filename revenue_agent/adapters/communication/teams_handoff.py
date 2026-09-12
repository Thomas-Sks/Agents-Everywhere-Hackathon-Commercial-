"""Handoff vers Microsoft Teams — là où le commercial regarde vraiment.

Une tâche HubSpot est durable mais passive : elle attend qu'on ouvre le CRM. Le ping Teams est
immédiat mais éphémère. Les deux sont complémentaires, d'où le composite qui les combine.

**Transport : webhook de workflow Power Automate.** Les connecteurs entrants historiques
d'Office 365 sont en fin de vie ; la voie supportée est un workflow « When a Teams webhook
request is received », qui fournit une URL acceptant une carte adaptative. Aucun
enregistrement d'application ni consentement administrateur : une seule variable
d'environnement, cohérent avec le reste des intégrations du projet.

Le message porte un **lien d'arbitrage** : recevoir l'information sans pouvoir agir ne servirait
qu'à créer de la culpabilité.
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
            title=f"Reprise nécessaire — {opportunity.company}",
            subtitle=f"Urgence {urgency} · stade {opportunity.stage}",
            facts=[("Motif", reason)],
            body=context_brief,
            action_label="Ouvrir la file d'arbitrage",
            action_url=self._approval_url(""),
        )

    def notify_pending_approval(
        self, *, opportunity: Opportunity, approval_id: str, channel: str, reason: str, preview: str
    ) -> None:
        self._post(
            title=f"Validation requise — {opportunity.company}",
            subtitle=f"Action {channel} retenue · référence {approval_id}",
            facts=[("Motif", reason)],
            body=preview,
            action_label="Relire et arbitrer",
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
        logger.info("Notification Teams envoyée : %s", title)


def _truncate(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= _MAX_PREVIEW:
        return cleaned
    return cleaned[: _MAX_PREVIEW - 1] + "…"
