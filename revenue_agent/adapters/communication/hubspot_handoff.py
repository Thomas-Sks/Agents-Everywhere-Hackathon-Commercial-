"""Handoff to HubSpot — a task assigned on the deal.

This is as native a destination as it gets: the sales rep finds it in their task queue, right
where they already work, associated with the deal and therefore one click away from the whole
context. Nothing to install, nothing extra to check.

The task is assigned to the **deal owner** whenever HubSpot declares one. A brief that lands in
a shared queue is handled by nobody; addressed to the person responsible for the deal, it is.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from revenue_agent.adapters.http import HttpClient
from revenue_agent.domain.errors import MessagingError
from revenue_agent.domain.models import Opportunity

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"

_PRIORITY_BY_URGENCY = {"haute": "HIGH", "normale": "MEDIUM", "faible": "LOW"}


class HubSpotHandoffAdapter:
    def __init__(self, token: str, default_owner_id: str = "") -> None:
        self._default_owner_id = default_owner_id
        self._http = HttpClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            error_factory=lambda message: MessagingError("hubspot-handoff", message),
        )

    def escalate(
        self, *, opportunity: Opportunity, reason: str, urgency: str, context_brief: str
    ) -> None:
        self._create_task(
            opportunity=opportunity,
            subject=f"[Agent] Reprise nécessaire — {opportunity.company}",
            body=f"Motif de l'escalade : {reason}\n\n{context_brief}",
            priority=_PRIORITY_BY_URGENCY.get(urgency.lower(), "MEDIUM"),
        )

    def notify_pending_approval(
        self, *, opportunity: Opportunity, approval_id: str, channel: str, reason: str, preview: str
    ) -> None:
        self._create_task(
            opportunity=opportunity,
            subject=f"[Agent] Validation requise ({channel}) — {opportunity.company}",
            body=(
                f"L'agent a préparé une action mais ne l'a pas envoyée.\n\n"
                f"Motif : {reason}\n"
                f"Référence : {approval_id}\n\n"
                f"Message retenu :\n{preview}"
            ),
            priority="HIGH",
        )

    def _create_task(
        self, *, opportunity: Opportunity, subject: str, body: str, priority: str
    ) -> None:
        properties = {
            "hs_task_subject": subject,
            "hs_task_body": body,
            "hs_task_status": "NOT_STARTED",
            "hs_task_priority": priority,
            "hs_timestamp": int(datetime.now(UTC).timestamp() * 1000),
        }
        owner_id = opportunity.owner_id or self._default_owner_id
        if owner_id:
            properties["hubspot_owner_id"] = owner_id

        payload = self._http.request(
            "POST", "/crm/v3/objects/tasks", json={"properties": properties}
        )
        self._http.request(
            "PUT",
            f"/crm/v4/objects/tasks/{payload['id']}/associations/default/deals/{opportunity.id}",
        )
        logger.info(
            "Tâche HubSpot %s créée sur l'opportunité %s (propriétaire %s)",
            payload["id"],
            opportunity.id,
            owner_id or "non assigné",
        )
