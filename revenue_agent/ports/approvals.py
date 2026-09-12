"""Port de la file de validation humaine."""

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
        """Enregistre l'arbitrage. Retourne None si l'identifiant est inconnu."""
        ...

    def count_recent_outbound(self, opportunity_id: str, hours: int = 24) -> int:
        """Actions sortantes réellement émises sur la période — alimente la règle de cadence."""
        ...

    def record_sent(self, opportunity_id: str) -> None: ...
