"""Use case : arbitrage humain sur une action mise en attente.

C'est le point de contrôle réel de l'opérateur. Tant qu'une action est en attente, rien n'est
parti chez le prospect — approuver l'exécute telle qu'elle a été rédigée, rejeter la classe
en laissant une trace dans le CRM.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from revenue_agent.application.action_registry import ActionRegistry
from revenue_agent.domain.approvals import ApprovalStatus, PendingApproval
from revenue_agent.domain.models import Channel, Interaction
from revenue_agent.ports.approvals import ApprovalPort
from revenue_agent.ports.crm import CrmPort

logger = logging.getLogger(__name__)


class ReviewApproval:
    def __init__(
        self, *, approvals: ApprovalPort, actions: ActionRegistry, crm: CrmPort
    ) -> None:
        self._approvals = approvals
        self._actions = actions
        self._crm = crm

    def list_pending(self) -> list[PendingApproval]:
        return self._approvals.list_pending()

    def approve(self, approval_id: str, reviewer: str, note: str = "") -> str:
        approval = self._approvals.get(approval_id)
        if approval is None:
            return f"Demande {approval_id} introuvable."
        if not approval.is_pending:
            return f"Demande {approval_id} déjà arbitrée ({approval.status.value})."

        self._approvals.mark(approval_id, ApprovalStatus.APPROVED, reviewer, note)
        logger.info("Demande %s approuvée par %s", approval_id, reviewer)

        result = self._actions.execute_approved(approval)
        self._crm.record_interaction(
            approval.opportunity_id,
            Interaction(
                channel=Channel.DECISION,
                summary=f"[VALIDATION] {reviewer} a approuvé : {approval.reason}",
                occurred_at=datetime.now(UTC),
            ),
        )
        return result

    def reject(self, approval_id: str, reviewer: str, note: str = "") -> str:
        approval = self._approvals.get(approval_id)
        if approval is None:
            return f"Demande {approval_id} introuvable."
        if not approval.is_pending:
            return f"Demande {approval_id} déjà arbitrée ({approval.status.value})."

        self._approvals.mark(approval_id, ApprovalStatus.REJECTED, reviewer, note)
        logger.info("Demande %s rejetée par %s", approval_id, reviewer)

        # La trace du refus compte autant que celle d'un envoi : c'est elle qui permettra plus
        # tard de comprendre pourquoi l'agent n'a pas agi.
        self._crm.record_interaction(
            approval.opportunity_id,
            Interaction(
                channel=Channel.DECISION,
                summary=f"[VALIDATION] {reviewer} a rejeté l'action — {note or 'sans motif'}",
                occurred_at=datetime.now(UTC),
            ),
        )
        return f"Demande {approval_id} rejetée. Rien n'a été envoyé."
