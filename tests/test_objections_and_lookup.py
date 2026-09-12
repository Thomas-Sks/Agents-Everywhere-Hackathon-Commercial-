"""Objection life cycle, and resolving an opportunity from a phone number.

Two defects these tests lock down:

1. Objections had no write path towards `resolved=True`. They therefore accumulated
   indefinitely, which pinned stakes-based routing to the most expensive model (`stakes`
   escalates as soon as an objection is open) and polluted the context on every cycle.
2. Resolving an opportunity from an inbound number swept the whole book of business — one
   request per opportunity — and matched on the trailing digits, so it was slow and carried a
   risk of false positives.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from revenue_agent.adapters.crm.hubspot import _parse_notes, _phone_variants
from revenue_agent.adapters.crm.json_file import JsonFileCrmAdapter
from revenue_agent.adapters.storage.json_document import JsonDocument
from revenue_agent.domain import stakes
from revenue_agent.domain.models import Objection, Opportunity, Stakeholder


@pytest.fixture
def crm_local(tmp_path) -> JsonFileCrmAdapter:
    adapter = JsonFileCrmAdapter(JsonDocument(tmp_path / "crm.json"))
    adapter.update_opportunity(
        "acme-co",
        objection=Objection(id="ab12cd", text="C'est trop cher", root_cause="budget"),
    )
    return adapter


# -- Objection life cycle -----------------------------------------------------------


def test_an_objection_starts_open(crm_local):
    opportunity = crm_local.load_opportunity("acme-co")

    assert len(opportunity.unresolved_objections()) == 1


def test_a_resolved_objection_drops_out_of_the_open_ones(crm_local):
    assert crm_local.resolve_objection("acme-co", "ab12cd", "Budget débloqué par le CFO") is True

    opportunity = crm_local.load_opportunity("acme-co")
    assert opportunity.unresolved_objections() == ()
    assert opportunity.objections[0].resolution == "Budget débloqué par le CFO"


def test_resolving_an_unknown_objection_fails_without_crashing(crm_local):
    assert crm_local.resolve_objection("acme-co", "inexistante", "peu importe") is False
    assert len(crm_local.load_opportunity("acme-co").unresolved_objections()) == 1


def test_resolving_frees_up_the_economy_routing():
    """The economic consequence of the defect: without resolution, everything went through the
    expensive model."""
    open_objection = Opportunity(
        id="acme-co",
        company="Acme",
        amount=1_000,
        objections=(Objection(id="x1", text="Trop cher", root_cause="budget"),),
    )
    resolved_objection = Opportunity(
        id="acme-co",
        company="Acme",
        amount=1_000,
        objections=(
            Objection(id="x1", text="Trop cher", root_cause="budget", resolved=True),
        ),
    )

    assert stakes.requires_strategic_reasoning(open_objection) is True
    assert stakes.requires_strategic_reasoning(resolved_objection) is False


# -- Reading HubSpot notes back -----------------------------------------------------


def note(body: str, moment: str = "1757000000000") -> dict:
    return {"hs_note_body": body, "hs_timestamp": moment}


def test_hubspot_notes_carry_the_objection_identifier():
    objections, _, _ = _parse_notes(
        {"1": note("[OBJECTION:ab12cd] Trop cher | cause probable : budget")}
    )

    assert objections[0].id == "ab12cd"
    assert objections[0].resolved is False


def test_a_resolution_note_closes_the_matching_objection():
    objections, _, _ = _parse_notes(
        {
            "1": note("[OBJECTION:ab12cd] Trop cher | cause probable : budget"),
            "2": note("[OBJECTION-RESOLUE:ab12cd] Budget débloqué"),
        }
    )

    assert objections[0].resolved is True
    assert objections[0].resolution == "Budget débloqué"


def test_resolution_works_even_when_the_note_arrives_first():
    """The API does not guarantee note ordering: resolutions must be collected first."""
    objections, _, _ = _parse_notes(
        {
            "1": note("[OBJECTION-RESOLUE:ab12cd] Budget débloqué"),
            "2": note("[OBJECTION:ab12cd] Trop cher | cause probable : budget"),
        }
    )

    assert objections[0].resolved is True


def test_a_resolution_only_closes_the_objection_it_targets():
    objections, _, _ = _parse_notes(
        {
            "1": note("[OBJECTION:aaa] Trop cher | cause probable : budget"),
            "2": note("[OBJECTION:bbb] Mauvais timing | cause probable : contrat"),
            "3": note("[OBJECTION-RESOLUE:aaa] Budget débloqué"),
        }
    )

    by_id = {objection.id: objection for objection in objections}
    assert by_id["aaa"].resolved is True
    assert by_id["bbb"].resolved is False


def test_a_resolution_note_is_not_read_as_history():
    objections, history, _ = _parse_notes({"1": note("[OBJECTION-RESOLUE:aaa] Réglé")})

    assert objections == ()
    assert history == (), "a resolution must not pollute the interaction history"


# -- Resolution by phone number -----------------------------------------------------


def test_the_number_is_searched_under_its_plausible_spellings():
    variants = _phone_variants("+33 6 00 00 00 01")

    assert "33600000001" in variants
    assert "+33600000001" in variants
    assert "0600000001" in variants, "a CRM also holds the national form"


def test_a_number_that_is_too_short_is_not_searched():
    assert _phone_variants("1234") == []


def test_local_resolution_by_phone_number(tmp_path):
    adapter = JsonFileCrmAdapter(JsonDocument(tmp_path / "crm.json"))
    adapter.update_opportunity("acme-co")
    with adapter._document.update() as data:
        data["opportunities"]["acme-co"]["stakeholders"] = [
            {"name": "Julie", "phone": "+33600000001"}
        ]

    assert adapter.find_opportunity_by_phone("33600000001") == "acme-co"
    assert adapter.find_opportunity_by_phone("0600000001") == "acme-co"


def test_a_shared_suffix_does_not_produce_a_false_positive(tmp_path):
    """The original defect matched on nine digits: two prospects could collide."""
    adapter = JsonFileCrmAdapter(JsonDocument(tmp_path / "crm.json"))
    adapter.update_opportunity("acme-co")
    with adapter._document.update() as data:
        data["opportunities"]["acme-co"]["stakeholders"] = [
            {"name": "Julie", "phone": "+33600000001"}
        ]

    assert adapter.find_opportunity_by_phone("+1 555 600000001") is None


def test_no_contact_matches(tmp_path):
    adapter = JsonFileCrmAdapter(JsonDocument(tmp_path / "crm.json"))

    assert adapter.find_opportunity_by_phone("+33699999999") is None


def test_a_stakeholder_without_a_phone_number_is_skipped(tmp_path):
    adapter = JsonFileCrmAdapter(JsonDocument(tmp_path / "crm.json"))
    adapter.update_opportunity("acme-co")
    with adapter._document.update() as data:
        data["opportunities"]["acme-co"]["stakeholders"] = [
            {"name": "Marc", "phone": None},
            {"name": "Julie", "phone": "+33600000001"},
        ]

    assert adapter.find_opportunity_by_phone("+33600000001") == "acme-co"


def test_the_objection_identifier_is_exposed_to_the_model(crm_local):
    """Without the identifier in the context, the agent cannot point at what it is closing."""
    from revenue_agent.application.action_registry import serialise_opportunity

    payload = serialise_opportunity(crm_local.load_opportunity("acme-co"))

    assert payload["objections_ouvertes"][0]["id"] == "ab12cd"


def test_a_domain_stakeholder_is_left_intact():
    assert Stakeholder(name="Julie", phone="+33600000001").phone == "+33600000001"
    assert datetime.now(UTC).tzinfo is UTC
