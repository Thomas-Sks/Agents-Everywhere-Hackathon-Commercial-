"""Le handoff doit atteindre un humain — la propriété la plus importante du produit.

Un message au prospect qui échoue peut être réessayé. Un handoff perdu est un deal abandonné
sans que personne ne le sache : l'agent s'est retiré, et le silence ressemble à un succès.
Ces tests verrouillent la remise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from revenue_agent.adapters.communication.composite_handoff import CompositeHandoffAdapter
from revenue_agent.config import AgentMode, PolicySettings
from revenue_agent.entrypoints.approval_page import render
from tests.conftest import build_opportunity
from tests.test_actions_and_stakes import build_harness


@dataclass
class SpyHandoff:
    name: str = "spy"
    escalations: list = field(default_factory=list)
    notifications: list = field(default_factory=list)
    broken: bool = False

    def escalate(self, *, opportunity, reason, urgency, context_brief):
        if self.broken:
            raise RuntimeError(f"{self.name} indisponible")
        self.escalations.append(opportunity.id)

    def notify_pending_approval(self, *, opportunity, approval_id, channel, reason, preview):
        if self.broken:
            raise RuntimeError(f"{self.name} indisponible")
        self.notifications.append(approval_id)


def escalate(composite: CompositeHandoffAdapter) -> None:
    composite.escalate(
        opportunity=build_opportunity(),
        reason="Négociation au-delà de l'autonomie",
        urgency="haute",
        context_brief="Julie, directrice marketing. Budget validé par le CFO.",
    )


# -- Diffusion ----------------------------------------------------------------------


def test_le_brief_part_vers_toutes_les_destinations():
    crm, teams, console = SpyHandoff("crm"), SpyHandoff("teams"), SpyHandoff("console")

    escalate(CompositeHandoffAdapter([crm, teams], console))

    assert crm.escalations == ["acme-co"]
    assert teams.escalations == ["acme-co"]
    assert console.escalations == [], "le repli ne sert que si tout échoue"


def test_une_destination_en_panne_nempeche_pas_les_autres():
    """Une coupure Teams ne doit pas emporter la tâche CRM, qui est la trace durable."""
    crm, teams, console = SpyHandoff("crm"), SpyHandoff("teams", broken=True), SpyHandoff("console")

    escalate(CompositeHandoffAdapter([crm, teams], console))

    assert crm.escalations == ["acme-co"]
    assert console.escalations == [], "une destination a réussi, le repli est inutile"


def test_si_tout_echoue_le_brief_nest_pas_perdu():
    crm = SpyHandoff("crm", broken=True)
    teams = SpyHandoff("teams", broken=True)
    console = SpyHandoff("console")

    escalate(CompositeHandoffAdapter([crm, teams], console))

    assert console.escalations == ["acme-co"], "le brief doit survivre à la panne générale"


def test_la_meme_garantie_couvre_les_demandes_de_validation():
    console = SpyHandoff("console")
    composite = CompositeHandoffAdapter([SpyHandoff("teams", broken=True)], console)

    composite.notify_pending_approval(
        opportunity=build_opportunity(),
        approval_id="ap-1",
        channel="email",
        reason="Engagement commercial",
        preview="Je vous propose une remise.",
    )

    assert console.notifications == ["ap-1"]


# -- Une action retenue prévient un humain -------------------------------------------


def test_mettre_en_attente_previent_immediatement_un_humain(crm, scan_state):
    """Sans notification, la file de validation n'est consultée que par qui y pense."""
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))

    harness.registry.send_email("acme-co", "Suivi", "Bonjour Julie")

    assert len(harness.handoff.notifications) == 1
    assert harness.handoff.notifications[0]["channel"] == "email"


def test_une_notification_en_panne_ne_perd_pas_laction(crm, scan_state, monkeypatch):
    """Le ping peut échouer ; l'action doit rester en file, pas disparaître."""
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))

    def boom(**_kwargs):
        raise RuntimeError("Teams indisponible")

    monkeypatch.setattr(harness.handoff, "notify_pending_approval", boom)

    result = harness.registry.send_email("acme-co", "Suivi", "Bonjour Julie")

    assert len(harness.approvals.submitted) == 1
    assert "EN ATTENTE DE VALIDATION" in result
    assert harness.email.sent == []


# -- Page d'arbitrage ---------------------------------------------------------------


@pytest.fixture
def pending(crm, scan_state):
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))
    harness.registry.send_email(
        "acme-co", "Relance trimestrielle", "Bonjour Julie, où en êtes-vous ?"
    )
    return harness.approvals.submitted


def test_la_page_montre_le_message_exact_qui_partira(pending):
    html = render(list(pending), "jeton", "Exalt")

    assert "Relance trimestrielle" in html
    assert "Bonjour Julie, où en êtes-vous ?" in html


def test_la_page_propose_les_deux_arbitrages(pending):
    html = render(list(pending), "jeton", "Exalt")

    assert "Approuver et envoyer" in html
    assert "Rejeter" in html


def test_la_page_vide_le_dit_clairement():
    html = render([], "jeton", "Exalt")

    assert "Aucune action en attente" in html


def test_le_contenu_du_message_est_echappe(crm, scan_state):
    """Le corps d'un message est rédigé par un modèle : il ne doit jamais être injecté brut."""
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))
    harness.registry.send_email("acme-co", "Objet", "<script>alert('xss')</script>")

    html = render(list(harness.approvals.submitted), "jeton", "Exalt")

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
