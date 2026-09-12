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


def test_l_agent_peut_enregistrer_une_posture(harness, crm):
    result = harness.registry.update_stakeholder(
        "acme-co", "Marc Dubois", "decideur", role="Directeur Financier"
    )

    assert crm.stakeholder_updates[0]["stance"] is Stance.DECISION_MAKER
    assert "decideur" in result


def test_une_personne_jamais_contactee_entre_dans_la_carte(harness, crm):
    """Le CFO qui valide le budget compte, même sans coordonnées — c'est souvent lui qui
    décide du sort de l'affaire."""
    harness.registry.update_stakeholder("acme-co", "Marc Dubois", "decideur")

    stakeholders = crm.load_opportunity("acme-co").stakeholders
    marc = next(s for s in stakeholders if s.name == "Marc Dubois")
    assert marc.stance is Stance.DECISION_MAKER
    assert marc.email is None


def test_une_posture_inconnue_est_refusee_explicitement(harness, crm):
    """Une posture fausse corrompt la carte : on le dit au modèle plutôt que de retomber
    silencieusement sur « inconnu »."""
    result = harness.registry.update_stakeholder("acme-co", "Marc Dubois", "sceptique")

    assert "sceptique" in result and "champion" in result
    assert crm.stakeholder_updates == []


def test_la_mise_a_jour_laisse_une_trace_dans_le_crm(harness, crm):
    harness.registry.update_stakeholder("acme-co", "Marc Dubois", "opposant")

    assert any("Marc Dubois" in i.summary for _, i in crm.interactions)


# -- Persistence, local adapter -----------------------------------------------------


def test_le_crm_local_persiste_la_posture(tmp_path):
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
    # Mettre à jour une posture ne doit pas effacer ce qu'on savait déjà.
    assert julie.email == "j@acme.test"
    assert julie.role == "Marketing"


# -- Persistence, HubSpot notes -----------------------------------------------------


def note(body: str, millis: int) -> dict:
    return {"hs_note_body": body, "hs_timestamp": str(millis)}


def test_la_posture_ecrite_en_note_est_relue_au_scan_suivant():
    _, _, stances = _parse_notes(
        {"1": note("[PARTIE-PRENANTE] Julie Martin | posture : champion | Après la démo", 1_700)}
    )

    merged = _merge_stances((Stakeholder(name="Julie Martin", email="j@acme.test"),), stances)

    assert merged[0].stance is Stance.CHAMPION
    assert merged[0].email == "j@acme.test", "la coordonnée HubSpot ne doit pas être perdue"


def test_la_note_la_plus_recente_gagne():
    """Une posture évolue : neutre, puis champion, puis opposant quand le budget est refusé."""
    _, _, stances = _parse_notes(
        {
            "1": note("[PARTIE-PRENANTE] Julie Martin | posture : champion", 2_000),
            "2": note("[PARTIE-PRENANTE] Julie Martin | posture : opposant", 9_000),
        }
    )

    assert stances["julie martin"].stance is Stance.BLOCKER


def test_une_note_de_partie_prenante_n_est_pas_confondue_avec_l_historique():
    _, history, stances = _parse_notes(
        {
            "1": note("[PARTIE-PRENANTE] Marc Dubois | posture : decideur", 1_700),
            "2": note("[INTERACTION] (email) Relance envoyée", 1_700),
        }
    )

    assert len(stances) == 1
    assert len(history) == 1


def test_le_role_et_les_notes_survivent_a_l_aller_retour():
    _, _, stances = _parse_notes(
        {
            "1": note(
                "[PARTIE-PRENANTE] Marc Dubois | rôle : Directeur Financier | "
                "posture : decideur | Valide le budget, jamais contacté",
                1_700,
            )
        }
    )
    marc = _merge_stances((), stances)[0]

    assert marc.role == "Directeur Financier"
    assert "Valide le budget" in marc.notes
    assert marc.stance is Stance.DECISION_MAKER
