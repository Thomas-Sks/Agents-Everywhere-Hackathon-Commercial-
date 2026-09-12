"""The stakeholder map must be writable, not just readable.

A complex sale rarely fails on the product alone. The prompt asks the agent to keep track of
who decides, who influences and who blocks — these tests lock in that it actually *can*, and
that what it records survives to the next cycle.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from revenue_agent.adapters.crm.hubspot import _merge_stances, _parse_notes
from revenue_agent.adapters.crm.json_file import JsonFileCrmAdapter
from revenue_agent.adapters.storage.json_document import JsonDocument
from revenue_agent.domain.models import Stakeholder, Stance
from tests.test_actions_and_stakes import build_harness

NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


@pytest.fixture
def harness(crm, scan_state):
    return build_harness(crm, scan_state)


# -- The action ---------------------------------------------------------------------


def test_the_agent_can_record_a_stance(harness, crm):
    result = harness.registry.update_stakeholder(
        "acme-co", "Marc Dubois", "decision_maker", role="CFO"
    )

    assert crm.stakeholder_updates[0]["stance"] is Stance.DECISION_MAKER
    assert "decision_maker" in result


def test_a_never_contacted_person_enters_the_map(harness, crm):
    """The CFO who signs off the budget counts, even without contact details — c'est souvent lui qui
    décide du sort de l'affaire."""
    harness.registry.update_stakeholder("acme-co", "Marc Dubois", "decision_maker")

    stakeholders = crm.load_opportunity("acme-co").stakeholders
    marc = next(s for s in stakeholders if s.name == "Marc Dubois")
    assert marc.stance is Stance.DECISION_MAKER
    assert marc.email is None


def test_an_unknown_stance_is_refused_explicitly(harness, crm):
    """A wrong stance corrupts the map: we tell the model rather than falling back
    silencieusement sur « inconnu »."""
    result = harness.registry.update_stakeholder("acme-co", "Marc Dubois", "sceptique")

    assert "sceptique" in result and "champion" in result
    assert crm.stakeholder_updates == []


def test_the_update_leaves_a_trace_in_the_crm(harness, crm):
    harness.registry.update_stakeholder("acme-co", "Marc Dubois", "blocker")

    assert any("Marc Dubois" in i.summary for _, i in crm.interactions)


# -- Persistence, local adapter -----------------------------------------------------


def test_the_local_crm_persists_the_stance(tmp_path):
    document = JsonDocument(tmp_path / "crm.json")
    document.write(
        {
            "opportunities": {
                "acme-co": {
                    "company": "Acme Co",
                    "stakeholders": [
                        {"name": "Julie Martin", "role": "Marketing", "email": "j@acme.test"}
                    ],
                }
            }
        }
    )
    adapter = JsonFileCrmAdapter(document)

    adapter.update_stakeholder("acme-co", name="Julie Martin", stance=Stance.CHAMPION)

    julie = adapter.load_opportunity("acme-co").stakeholders[0]
    assert julie.stance is Stance.CHAMPION
    # Updating a stance must not erase what we already knew.
    assert julie.email == "j@acme.test"
    assert julie.role == "Marketing"


# -- Persistence, HubSpot notes -----------------------------------------------------


def note(body: str, millis: int) -> dict:
    return {"hs_note_body": body, "hs_timestamp": str(millis)}


def test_a_stance_written_as_a_note_is_read_back_on_the_next_scan():
    _, _, stances = _parse_notes(
        {"1": note("[STAKEHOLDER] Julie Martin | stance: champion | After the demo", 1_700)}
    )

    merged = _merge_stances((Stakeholder(name="Julie Martin", email="j@acme.test"),), stances)

    assert merged[0].stance is Stance.CHAMPION
    assert merged[0].email == "j@acme.test", "the HubSpot contact detail must not be lost"


def test_the_most_recent_note_wins():
    """A stance moves: neutral, then champion, then blocker once the budget is refused."""
    _, _, stances = _parse_notes(
        {
            "1": note("[STAKEHOLDER] Julie Martin | stance: champion", 2_000),
            "2": note("[STAKEHOLDER] Julie Martin | stance: blocker", 9_000),
        }
    )

    assert stances["julie martin"].stance is Stance.BLOCKER


def test_a_stakeholder_note_is_not_mistaken_for_history():
    _, history, stances = _parse_notes(
        {
            "1": note("[STAKEHOLDER] Marc Dubois | stance: decision_maker", 1_700),
            "2": note("[INTERACTION] (email) Relance envoyée", 1_700),
        }
    )

    assert len(stances) == 1
    assert len(history) == 1


def test_role_and_notes_survive_the_round_trip():
    _, _, stances = _parse_notes(
        {
            "1": note(
                "[STAKEHOLDER] Marc Dubois | role: CFO | "
                "stance: decision_maker | Signs off the budget, never contacted",
                1_700,
            )
        }
    )
    marc = _merge_stances((), stances)[0]

    assert marc.role == "CFO"
    assert "Signs off the budget" in marc.notes
    assert marc.stance is Stance.DECISION_MAKER
