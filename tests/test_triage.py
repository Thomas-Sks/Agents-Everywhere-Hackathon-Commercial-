"""Le triage décide qui consomme un cycle LLM — ses règles méritent d'être verrouillées."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from revenue_agent.domain.models import DealSnapshot
from revenue_agent.domain.triage import KnownDealState, TriggerKind, select_triggers

NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


def snapshot(deal_id: str = "1", stage: str = "qualification") -> DealSnapshot:
    return DealSnapshot(id=deal_id, name="Acme Co", stage=stage, last_modified_at=NOW)


def test_deal_inconnu_est_un_nouveau_lead():
    triggers = select_triggers([snapshot()], {}, NOW, inactivity_days=14)

    assert len(triggers) == 1
    assert triggers[0].kind is TriggerKind.NEW_LEAD


def test_changement_de_stade_detecte():
    known = {"1": KnownDealState(stage="discovery")}

    triggers = select_triggers([snapshot(stage="negotiation")], known, NOW, inactivity_days=14)

    assert triggers[0].kind is TriggerKind.STAGE_CHANGED
    assert "discovery" in triggers[0].reason and "negotiation" in triggers[0].reason


def test_deal_modifie_sans_changement_de_stade_est_ignore():
    """Une modification quelconque dans le CRM ne justifie pas de faire raisonner un modèle."""
    known = {"1": KnownDealState(stage="qualification")}

    assert select_triggers([snapshot()], known, NOW, inactivity_days=14) == []


def test_relance_programmee_arrivee_a_echeance():
    known = {"1": KnownDealState(stage="qualification", follow_up_due_at=NOW - timedelta(hours=1))}

    triggers = select_triggers([], known, NOW, inactivity_days=14)

    assert triggers[0].kind is TriggerKind.SCHEDULED_FOLLOW_UP


def test_relance_future_non_declenchee():
    known = {"1": KnownDealState(stage="qualification", follow_up_due_at=NOW + timedelta(days=30))}

    assert select_triggers([], known, NOW, inactivity_days=14) == []


def test_inactivite_prolongee_reveille_lopportunite():
    known = {"1": KnownDealState(stage="qualification", last_decision_at=NOW - timedelta(days=20))}

    triggers = select_triggers([], known, NOW, inactivity_days=14)

    assert triggers[0].kind is TriggerKind.INACTIVITY
    assert "20 jours" in triggers[0].reason


def test_inactivite_sous_le_seuil_ignoree():
    known = {"1": KnownDealState(stage="qualification", last_decision_at=NOW - timedelta(days=3))}

    assert select_triggers([], known, NOW, inactivity_days=14) == []


def test_un_seul_declencheur_par_opportunite_le_plus_prioritaire():
    """Signal frais (le prospect a bougé) prime sur signal d'absence (rien n'a bougé)."""
    known = {
        "1": KnownDealState(
            stage="discovery",
            last_decision_at=NOW - timedelta(days=90),
            follow_up_due_at=NOW - timedelta(days=1),
        )
    }

    triggers = select_triggers([snapshot(stage="negotiation")], known, NOW, inactivity_days=14)

    assert len(triggers) == 1
    assert triggers[0].kind is TriggerKind.STAGE_CHANGED


def test_resultats_tries_par_priorite():
    snapshots = [snapshot("1", stage="negotiation"), snapshot("2")]
    known = {"1": KnownDealState(stage="discovery")}

    triggers = select_triggers(snapshots, known, NOW, inactivity_days=14)

    assert [t.kind for t in triggers] == [TriggerKind.STAGE_CHANGED, TriggerKind.NEW_LEAD]
