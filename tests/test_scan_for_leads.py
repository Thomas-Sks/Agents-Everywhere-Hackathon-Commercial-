"""Boucle de scan : budget, fenêtre de recouvrement, résistance aux pannes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from revenue_agent.application.run_decision_cycle import RunDecisionCycle
from revenue_agent.application.scan_for_leads import ScanForLeads
from revenue_agent.config import ScanSettings
from revenue_agent.domain.models import DealSnapshot
from revenue_agent.domain.triage import KnownDealState
from tests.conftest import build_opportunity

NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


def build_scanner(crm, scan_state, agent, *, max_decisions: int = 5) -> ScanForLeads:
    return ScanForLeads(
        crm=crm,
        scan_state=scan_state,
        decision_cycle=RunDecisionCycle(crm=crm, agent=agent, scan_state=scan_state),
        settings=ScanSettings(max_decisions_per_scan=max_decisions),
    )


def mark_already_running(scan_state) -> None:
    """Place le scanner en régime établi : le premier scan a une sémantique différente
    (inventaire du portefeuille), couverte par ses propres tests plus bas."""
    scan_state.pointer = NOW - timedelta(minutes=10)


def test_nouveau_lead_declenche_un_cycle_de_decision(crm, scan_state, agent):
    mark_already_running(scan_state)
    crm.snapshots = [
        DealSnapshot(id="acme-co", name="Acme Co", stage="qualification", last_modified_at=NOW)
    ]

    report = build_scanner(crm, scan_state, agent).execute()

    assert report.detected == 1
    assert len(agent.calls) == 1
    assert agent.calls[0]["opportunity_id"] == "acme-co"


def test_le_budget_par_scan_est_respecte_et_le_surplus_reporte(crm, scan_state, agent):
    mark_already_running(scan_state)
    for index in range(4):
        opportunity_id = f"deal-{index}"
        crm.opportunities[opportunity_id] = build_opportunity(opportunity_id)
        crm.snapshots.append(
            DealSnapshot(
                id=opportunity_id, name="Acme", stage="qualification", last_modified_at=NOW
            )
        )

    report = build_scanner(crm, scan_state, agent, max_decisions=2).execute()

    assert report.detected == 4
    assert len(report.processed) == 2
    assert report.deferred == 2
    assert len(agent.calls) == 2


def test_le_pointeur_navance_pas_si_le_crm_est_indisponible(crm, scan_state, agent):
    """Sinon la fenêtre manquée serait considérée comme traitée, et les leads perdus."""
    crm.fail_on_search = True
    scan_state.pointer = NOW - timedelta(hours=1)

    report = build_scanner(crm, scan_state, agent).execute()

    assert report.crm_available is False
    assert scan_state.pointer == NOW - timedelta(hours=1)


def test_le_pointeur_avance_apres_un_scan_reussi(crm, scan_state, agent):
    build_scanner(crm, scan_state, agent).execute()

    assert scan_state.pointer is not None


def test_une_opportunite_en_echec_ninterrompt_pas_les_suivantes(crm, scan_state, agent):
    """Un deal absent du CRM ne doit pas arrêter la boucle autonome."""
    mark_already_running(scan_state)
    crm.snapshots = [
        DealSnapshot(id="fantome", name="Inconnu", stage="x", last_modified_at=NOW),
        DealSnapshot(id="acme-co", name="Acme Co", stage="qualification", last_modified_at=NOW),
    ]

    report = build_scanner(crm, scan_state, agent).execute()

    assert report.detected == 2
    assert [item.opportunity_id for item in report.processed] == ["acme-co"]


def test_le_premier_scan_adopte_le_portefeuille_sans_relancer_les_deals_actifs(
    crm, scan_state, agent
):
    """Jour 1 : on n'écrit pas à 500 prospects parce qu'on vient d'installer l'agent."""
    crm.snapshots = [
        DealSnapshot(
            id="acme-co",
            name="Acme Co",
            stage="qualification",
            last_modified_at=NOW - timedelta(days=2),
        )
    ]

    report = build_scanner(crm, scan_state, agent).execute()

    assert report.detected == 0
    assert scan_state.states["acme-co"].stage == "qualification"


def test_le_premier_scan_reveille_immediatement_les_deals_dormants(crm, scan_state, agent):
    """L'inverse du test précédent : un deal oublié depuis des mois est la valeur du jour 1."""
    crm.snapshots = [
        DealSnapshot(
            id="acme-co",
            name="Acme Co",
            stage="qualification",
            last_modified_at=NOW - timedelta(days=60),
        )
    ]

    report = build_scanner(crm, scan_state, agent).execute()

    assert report.detected == 1
    assert report.processed[0].trigger == "inactivite"


def test_la_relance_programmee_est_consommee_apres_traitement(crm, scan_state, agent):
    scan_state.states["acme-co"] = KnownDealState(
        stage="qualification", follow_up_due_at=NOW - timedelta(days=1)
    )

    build_scanner(crm, scan_state, agent).execute()

    assert scan_state.states["acme-co"].follow_up_due_at is None
