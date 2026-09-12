"""HubSpot CRM adapter — implements `CrmPort` against the real API.

Design choice: **the CRM is the single source of truth**. Everything the agent learns
(objections, decisions, interactions) is written as prefixed notes, so it stays readable by a
human inside HubSpot *and* re-parsable by the agent on the next scan. No hidden database
sitting next to the CRM that sales reps would never see.

Associations: we use the v4 `associations/default/...` endpoint rather than hard-coded
`associationTypeId` values (214 for note→deal, and so on). HubSpot applies the default type
itself there — a wrong numeric constant would produce a 400 that is hard to diagnose.

Note: HubSpot is migrating to date-based versioning (`/crm/objects/2026-09/`). The v3/v4
paths used here remain supported.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
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
    "hubspot_owner_id",
]
CONTACT_PROPERTIES = ["email", "phone", "mobilephone", "firstname", "lastname", "jobtitle"]
COMPANY_PROPERTIES = ["name", "domain", "industry", "numberofemployees"]

OBJECTION_PREFIX = "[OBJECTION]"
INTERACTION_PREFIX = "[INTERACTION]"
MAX_NOTES_READ = 20

# HubSpot notes are a journal: you don't edit a past note, you add one that closes the
# previous one. Hence a short identifier carried by the objection note and echoed by the
# resolution note. Matching on text would break at the slightest rewording.
_OBJECTION_RE = re.compile(r"^\[OBJECTION(?::([A-Za-z0-9]+))?\]\s*(.*)", re.DOTALL)
# `[STAKEHOLDER] Name | stance: decision_maker | free-form notes`
_STAKEHOLDER_RE = re.compile(r"^\[STAKEHOLDER\]\s*(.*)", re.DOTALL)
_RESOLUTION_RE = re.compile(r"^\[OBJECTION-RESOLVED:([A-Za-z0-9]+)\]\s*(.*)", re.DOTALL)


class HubSpotCrmAdapter:
    def __init__(self, token: str) -> None:
        self._http = HttpClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            error_factory=lambda message: CrmError("hubspot", message),
        )

    # -- Reads --------------------------------------------------------------------

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
        objections, history, stances = _parse_notes(notes)

        return Opportunity(
            id=deal["id"],
            company=company.get("name") or properties.get("dealname") or deal["id"],
            stage=properties.get("dealstage") or "discovery",
            probability=_to_int(properties.get("hs_deal_stage_probability")),
            amount=_to_float(properties.get("amount")),
            stakeholders=_merge_stances(
                tuple(_to_stakeholder(props) for props in contacts.values()), stances
            ),
            objections=objections,
            history=history,
            last_activity_at=_from_millis(properties.get("hs_lastmodifieddate")),
            source="hubspot",
            owner_id=properties.get("hubspot_owner_id") or "",
        )

    def _batch_read(self, object_type: str, ids: list[str], properties: list[str]) -> dict:
        """`?associations=` does not work on batch endpoints — hence these separate
        hydration calls."""
        if not ids:
            return {}
        payload = self._http.request(
            "POST",
            f"/crm/v3/objects/{object_type}/batch/read",
            json={"inputs": [{"id": str(i)} for i in ids], "properties": properties},
        )
        return {row["id"]: row.get("properties", {}) for row in payload.get("results", [])}

    # -- Writes -------------------------------------------------------------------

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
            # Since 2026-09, HubSpot enforces admin-configured validation rules on writes: a
            # rejection can be a business-level one, not merely a technical failure.
            self._http.request(
                "PATCH",
                f"/crm/v3/objects/deals/{opportunity_id}",
                json={"properties": properties},
            )

        if objection is not None:
            marker = f"[OBJECTION:{objection.id}]" if objection.id else OBJECTION_PREFIX
            self._create_note(
                opportunity_id,
                f"{marker} {objection.text} | probable cause: {objection.root_cause}",
            )
        if next_steps:
            self._create_note(
                opportunity_id, f"{INTERACTION_PREFIX} Next step: {next_steps}"
            )
        if probability is not None:
            logger.debug(
                "Probability %s not pushed to HubSpot (property computed CRM-side)",
                probability,
            )

    def update_stakeholder(
        self,
        opportunity_id: str,
        *,
        name: str,
        stance: Stance,
        role: str = "",
        notes: str = "",
    ) -> None:
        """Written as a prefixed note, like objections.

        HubSpot has no standard property carrying "who blocks this deal": a custom property
        would have to be created in every customer portal. A note is readable by the rep in the
        interface and re-parsable by the agent on the next scan — the same single source of
        truth, with nothing to configure.
        """
        segments = [f"{name.strip()}", f"stance: {stance.value}"]
        if role.strip():
            segments.insert(1, f"role: {role.strip()}")
        if notes.strip():
            segments.append(notes.strip())
        self._create_note(opportunity_id, f"[STAKEHOLDER] {' | '.join(segments)}")

    def resolve_objection(self, opportunity_id: str, objection_id: str, resolution: str) -> bool:
        if not objection_id:
            return False
        self._create_note(opportunity_id, f"[OBJECTION-RESOLVED:{objection_id}] {resolution}")
        return True

    def find_opportunity_by_phone(self, phone_number: str) -> str | None:
        """Indexed search on the HubSpot side, then contact → deal association.

        Two to three calls in total. The alternative — walking the book of business and
        comparing numbers locally — would cost hundreds of calls per inbound message, against
        a search quota capped at 4 requests/second.
        """
        variants = _phone_variants(phone_number)
        if not variants:
            return None

        payload = self._http.request(
            "POST",
            "/crm/v3/objects/contacts/search",
            json={
                # Separate filter groups are interpreted as an OR: we query both phone
                # properties for every plausible spelling of the number.
                "filterGroups": [
                    {"filters": [{"propertyName": prop, "operator": "EQ", "value": variant}]}
                    for variant in variants
                    for prop in ("phone", "mobilephone")
                ],
                "properties": ["phone", "mobilephone"],
                "limit": 5,
            },
        )

        for contact in payload.get("results", []):
            associations = self._http.request(
                "GET",
                f"/crm/v3/objects/contacts/{contact['id']}",
                params={"associations": "deals"},
            )
            deal_ids = _association_ids(associations.get("associations", {}), "deals")
            if deal_ids:
                return deal_ids[0]

        logger.info("No opportunity associated with number %s", phone_number)
        return None

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


# -- HubSpot → domain translation -------------------------------------------------


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


@dataclass(frozen=True, slots=True)
class _StakeholderNote:
    """One stance note, already parsed. `key` is the name used for matching."""

    key: str
    name: str
    stance: Stance
    role: str
    notes: str
    noted_at: datetime


def _parse_stakeholder_note(body: str, occurred_at: datetime) -> _StakeholderNote | None:
    """`Nom | rôle : X | posture : Y | notes`, the `rôle` and notes segments being optional."""
    segments = [segment.strip() for segment in body.split("|") if segment.strip()]
    if not segments:
        return None

    name = segments[0]
    role, notes, stance = "", [], Stance.UNKNOWN

    for segment in segments[1:]:
        lowered = segment.casefold()
        if lowered.startswith("stance:"):
            stance = _parse_stance(segment.split(":", 1)[1])
        elif lowered.startswith("role:"):
            role = segment.split(":", 1)[1].strip()
        else:
            notes.append(segment)

    return _StakeholderNote(
        key=name.casefold(),
        name=name,
        stance=stance,
        role=role,
        notes=" | ".join(notes),
        noted_at=occurred_at,
    )


def _parse_stance(raw: str) -> Stance:
    try:
        return Stance(raw.strip().casefold())
    except ValueError:
        return Stance.UNKNOWN


def _merge_stances(
    contacts: tuple[Stakeholder, ...], stances: dict[str, _StakeholderNote]
) -> tuple[Stakeholder, ...]:
    """Applies what the agent learned on top of the contacts read from HubSpot.

    A note whose name matches no contact becomes a stakeholder of its own, without contact
    details: the CFO who decides the budget and has never been contacted belongs on the map,
    precisely because nobody has spoken to them.
    """
    remaining = dict(stances)
    merged: list[Stakeholder] = []

    for contact in contacts:
        note = remaining.pop(contact.name.casefold(), None)
        if note is None:
            merged.append(contact)
            continue
        merged.append(
            replace(
                contact,
                stance=note.stance,
                role=note.role or contact.role,
                notes=note.notes or contact.notes,
            )
        )

    merged.extend(
        Stakeholder(name=note.name, role=note.role, stance=note.stance, notes=note.notes)
        for note in remaining.values()
    )
    return tuple(merged)


def _to_stakeholder(properties: dict) -> Stakeholder:
    name = " ".join(
        part for part in (properties.get("firstname"), properties.get("lastname")) if part
    )
    return Stakeholder(
        name=name or properties.get("email") or "Unnamed contact",
        role=properties.get("jobtitle") or "",
        email=properties.get("email") or None,
        phone=properties.get("phone") or properties.get("mobilephone") or None,
        stance=Stance.UNKNOWN,
    )


def _parse_notes(
    notes: dict,
) -> tuple[tuple[Objection, ...], tuple[Interaction, ...], dict[str, _StakeholderNote]]:
    """Reads back what the agent wrote during previous cycles.

    Two passes: resolutions are collected first, then the matching objections are marked.
    Without that, an objection noted before its resolution would stay open — and the API does
    not guarantee the ordering of notes.

    Stakeholder notes are collapsed to one per person, the most recent winning: a stance is
    meant to move (neutral, then champion, then blocker once the budget is refused), and it is
    the latest reading that matters.
    """
    raw_objections: list[Objection] = []
    resolutions: dict[str, str] = {}
    history: list[Interaction] = []
    stances: dict[str, _StakeholderNote] = {}

    for properties in notes.values():
        body = (properties.get("hs_note_body") or "").strip()
        occurred_at = _from_millis(properties.get("hs_timestamp")) or datetime.now(UTC)
        if not body:
            continue

        resolution = _RESOLUTION_RE.match(body)
        if resolution:
            resolutions[resolution.group(1)] = resolution.group(2).strip()
            continue

        objection = _OBJECTION_RE.match(body)
        if objection:
            text, _, cause = objection.group(2).partition("| probable cause:")
            raw_objections.append(
                Objection(
                    id=objection.group(1) or "",
                    text=text.strip(),
                    root_cause=cause.strip() or "unknown",
                    raised_at=occurred_at,
                )
            )
            continue

        stakeholder = _STAKEHOLDER_RE.match(body)
        if stakeholder:
            parsed = _parse_stakeholder_note(stakeholder.group(1), occurred_at)
            if parsed is not None:
                known = stances.get(parsed.key)
                if known is None or parsed.noted_at >= known.noted_at:
                    stances[parsed.key] = parsed
            continue

        summary = body
        if body.startswith(INTERACTION_PREFIX):
            summary = body[len(INTERACTION_PREFIX) :].strip()
        history.append(
            Interaction(channel=Channel.SIGNAL, summary=summary, occurred_at=occurred_at)
        )

    objections = tuple(
        replace(objection, resolved=True, resolution=resolutions[objection.id])
        if objection.id and objection.id in resolutions
        else objection
        for objection in raw_objections
    )

    history.sort(key=lambda item: item.occurred_at)
    return objections, tuple(history), stances


def _call_body(outcome: CallOutcome) -> str:
    sections = []
    if outcome.summary:
        sections.append(f"Summary: {outcome.summary}")
    if outcome.sentiment:
        sections.append(f"Sentiment: {outcome.sentiment}")
    if outcome.transcript:
        sections.append(f"Transcript:\n{outcome.transcript}")
    return "\n\n".join(sections) or "Call with no transcript."


def _phone_variants(phone_number: str) -> list[str]:
    """Plausible spellings of one and the same number, for a strict-equality search.

    HubSpot compares phone properties exactly as they were entered, and a CRM holds
    "+33 6 00 00 00 01" just as readily as "0600000001". So we query several forms rather than
    comparing digit suffixes locally — matching on trailing digits would produce false
    positives between distinct prospects.
    """
    digits = "".join(character for character in phone_number if character.isdigit())
    if len(digits) < 8:
        return []

    variants = {digits, f"+{digits}"}
    # French national number reconstructed from the international form, and vice versa.
    if digits.startswith("33") and len(digits) == 11:
        variants.add(f"0{digits[2:]}")
    elif digits.startswith("0") and len(digits) == 10:
        variants.add(f"+33{digits[1:]}")
        variants.add(f"33{digits[1:]}")
    return sorted(variants)


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
