"""Human approval queue.

When the policy requires sign-off, the action is not executed: it is held along with everything
needed to replay it verbatim once approved. The message drafted by the agent is preserved word
for word — a human must approve what will actually go out, not a summary of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from revenue_agent.domain.policy import ActionKind


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class PendingApproval:
    id: str
    opportunity_id: str
    company: str
    kind: ActionKind
    recipient: str
    rule: str
    reason: str
    requested_at: datetime
    payload: dict[str, str] = field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    reviewer: str = ""
    reviewed_at: datetime | None = None
    review_note: str = ""

    @property
    def is_pending(self) -> bool:
        return self.status is ApprovalStatus.PENDING

    def summary(self) -> str:
        """Readable summary for the human arbitrating — they must be able to decide without
        opening any other tool."""
        preview = self.payload.get("body") or self.payload.get("message") or ""
        subject = self.payload.get("subject")
        lines = [
            f"[{self.id}] {self.kind.value} → {self.recipient}",
            f"  Opportunity : {self.company} ({self.opportunity_id})",
            f"  Reason      : {self.reason}",
        ]
        if subject:
            lines.append(f"  Subject     : {subject}")
        if preview:
            lines.append(f"  Message     : {preview[:300]}")
        return "\n".join(lines)
