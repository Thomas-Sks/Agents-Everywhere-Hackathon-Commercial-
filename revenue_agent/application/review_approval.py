"""Use case: human arbitration on an action that has been held.

This is the operator's real control point. As long as an action is pending, nothing has reached
the prospect — approving executes it exactly as it was drafted, rejecting files it away while
leaving a trace in the CRM.
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
            return f"Request {approval_id} not found."
        if not approval.is_pending:
            return f"Request {approval_id} already arbitrated ({approval.status.value})."

        self._approvals.mark(approval_id, ApprovalStatus.APPROVED, reviewer, note)
        logger.info("Request %s approved by %s", approval_id, reviewer)

        result = self._actions.execute_approved(approval)
        self._crm.record_interaction(
            approval.opportunity_id,
            Interaction(
                channel=Channel.DECISION,
                summary=f"[APPROVAL] {reviewer} approved: {approval.reason}",
                occurred_at=datetime.now(UTC),
            ),
        )
        return result

    def reject(self, approval_id: str, reviewer: str, note: str = "") -> str:
        approval = self._approvals.get(approval_id)
        if approval is None:
            return f"Request {approval_id} not found."
        if not approval.is_pending:
            return f"Request {approval_id} already arbitrated ({approval.status.value})."

        self._approvals.mark(approval_id, ApprovalStatus.REJECTED, reviewer, note)
        logger.info("Request %s rejected by %s", approval_id, reviewer)

        # The record of a refusal matters as much as the record of a send: it is what will
        # later explain why the agent did not act.
        self._crm.record_interaction(
            approval.opportunity_id,
            Interaction(
                channel=Channel.DECISION,
                summary=f"[APPROVAL] {reviewer} rejected the action — {note or 'no reason given'}",
                occurred_at=datetime.now(UTC),
            ),
        )
        return f"Request {approval_id} rejected. Nothing was sent."
