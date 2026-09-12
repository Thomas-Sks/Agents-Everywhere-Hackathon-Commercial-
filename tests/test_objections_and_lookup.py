"""Cycle de vie des objections, et résolution d'une opportunité par numéro.

Deux défauts que ces tests verrouillent :

1. Les objections n'avaient aucun chemin d'écriture vers `resolved=True`. Elles
   s'accumulaient donc indéfiniment, ce qui épinglait le routage par enjeu sur le modèle le
   plus cher (`stakes` escalade dès qu'une objection est ouverte) et polluait le contexte à
   chaque cycle.
2. La résolution d'une opportunité depuis un numéro entrant balayait le portefeuille — une
   requête par opportunité — et rapprochait sur les derniers chiffres, donc lentement et avec
   un risque de faux positif.
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


# -- Cycle de vie d'une objection ---------------------------------------------------


def test_une_objection_nait_ouverte(crm_local):
    opportunity = crm_local.load_opportunity("acme-co")

    assert len(opportunity.unresolved_objections()) == 1


def test_une_objection_resolue_sort_des_objections_ouvertes(crm_local):
    assert crm_local.resolve_objection("acme-co", "ab12cd", "Budget débloqué par le CFO") is True

    opportunity = crm_local.load_opportunity("acme-co")
    assert opportunity.unresolved_objections() == ()
    assert opportunity.objections[0].resolution == "Budget débloqué par le CFO"


def test_resoudre_une_objection_inconnue_echoue_sans_planter(crm_local):
    assert crm_local.resolve_objection("acme-co", "inexistante", "peu importe") is False
    assert len(crm_local.load_opportunity("acme-co").unresolved_objections()) == 1


def test_resoudre_libere_le_routage_economique():
    """La conséquence économique du défaut : sans résolution, tout passait par le gros modèle."""
    ouverte = Opportunity(
        id="acme-co",
        company="Acme",
        amount=1_000,
        objections=(Objection(id="x1", text="Trop cher", root_cause="budget"),),
    )
    resolue = Opportunity(
        id="acme-co",
        company="Acme",
        amount=1_000,
        objections=(
            Objection(id="x1", text="Trop cher", root_cause="budget", resolved=True),
        ),
    )

    assert stakes.requires_strategic_reasoning(ouverte) is True
    assert stakes.requires_strategic_reasoning(resolue) is False


# -- Relecture des notes HubSpot ----------------------------------------------------


def note(body: str, moment: str = "1757000000000") -> dict:
    return {"hs_note_body": body, "hs_timestamp": moment}


def test_les_notes_hubspot_portent_lidentifiant_de_lobjection():
    objections, _ = _parse_notes(
        {"1": note("[OBJECTION:ab12cd] Trop cher | cause probable : budget")}
    )

    assert objections[0].id == "ab12cd"
    assert objections[0].resolved is False


def test_une_note_de_resolution_ferme_lobjection_correspondante():
    objections, _ = _parse_notes(
        {
            "1": note("[OBJECTION:ab12cd] Trop cher | cause probable : budget"),
            "2": note("[OBJECTION-RESOLUE:ab12cd] Budget débloqué"),
        }
    )

    assert objections[0].resolved is True
    assert objections[0].resolution == "Budget débloqué"


def test_la_resolution_fonctionne_meme_si_la_note_arrive_avant():
    """L'API ne garantit pas l'ordre des notes : la résolution doit être collectée d'abord."""
    objections, _ = _parse_notes(
        {
            "1": note("[OBJECTION-RESOLUE:ab12cd] Budget débloqué"),
            "2": note("[OBJECTION:ab12cd] Trop cher | cause probable : budget"),
        }
    )

    assert objections[0].resolved is True


def test_une_resolution_ne_ferme_que_lobjection_visee():
    objections, _ = _parse_notes(
        {
            "1": note("[OBJECTION:aaa] Trop cher | cause probable : budget"),
            "2": note("[OBJECTION:bbb] Mauvais timing | cause probable : contrat"),
            "3": note("[OBJECTION-RESOLUE:aaa] Budget débloqué"),
        }
    )

    par_id = {objection.id: objection for objection in objections}
    assert par_id["aaa"].resolved is True
    assert par_id["bbb"].resolved is False


def test_une_note_de_resolution_nest_pas_lue_comme_un_historique():
    objections, history = _parse_notes({"1": note("[OBJECTION-RESOLUE:aaa] Réglé")})

    assert objections == ()
    assert history == (), "une résolution ne doit pas polluer l'historique des interactions"


# -- Résolution par numéro de téléphone ---------------------------------------------


def test_le_numero_est_recherche_sous_ses_ecritures_plausibles():
    variants = _phone_variants("+33 6 00 00 00 01")

    assert "33600000001" in variants
    assert "+33600000001" in variants
    assert "0600000001" in variants, "un CRM contient aussi la forme nationale"


def test_un_numero_trop_court_nest_pas_recherche():
    assert _phone_variants("1234") == []


def test_resolution_locale_par_numero(tmp_path):
    adapter = JsonFileCrmAdapter(JsonDocument(tmp_path / "crm.json"))
    adapter.update_opportunity("acme-co")
    with adapter._document.update() as data:
        data["opportunities"]["acme-co"]["stakeholders"] = [
            {"name": "Julie", "phone": "+33600000001"}
        ]

    assert adapter.find_opportunity_by_phone("33600000001") == "acme-co"
    assert adapter.find_opportunity_by_phone("0600000001") == "acme-co"


def test_un_suffixe_commun_ne_produit_pas_de_faux_positif(tmp_path):
    """Le défaut d'origine rapprochait sur neuf chiffres : deux prospects pouvaient coïncider."""
    adapter = JsonFileCrmAdapter(JsonDocument(tmp_path / "crm.json"))
    adapter.update_opportunity("acme-co")
    with adapter._document.update() as data:
        data["opportunities"]["acme-co"]["stakeholders"] = [
            {"name": "Julie", "phone": "+33600000001"}
        ]

    assert adapter.find_opportunity_by_phone("+1 555 600000001") is None


def test_aucun_contact_ne_correspond(tmp_path):
    adapter = JsonFileCrmAdapter(JsonDocument(tmp_path / "crm.json"))

    assert adapter.find_opportunity_by_phone("+33699999999") is None


def test_stakeholder_sans_telephone_est_ignore(tmp_path):
    adapter = JsonFileCrmAdapter(JsonDocument(tmp_path / "crm.json"))
    adapter.update_opportunity("acme-co")
    with adapter._document.update() as data:
        data["opportunities"]["acme-co"]["stakeholders"] = [
            {"name": "Marc", "phone": None},
            {"name": "Julie", "phone": "+33600000001"},
        ]

    assert adapter.find_opportunity_by_phone("+33600000001") == "acme-co"


def test_lidentifiant_dobjection_est_expose_au_modele(crm_local):
    """Sans l'identifiant dans le contexte, l'agent ne peut pas désigner ce qu'il referme."""
    from revenue_agent.application.action_registry import serialise_opportunity

    payload = serialise_opportunity(crm_local.load_opportunity("acme-co"))

    assert payload["objections_ouvertes"][0]["id"] == "ab12cd"


def test_un_stakeholder_du_domaine_reste_intact():
    assert Stakeholder(name="Julie", phone="+33600000001").phone == "+33600000001"
    assert datetime.now(UTC).tzinfo is UTC
