"""Adapter CRM local sur fichier JSON — implémente `CrmPort` sans dépendance réseau.

Deux usages : faire tourner la démo quand HubSpot n'est pas configuré, et exercer les use
cases dans les tests. Même port, même contrat — c'est ce qui permet à la dégradation d'être un
choix de câblage plutôt qu'une cascade de `if api_key is None` dans le code métier.
"""

from __future__ import annotations

from datetime import UTC, datetime

from revenue_agent.adapters.storage.json_document import JsonDocument
from revenue_agent.domain.errors import OpportunityNotFound
from revenue_agent.domain.models import (
    CallOutcome,
    Channel,
    DealSnapshot,
    Interaction,
    Objection,
    Opportunity,
    Page,
    Stakeholder,
    Stance,
)

OPPORTUNITIES_KEY = "opportunities"


class JsonFileCrmAdapter:
    def __init__(self, document: JsonDocument) -> None:
        self._document = document

    def find_modified_since(
        self, since: datetime, cursor: str | None = None, page_size: int = 100
    ) -> Page:
        data = self._document.read().get(OPPORTUNITIES_KEY, {})
        snapshots = []
        for opportunity_id, raw in data.items():
            modified_at = _parse_datetime(raw.get("last_activity_at"))
            if modified_at is None or modified_at >= since:
                snapshots.append(
                    DealSnapshot(
                        id=opportunity_id,
                        name=raw.get("company", opportunity_id),
                        stage=raw.get("stage", ""),
                        last_modified_at=modified_at or datetime.now(UTC),
                        amount=raw.get("amount"),
                    )
                )
        snapshots.sort(key=lambda item: item.last_modified_at)
        return Page(items=tuple(snapshots[:page_size]), next_cursor=None)

    def load_opportunity(self, opportunity_id: str) -> Opportunity:
        raw = self._document.read().get(OPPORTUNITIES_KEY, {}).get(opportunity_id)
        if raw is None:
            raise OpportunityNotFound(opportunity_id)
        return _deserialise(opportunity_id, raw)

    def record_interaction(self, opportunity_id: str, interaction: Interaction) -> None:
        with self._document.update() as data:
            opportunity = self._ensure(data, opportunity_id)
            opportunity.setdefault("history", []).append(
                {
                    "channel": interaction.channel.value,
                    "summary": interaction.summary,
                    "occurred_at": interaction.occurred_at.isoformat(),
                }
            )
            opportunity["last_activity_at"] = interaction.occurred_at.isoformat()

    def update_opportunity(
        self,
        opportunity_id: str,
        *,
        stage: str | None = None,
        probability: int | None = None,
        objection: Objection | None = None,
        next_steps: str | None = None,
    ) -> None:
        with self._document.update() as data:
            opportunity = self._ensure(data, opportunity_id)
            if stage:
                opportunity["stage"] = stage
            if probability is not None:
                opportunity["probability"] = probability
            if next_steps:
                opportunity["next_steps"] = next_steps
            if objection is not None:
                opportunity.setdefault("objections", []).append(
                    {
                        "text": objection.text,
                        "root_cause": objection.root_cause,
                        "resolved": objection.resolved,
                        "raised_at": (objection.raised_at or datetime.now(UTC)).isoformat(),
                    }
                )
            opportunity["last_activity_at"] = datetime.now(UTC).isoformat()

    def log_call(self, opportunity_id: str, outcome: CallOutcome) -> None:
        summary = outcome.summary or "Appel terminé"
        details = f"{summary} (durée {outcome.duration_seconds}s)"
        if outcome.transcript:
            details += f"\nTranscript : {outcome.transcript}"
        self.record_interaction(
            opportunity_id,
            Interaction(
                channel=Channel.VOICE, summary=details, occurred_at=datetime.now(UTC)
            ),
        )

    @staticmethod
    def _ensure(data: dict, opportunity_id: str) -> dict:
        opportunities = data.setdefault(OPPORTUNITIES_KEY, {})
        return opportunities.setdefault(
            opportunity_id,
            {
                "company": opportunity_id,
                "stage": "discovery",
                "probability": 0,
                "stakeholders": [],
                "objections": [],
                "history": [],
                "next_steps": "",
                "risk_notes": "",
            },
        )


def _deserialise(opportunity_id: str, raw: dict) -> Opportunity:
    return Opportunity(
        id=opportunity_id,
        company=raw.get("company", opportunity_id),
        stage=raw.get("stage", "discovery"),
        probability=int(raw.get("probability", 0)),
        amount=raw.get("amount"),
        stakeholders=tuple(
            Stakeholder(
                name=item.get("name", ""),
                role=item.get("role", ""),
                email=item.get("email"),
                phone=item.get("phone"),
                stance=_parse_stance(item.get("stance")),
                notes=item.get("notes", ""),
            )
            for item in raw.get("stakeholders", [])
        ),
        objections=tuple(
            Objection(
                text=item.get("text", ""),
                root_cause=item.get("root_cause", "inconnue"),
                resolved=bool(item.get("resolved", False)),
                raised_at=_parse_datetime(item.get("raised_at")),
            )
            for item in raw.get("objections", [])
        ),
        history=tuple(
            Interaction(
                channel=_parse_channel(item.get("channel")),
                summary=item.get("summary", ""),
                occurred_at=_parse_datetime(item.get("occurred_at")) or datetime.now(UTC),
            )
            for item in raw.get("history", [])
        ),
        next_steps=raw.get("next_steps", ""),
        risk_notes=raw.get("risk_notes", ""),
        last_activity_at=_parse_datetime(raw.get("last_activity_at")),
        source="local",
    )


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_channel(raw: str | None) -> Channel:
    try:
        return Channel(raw) if raw else Channel.SIGNAL
    except ValueError:
        return Channel.SIGNAL


def _parse_stance(raw: str | None) -> Stance:
    try:
        return Stance(raw) if raw else Stance.UNKNOWN
    except ValueError:
        return Stance.UNKNOWN
