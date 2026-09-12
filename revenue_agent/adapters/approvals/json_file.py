"""File de validation sur fichier JSON — implémente `ApprovalPort`.

Conserve aussi l'horodatage des envois réellement émis, qui alimente la règle de cadence
(« pas plus de N messages par prospect sur 24 h »). Cette information ne peut pas venir du CRM
seul : un message mis en attente de validation ne doit pas compter comme envoyé.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from revenue_agent.adapters.storage.json_document import JsonDocument
from revenue_agent.domain.approvals import ApprovalStatus, PendingApproval
from revenue_agent.domain.policy import ActionKind

APPROVALS_KEY = "approvals"
SENT_KEY = "sent_log"


class JsonFileApprovalAdapter:
    def __init__(self, document: JsonDocument) -> None:
        self._document = document

    def submit(self, approval: PendingApproval) -> PendingApproval:
        stored = (
            approval
            if approval.id
            else PendingApproval(**{**_as_dict(approval), "id": _new_id()})
        )
        with self._document.update() as data:
            data.setdefault(APPROVALS_KEY, {})[stored.id] = _serialise(stored)
        return stored

    def get(self, approval_id: str) -> PendingApproval | None:
        raw = self._document.read().get(APPROVALS_KEY, {}).get(approval_id)
        return _deserialise(approval_id, raw) if raw else None

    def list_pending(self) -> list[PendingApproval]:
        raw_approvals = self._document.read().get(APPROVALS_KEY, {})
        pending = [
            _deserialise(approval_id, raw)
            for approval_id, raw in raw_approvals.items()
            if raw.get("status") == ApprovalStatus.PENDING.value
        ]
        return sorted(pending, key=lambda item: item.requested_at)

    def mark(
        self, approval_id: str, status: ApprovalStatus, reviewer: str, note: str = ""
    ) -> PendingApproval | None:
        with self._document.update() as data:
            raw = data.get(APPROVALS_KEY, {}).get(approval_id)
            if raw is None:
                return None
            raw["status"] = status.value
            raw["reviewer"] = reviewer
            raw["reviewed_at"] = datetime.now(UTC).isoformat()
            raw["review_note"] = note
            return _deserialise(approval_id, raw)

    def count_recent_outbound(self, opportunity_id: str, hours: int = 24) -> int:
        threshold = datetime.now(UTC) - timedelta(hours=hours)
        entries = self._document.read().get(SENT_KEY, {}).get(opportunity_id, [])
        return sum(1 for entry in entries if _parse(entry) and _parse(entry) >= threshold)

    def record_sent(self, opportunity_id: str) -> None:
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=7)
        with self._document.update() as data:
            log = data.setdefault(SENT_KEY, {})
            entries = log.get(opportunity_id, [])
            # Purge glissante : ce journal ne sert qu'à la cadence récente, inutile de le
            # laisser croître indéfiniment.
            entries = [e for e in entries if (_parse(e) or now) >= cutoff]
            entries.append(now.isoformat())
            log[opportunity_id] = entries


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _as_dict(approval: PendingApproval) -> dict:
    return {field: getattr(approval, field) for field in PendingApproval.__slots__}


def _serialise(approval: PendingApproval) -> dict:
    return {
        "opportunity_id": approval.opportunity_id,
        "company": approval.company,
        "kind": approval.kind.value,
        "recipient": approval.recipient,
        "rule": approval.rule,
        "reason": approval.reason,
        "requested_at": approval.requested_at.isoformat(),
        "payload": approval.payload,
        "status": approval.status.value,
        "reviewer": approval.reviewer,
        "reviewed_at": approval.reviewed_at.isoformat() if approval.reviewed_at else None,
        "review_note": approval.review_note,
    }


def _deserialise(approval_id: str, raw: dict) -> PendingApproval:
    return PendingApproval(
        id=approval_id,
        opportunity_id=raw.get("opportunity_id", ""),
        company=raw.get("company", ""),
        kind=ActionKind(raw.get("kind", ActionKind.EMAIL.value)),
        recipient=raw.get("recipient", ""),
        rule=raw.get("rule", ""),
        reason=raw.get("reason", ""),
        requested_at=_parse(raw.get("requested_at")) or datetime.now(UTC),
        payload=raw.get("payload", {}),
        status=ApprovalStatus(raw.get("status", ApprovalStatus.PENDING.value)),
        reviewer=raw.get("reviewer", ""),
        reviewed_at=_parse(raw.get("reviewed_at")),
        review_note=raw.get("review_note", ""),
    )


def _parse(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
