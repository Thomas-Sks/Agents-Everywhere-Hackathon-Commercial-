"""Adapter CRM HubSpot — implémente `CrmPort` sur l'API réelle.

Choix de conception : **le CRM est la seule source de vérité**. Ce que l'agent apprend
(objections, décisions, interactions) est écrit sous forme de notes préfixées, donc lisible
par un humain dans HubSpot *et* re-parsable par l'agent au scan suivant. Pas de base cachée à
côté du CRM que les commerciaux ne verraient jamais.

Associations : on utilise l'endpoint v4 `associations/default/...` plutôt que des
`associationTypeId` en dur (214 pour note→deal, etc.). HubSpot y applique le type par défaut
lui-même — une constante numérique erronée produirait un 400 difficile à diagnostiquer.

Note : HubSpot migre vers un versionnage par date (`/crm/objects/2026-09/`). Les chemins
v3/v4 utilisés ici restent supportés.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from revenue_agent.adapters.http import HttpClient
from revenue_agent.domain.errors import CrmError, OpportunityNotFound
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

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"

DEAL_PROPERTIES = [
    "dealname",
    "dealstage",
    "amount",
    "closedate",
    "hs_lastmodifieddate",
    "hs_deal_stage_probability",
]
CONTACT_PROPERTIES = ["email", "phone", "mobilephone", "firstname", "lastname", "jobtitle"]
COMPANY_PROPERTIES = ["name", "domain", "industry", "numberofemployees"]

OBJECTION_PREFIX = "[OBJECTION]"
INTERACTION_PREFIX = "[INTERACTION]"
MAX_NOTES_READ = 20


class HubSpotCrmAdapter:
    def __init__(self, token: str) -> None:
        self._http = HttpClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            error_factory=lambda message: CrmError("hubspot", message),
        )

    # -- Lecture ------------------------------------------------------------------

    def find_modified_since(
        self, since: datetime, cursor: str | None = None, page_size: int = 100
    ) -> Page:
        body: dict = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "hs_lastmodifieddate",
                            "operator": "GTE",
                            "value": str(_to_millis(since)),
                        }
                    ]
                }
            ],
            "properties": DEAL_PROPERTIES,
            "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}],
            "limit": page_size,
        }
        if cursor:
            body["after"] = cursor

        payload = self._http.request("POST", "/crm/v3/objects/deals/search", json=body)
        snapshots = tuple(
            _to_snapshot(row) for row in payload.get("results", []) if row.get("properties")
        )
        next_cursor = payload.get("paging", {}).get("next", {}).get("after")
        return Page(items=snapshots, next_cursor=next_cursor)

    def load_opportunity(self, opportunity_id: str) -> Opportunity:
        try:
            deal = self._http.request(
                "GET",
                f"/crm/v3/objects/deals/{opportunity_id}",
                params={
                    "properties": ",".join(DEAL_PROPERTIES),
                    "associations": "contacts,companies,notes",
                },
            )
        except CrmError as exc:
            if "→ 404" in str(exc):
                raise OpportunityNotFound(opportunity_id) from exc
            raise

        associations = deal.get("associations", {})
        contact_ids = _association_ids(associations, "contacts")
        company_ids = _association_ids(associations, "companies")
        note_ids = _association_ids(associations, "notes")[-MAX_NOTES_READ:]

        contacts = self._batch_read("contacts", contact_ids, CONTACT_PROPERTIES)
        companies = self._batch_read("companies", company_ids, COMPANY_PROPERTIES)
        notes = self._batch_read("notes", note_ids, ["hs_note_body", "hs_timestamp"])

        properties = deal.get("properties", {})
        company = next(iter(companies.values()), {})
        objections, history = _parse_notes(notes)

        return Opportunity(
            id=deal["id"],
            company=company.get("name") or properties.get("dealname") or deal["id"],
            stage=properties.get("dealstage") or "discovery",
            probability=_to_int(properties.get("hs_deal_stage_probability")),
            amount=_to_float(properties.get("amount")),
            stakeholders=tuple(_to_stakeholder(props) for props in contacts.values()),
            objections=objections,
            history=history,
            last_activity_at=_from_millis(properties.get("hs_lastmodifieddate")),
            source="hubspot",
        )

    def _batch_read(self, object_type: str, ids: list[str], properties: list[str]) -> dict:
        """`?associations=` ne fonctionne pas sur les endpoints batch — d'où ces appels
        d'hydratation séparés."""
        if not ids:
            return {}
        payload = self._http.request(
            "POST",
            f"/crm/v3/objects/{object_type}/batch/read",
            json={"inputs": [{"id": str(i)} for i in ids], "properties": properties},
        )
        return {row["id"]: row.get("properties", {}) for row in payload.get("results", [])}

    # -- Écriture -----------------------------------------------------------------

    def record_interaction(self, opportunity_id: str, interaction: Interaction) -> None:
        self._create_note(
            opportunity_id,
            f"{INTERACTION_PREFIX} ({interaction.channel.value}) {interaction.summary}",
        )

    def update_opportunity(
        self,
        opportunity_id: str,
        *,
        stage: str | None = None,
        probability: int | None = None,
        objection: Objection | None = None,
        next_steps: str | None = None,
    ) -> None:
        properties: dict[str, str] = {}
        if stage:
            properties["dealstage"] = stage
        if properties:
            # Depuis 2026-09, HubSpot applique les règles de validation de l'admin sur les
            # écritures : un refus peut être métier, pas seulement technique.
            self._http.request(
                "PATCH",
                f"/crm/v3/objects/deals/{opportunity_id}",
                json={"properties": properties},
            )

        if objection is not None:
            self._create_note(
                opportunity_id,
                f"{OBJECTION_PREFIX} {objection.text} | cause probable : {objection.root_cause}",
            )
        if next_steps:
            self._create_note(
                opportunity_id, f"{INTERACTION_PREFIX} Prochaine étape : {next_steps}"
            )
        if probability is not None:
            logger.debug(
                "Probabilité %s non poussée vers HubSpot (propriété calculée côté CRM)",
                probability,
            )

    def log_call(self, opportunity_id: str, outcome: CallOutcome) -> None:
        payload = self._http.request(
            "POST",
            "/crm/v3/objects/calls",
            json={
                "properties": {
                    "hs_timestamp": _to_millis(datetime.now(UTC)),
                    "hs_call_body": _call_body(outcome),
                    "hs_call_duration": outcome.duration_seconds * 1000,
                    "hs_call_direction": "OUTBOUND",
                    "hs_call_status": "COMPLETED" if outcome.successful else "NO_ANSWER",
                }
            },
        )
        self._associate_default("calls", payload["id"], "deals", opportunity_id)

    def _create_note(self, opportunity_id: str, body: str) -> None:
        payload = self._http.request(
            "POST",
            "/crm/v3/objects/notes",
            json={
                "properties": {
                    "hs_note_body": body,
                    "hs_timestamp": _to_millis(datetime.now(UTC)),
                }
            },
        )
        self._associate_default("notes", payload["id"], "deals", opportunity_id)

    def _associate_default(
        self, from_type: str, from_id: str, to_type: str, to_id: str
    ) -> None:
        self._http.request(
            "PUT",
            f"/crm/v4/objects/{from_type}/{from_id}/associations/default/{to_type}/{to_id}",
        )


# -- Traduction HubSpot → domaine -------------------------------------------------


def _to_snapshot(row: dict) -> DealSnapshot:
    properties = row.get("properties", {})
    return DealSnapshot(
        id=row["id"],
        name=properties.get("dealname") or row["id"],
        stage=properties.get("dealstage") or "",
        last_modified_at=_from_millis(properties.get("hs_lastmodifieddate"))
        or datetime.now(UTC),
        amount=_to_float(properties.get("amount")),
    )


def _to_stakeholder(properties: dict) -> Stakeholder:
    name = " ".join(
        part for part in (properties.get("firstname"), properties.get("lastname")) if part
    )
    return Stakeholder(
        name=name or properties.get("email") or "Contact sans nom",
        role=properties.get("jobtitle") or "",
        email=properties.get("email") or None,
        phone=properties.get("phone") or properties.get("mobilephone") or None,
        stance=Stance.UNKNOWN,
    )


def _parse_notes(notes: dict) -> tuple[tuple[Objection, ...], tuple[Interaction, ...]]:
    """Relit ce que l'agent a écrit lors des cycles précédents."""
    objections: list[Objection] = []
    history: list[Interaction] = []

    for properties in notes.values():
        body = (properties.get("hs_note_body") or "").strip()
        occurred_at = _from_millis(properties.get("hs_timestamp")) or datetime.now(UTC)
        if not body:
            continue

        if body.startswith(OBJECTION_PREFIX):
            text, _, cause = body[len(OBJECTION_PREFIX) :].partition("| cause probable :")
            objections.append(
                Objection(
                    text=text.strip(),
                    root_cause=cause.strip() or "inconnue",
                    raised_at=occurred_at,
                )
            )
        else:
            summary = body
            if body.startswith(INTERACTION_PREFIX):
                summary = body[len(INTERACTION_PREFIX) :].strip()
            history.append(
                Interaction(channel=Channel.SIGNAL, summary=summary, occurred_at=occurred_at)
            )

    history.sort(key=lambda item: item.occurred_at)
    return tuple(objections), tuple(history)


def _call_body(outcome: CallOutcome) -> str:
    sections = []
    if outcome.summary:
        sections.append(f"Résumé : {outcome.summary}")
    if outcome.sentiment:
        sections.append(f"Sentiment : {outcome.sentiment}")
    if outcome.transcript:
        sections.append(f"Transcript :\n{outcome.transcript}")
    return "\n\n".join(sections) or "Appel sans transcript."


def _association_ids(associations: dict, key: str) -> list[str]:
    return [row["id"] for row in associations.get(key, {}).get("results", []) if row.get("id")]


def _to_millis(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _from_millis(raw: str | int | None) -> datetime | None:
    if raw in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=UTC)
    except (TypeError, ValueError):
        return None


def _to_int(raw: str | None) -> int:
    try:
        return int(float(raw)) if raw else 0
    except (TypeError, ValueError):
        return 0


def _to_float(raw: str | None) -> float | None:
    try:
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None
