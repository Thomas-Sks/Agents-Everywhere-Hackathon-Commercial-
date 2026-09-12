"""Résolution des canaux, routage par enjeu, et garde-fous des actions."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from revenue_agent.adapters.intelligence.lexical_reviewer import LexicalMessageReviewer
from revenue_agent.application.action_registry import ActionRegistry
from revenue_agent.application.run_decision_cycle import RunDecisionCycle
from revenue_agent.config import AgentMode, PolicySettings
from revenue_agent.domain import stakes
from revenue_agent.domain.errors import ChannelUnavailable
from revenue_agent.domain.models import Objection, Stakeholder, Stance
from revenue_agent.domain.triage import TriggerKind
from tests.conftest import (
    InMemoryApprovals,
    RecordingEmail,
    RecordingHandoff,
    RecordingVoice,
    RecordingWhatsApp,
    StubCatalog,
    StubEnrichment,
    build_opportunity,
)


@dataclass
class Harness:
    registry: ActionRegistry
    email: RecordingEmail
    whatsapp: RecordingWhatsApp
    voice: RecordingVoice
    handoff: RecordingHandoff
    approvals: InMemoryApprovals


def build_harness(crm, scan_state, policy_settings: PolicySettings | None = None) -> Harness:
    email, whatsapp = RecordingEmail(), RecordingWhatsApp()
    voice, handoff = RecordingVoice(), RecordingHandoff()
    approvals = InMemoryApprovals()
    return Harness(
        registry=ActionRegistry(
            crm=crm,
            catalog=StubCatalog(),
            email=email,
            whatsapp=whatsapp,
            voice=voice,
            handoff=handoff,
            enrichment=StubEnrichment(),
            scan_state=scan_state,
            approvals=approvals,
            reviewer=LexicalMessageReviewer(),
            # Par défaut les tests d'action tournent en mode autonome : la politique a sa
            # propre suite (test_policy.py), ici on vérifie la mécanique des actions.
            policy_settings=policy_settings or PolicySettings(mode=AgentMode.AUTONOMOUS),
        ),
        email=email,
        whatsapp=whatsapp,
        voice=voice,
        handoff=handoff,
        approvals=approvals,
    )


@pytest.fixture
def harness(crm, scan_state) -> Harness:
    return build_harness(crm, scan_state)


# -- Canaux joignables -------------------------------------------------------------


def test_ladresse_email_vient_du_crm_pas_du_modele(harness):
    harness.registry.send_email("acme-co", "Objet", "Corps")

    assert harness.email.sent[0]["to"] == "julie@acme.example"


def test_canal_indisponible_leve_une_erreur_explicite(crm, harness):
    crm.opportunities["acme-co"] = build_opportunity(email=None)

    with pytest.raises(ChannelUnavailable) as exc:
        harness.registry.send_email("acme-co", "Objet", "Corps")

    assert "email" in str(exc.value)


def test_appel_impossible_sans_numero(crm, harness):
    crm.opportunities["acme-co"] = build_opportunity(phone=None)

    with pytest.raises(ChannelUnavailable):
        harness.registry.place_phone_call("acme-co", "qualifier le budget")


def test_le_champion_est_prioritaire_sur_les_autres_contacts():
    opportunity = replace(
        build_opportunity(),
        stakeholders=(
            Stakeholder(name="Bloqueur", email="non@acme.example", stance=Stance.BLOCKER),
            Stakeholder(name="Championne", email="oui@acme.example", stance=Stance.CHAMPION),
        ),
    )

    assert opportunity.reachable_channels().email == "oui@acme.example"


def test_opportunite_sans_coordonnee_na_aucun_canal():
    opportunity = build_opportunity(email=None, phone=None)

    assert opportunity.reachable_channels().is_empty is True


def test_toute_action_laisse_une_trace_dans_le_crm(crm, harness):
    harness.registry.send_email("acme-co", "Objet", "Corps")

    assert crm.interactions, "une action sans trace CRM n'existe pas pour le commercial humain"


# -- Routage par enjeu -------------------------------------------------------------


def test_montant_eleve_route_vers_le_modele_strategique():
    assert stakes.requires_strategic_reasoning(build_opportunity(amount=120_000)) is True


def test_petit_deal_de_routine_reste_sur_le_modele_economique():
    assert stakes.requires_strategic_reasoning(build_opportunity(amount=3_000)) is False


def test_objection_non_resolue_justifie_le_modele_strategique():
    opportunity = build_opportunity(
        objections=(Objection(text="Trop cher", root_cause="budget"),)
    )

    assert stakes.requires_strategic_reasoning(opportunity) is True


def test_stade_tardif_justifie_le_modele_strategique():
    assert stakes.requires_strategic_reasoning(build_opportunity(stage="negotiation")) is True


def test_changement_de_stade_justifie_le_modele_strategique():
    assert (
        stakes.requires_strategic_reasoning(build_opportunity(), TriggerKind.STAGE_CHANGED) is True
    )


def test_le_motif_de_routage_est_explicable():
    assert "montant" in stakes.explain(build_opportunity(amount=120_000))
    assert stakes.explain(build_opportunity(amount=1_000)) == "décision de routine"


# -- Cycle de décision -------------------------------------------------------------


def test_le_cycle_transmet_lenjeu_a_lagent(crm, scan_state, agent):
    crm.opportunities["acme-co"] = build_opportunity(amount=200_000)
    cycle = RunDecisionCycle(crm=crm, agent=agent, scan_state=scan_state)

    result = cycle.execute("acme-co", "Le prospect demande une remise")

    assert result.strategic is True
    assert agent.calls[0]["strategic"] is True


def test_le_cycle_memorise_le_stade_pour_le_prochain_scan(crm, scan_state, agent):
    RunDecisionCycle(crm=crm, agent=agent, scan_state=scan_state).execute("acme-co", "événement")

    assert scan_state.states["acme-co"].stage == "qualification"
    assert scan_state.states["acme-co"].last_decision_at is not None


def test_execute_safely_absorbe_une_opportunite_inconnue(crm, scan_state, agent):
    cycle = RunDecisionCycle(crm=crm, agent=agent, scan_state=scan_state)

    assert cycle.execute_safely("inexistante", "événement") is None


# -- La politique est réellement appliquée, pas seulement calculée -------------------


def test_en_mode_supervise_aucun_email_natteint_le_fournisseur(crm, scan_state):
    """Le test décisif : la politique doit empêcher l'envoi, pas seulement le déconseiller."""
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))

    result = harness.registry.send_email("acme-co", "Suivi", "Bonjour Julie")

    assert harness.email.sent == [], "l'email ne doit jamais atteindre l'adapter"
    assert len(harness.approvals.submitted) == 1
    assert "EN ATTENTE DE VALIDATION" in result


def test_une_remise_est_retenue_meme_en_mode_autonome(crm, scan_state):
    harness = build_harness(crm, scan_state)

    result = harness.registry.send_email("acme-co", "Offre", "Je vous propose une remise de 15 %.")

    assert harness.email.sent == []
    assert harness.approvals.submitted[0].rule.startswith("relecture_")
    assert "EN ATTENTE DE VALIDATION" in result


def test_un_prix_hallucine_est_bloque_sans_creer_de_demande(crm, scan_state):
    """Un prix inventé n'est pas un arbitrage commercial : il n'y a rien à approuver."""
    harness = build_harness(crm, scan_state)
    harness.registry._catalog.products.clear()

    result = harness.registry.send_email("acme-co", "Tarif", "Ce sera 3 200 € par an.")

    assert harness.email.sent == []
    assert harness.approvals.submitted == []
    assert "BLOQUÉE" in result


def test_apres_validation_humaine_le_message_part_reellement(crm, scan_state):
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))
    harness.registry.send_email("acme-co", "Suivi", "Bonjour Julie")
    approval = harness.approvals.submitted[0]

    harness.registry.execute_approved(approval)

    assert len(harness.email.sent) == 1
    assert harness.email.sent[0]["subject"] == "Suivi"


def test_un_appel_telephonique_est_soumis_a_la_meme_politique(crm, scan_state):
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))

    harness.registry.place_phone_call("acme-co", "qualifier le budget")

    assert harness.voice.calls == []
    assert len(harness.approvals.submitted) == 1


def test_la_cadence_bloque_le_enieme_message(crm, scan_state):
    harness = build_harness(
        crm, scan_state, PolicySettings(mode=AgentMode.AUTONOMOUS, max_outbound_per_day=2)
    )
    harness.approvals.sent["acme-co"] = 2

    result = harness.registry.send_email("acme-co", "Relance", "Un petit rappel")

    assert harness.email.sent == []
    assert "BLOQUÉE" in result
