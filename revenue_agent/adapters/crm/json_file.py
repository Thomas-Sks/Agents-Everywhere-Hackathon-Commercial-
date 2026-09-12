"""Local JSON-file CRM adapter — implements `CrmPort` with no network dependency.

Two uses: running the demo when HubSpot is not configured, and exercising the use cases in
tests. Same port, same contract — which is what lets degradation be a wiring choice rather than
a cascade of `if api_key is None` throughout the business code.
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
                        "id": objection.id,
                        "resolved": objection.resolved,
                        "raised_at": (objection.raised_at or datetime.now(UTC)).isoformat(),
                    }
                )
            opportunity["last_activity_at"] = datetime.now(UTC).isoformat()

    def update_stakeholder(
        self,
        opportunity_id: str,
        *,
        name: str,
        stance: Stance,
        role: str = "",
        notes: str = "",
    ) -> None:
        with self._document.update() as data:
            opportunity = self._ensure(data, opportunity_id)
            stakeholders = opportunity.setdefault("stakeholders", [])

            for entry in stakeholders:
                if _same_person(entry.get("name", ""), name):
                    entry["stance"] = stance.value
                    # Only overwrite on new information: an update about the stance must not
                    # silently erase a role or notes learned earlier.
                    if role:
                        entry["role"] = role
                    if notes:
                        entry["notes"] = notes
                    return

            stakeholders.append(
                {
                    "name": name,
                    "role": role,
                    "email": None,
                    "phone": None,
                    "stance": stance.value,
                    "notes": notes,
                }
            )

    def resolve_objection(self, opportunity_id: str, objection_id: str, resolution: str) -> bool:
        with self._document.update() as data:
            opportunity = data.get(OPPORTUNITIES_KEY, {}).get(opportunity_id)
            if opportunity is None:
                return False
            for item in opportunity.get("objections", []):
                if item.get("id") == objection_id:
                    item["resolved"] = True
                    item["resolution"] = resolution
                    return True
        return False

    def find_opportunity_by_phone(self, phone_number: str) -> str | None:
        digits = _digits(phone_number)
        if len(digits) < 8:
            return None
        for opportunity_id, raw in self._document.read().get(OPPORTUNITIES_KEY, {}).items():
            for stakeholder in raw.get("stakeholders", []):
                candidate = _digits(stakeholder.get("phone") or "")
                # Compare on the full national number: two distinct prospects can share a
                # suffix, never the whole number.
                if candidate and _national(candidate) == _national(digits):
                    return opportunity_id
        return None

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
                id=item.get("id", ""),
                resolution=item.get("resolution", ""),
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


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _national(digits: str) -> str:
    """Reduces a number to its national form so two spellings can be compared."""
    if digits.startswith("33") and len(digits) == 11:
        return digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        return digits[1:]
    return digits


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


def _same_person(left: str, right: str) -> bool:
    """Loose name matching — the model writes "julie martin" where the CRM has "Julie Martin"."""
    return left.strip().casefold() == right.strip().casefold()


def _parse_stance(raw: str | None) -> Stance:
    try:
        return Stance(raw) if raw else Stance.UNKNOWN
    except ValueError:
        return Stance.UNKNOWN
