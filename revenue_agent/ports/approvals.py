"""Port for the human approval queue."""

from __future__ import annotations

from typing import Protocol

from revenue_agent.domain.approvals import ApprovalStatus, PendingApproval


class ApprovalPort(Protocol):
    def submit(self, approval: PendingApproval) -> PendingApproval: ...

    def get(self, approval_id: str) -> PendingApproval | None: ...

    def list_pending(self) -> list[PendingApproval]: ...

    def mark(
        self, approval_id: str, status: ApprovalStatus, reviewer: str, note: str = ""
    ) -> PendingApproval | None:
        """Records the arbitration. Returns None if the identifier is unknown."""
        ...

    def count_recent_outbound(self, opportunity_id: str, hours: int = 24) -> int:
        """Outbound actions actually sent over the period — feeds the rate-limit rule."""
        ...

    def record_sent(self, opportunity_id: str) -> None: ...
