"""File d'attente de validation humaine.

Quand la politique exige un accord, l'action n'est pas exécutée : elle est mise en attente avec
tout ce qu'il faut pour la rejouer telle quelle une fois validée. Le message rédigé par l'agent
est conservé mot pour mot — un humain doit valider ce qui partira réellement, pas un résumé.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from revenue_agent.domain.policy import ActionKind


class ApprovalStatus(StrEnum):
    PENDING = "en_attente"
    APPROVED = "approuve"
    REJECTED = "rejete"


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
        """Résumé lisible pour un humain qui arbitre — il doit pouvoir décider sans ouvrir
        d'autre outil."""
        preview = self.payload.get("body") or self.payload.get("message") or ""
        subject = self.payload.get("subject")
        lines = [
            f"[{self.id}] {self.kind.value} → {self.recipient}",
            f"  Opportunité : {self.company} ({self.opportunity_id})",
            f"  Motif       : {self.reason}",
        ]
        if subject:
            lines.append(f"  Objet       : {subject}")
        if preview:
            lines.append(f"  Message     : {preview[:300]}")
        return "\n".join(lines)
