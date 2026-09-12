"""The handoff must reach a human — the most important property of the product.

A message to the prospect that fails can be retried. A lost handoff is a deal abandoned without
anyone knowing: the agent has stepped back, and the silence looks like success. These tests lock
delivery down.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from revenue_agent.adapters.communication.composite_handoff import CompositeHandoffAdapter
from revenue_agent.config import AgentMode, PolicySettings
from revenue_agent.entrypoints.approval_page import render
from tests.conftest import RecordingWhatsApp, build_opportunity
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


# -- Fan-out ------------------------------------------------------------------------


def test_the_brief_goes_out_to_every_destination():
    crm, teams, console = SpyHandoff("crm"), SpyHandoff("teams"), SpyHandoff("console")

    escalate(CompositeHandoffAdapter([crm, teams], console))

    assert crm.escalations == ["acme-co"]
    assert teams.escalations == ["acme-co"]
    assert console.escalations == [], "the fallback is only used when everything fails"


def test_one_destination_being_down_does_not_stop_the_others():
    """A Teams outage must not take down the CRM task, which is the durable trace."""
    crm, teams, console = SpyHandoff("crm"), SpyHandoff("teams", broken=True), SpyHandoff("console")

    escalate(CompositeHandoffAdapter([crm, teams], console))

    assert crm.escalations == ["acme-co"]
    assert console.escalations == [], "one destination succeeded, the fallback is unnecessary"


def test_if_everything_fails_the_brief_is_not_lost():
    crm = SpyHandoff("crm", broken=True)
    teams = SpyHandoff("teams", broken=True)
    console = SpyHandoff("console")

    escalate(CompositeHandoffAdapter([crm, teams], console))

    assert console.escalations == ["acme-co"], "the brief must survive a total outage"


def test_the_same_guarantee_covers_approval_requests():
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


# -- Holding an action warns a human ------------------------------------------------


def test_holding_an_action_immediately_warns_a_human(crm, scan_state):
    """Without a notification, the approval queue is only read by whoever thinks to read it."""
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))

    harness.registry.send_email("acme-co", "Suivi", "Bonjour Julie")

    assert len(harness.handoff.notifications) == 1
    assert harness.handoff.notifications[0]["channel"] == "email"


def test_a_failing_notification_does_not_lose_the_action(crm, scan_state, monkeypatch):
    """The ping may fail; the action must stay queued, not vanish."""
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))

    def boom(**_kwargs):
        raise RuntimeError("Teams indisponible")

    monkeypatch.setattr(harness.handoff, "notify_pending_approval", boom)

    result = harness.registry.send_email("acme-co", "Suivi", "Bonjour Julie")

    assert len(harness.approvals.submitted) == 1
    assert "EN ATTENTE DE VALIDATION" in result
    assert harness.email.sent == []


# -- Approval page ------------------------------------------------------------------


@pytest.fixture
def pending(crm, scan_state):
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))
    harness.registry.send_email(
        "acme-co", "Relance trimestrielle", "Bonjour Julie, où en êtes-vous ?"
    )
    return harness.approvals.submitted


def test_the_page_shows_the_exact_message_that_will_go_out(pending):
    html = render(list(pending), "jeton", "Exalt")

    assert "Relance trimestrielle" in html
    assert "Bonjour Julie, où en êtes-vous ?" in html


def test_the_page_offers_both_verdicts(pending):
    html = render(list(pending), "jeton", "Exalt")

    assert "Approuver et envoyer" in html
    assert "Rejeter" in html


def test_an_empty_page_says_so_clearly():
    html = render([], "jeton", "Exalt")

    assert "Aucune action en attente" in html


def test_the_message_content_is_escaped(crm, scan_state):
    """A message body is drafted by a model: it must never be injected raw."""
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))
    harness.registry.send_email("acme-co", "Objet", "<script>alert('xss')</script>")

    html = render(list(harness.approvals.submitted), "jeton", "Exalt")

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


# -- WhatsApp: reaching the colleague on the channel they actually read ---------------


def test_whatsapp_handoff_notifies_every_configured_colleague():
    from revenue_agent.adapters.communication.whatsapp_handoff import WhatsAppHandoffAdapter

    whatsapp = RecordingWhatsApp()
    adapter = WhatsAppHandoffAdapter(whatsapp, ("+33600000001", "+33600000002"), lambda _id: "")

    adapter.notify_pending_approval(
        opportunity=build_opportunity(),
        approval_id="ap-1",
        channel="email",
        reason="Engagement commercial",
        preview="Je vous propose une remise.",
    )

    assert [sent["to"] for sent in whatsapp.sent] == ["+33600000001", "+33600000002"]


def test_the_whatsapp_message_carries_the_arbitration_link():
    """Being told without being able to act would only manufacture guilt."""
    from revenue_agent.adapters.communication.whatsapp_handoff import WhatsAppHandoffAdapter

    whatsapp = RecordingWhatsApp()
    adapter = WhatsAppHandoffAdapter(
        whatsapp, ("+33600000001",), lambda approval_id: f"https://demo.test/ui#{approval_id}"
    )

    adapter.notify_pending_approval(
        opportunity=build_opportunity(),
        approval_id="ap-1",
        channel="email",
        reason="Engagement commercial",
        preview="Bonjour",
    )

    assert "https://demo.test/ui#ap-1" in whatsapp.sent[0]["message"]


def test_one_unreachable_colleague_does_not_silence_the_others():
    from revenue_agent.adapters.communication.whatsapp_handoff import WhatsAppHandoffAdapter

    class FlakyWhatsApp:
        def __init__(self):
            self.sent = []

        def send(self, *, to_phone_number, message):
            if to_phone_number.endswith("1"):
                raise RuntimeError("unreachable")
            self.sent.append(to_phone_number)
            return "wa-1"

    whatsapp = FlakyWhatsApp()
    adapter = WhatsAppHandoffAdapter(whatsapp, ("+33600000001", "+33600000002"), lambda _id: "")

    adapter.escalate(
        opportunity=build_opportunity(), reason="x", urgency="haute", context_brief="brief"
    )

    assert whatsapp.sent == ["+33600000002"]


def test_a_total_whatsapp_failure_is_reported_so_the_composite_can_fall_back():
    """Silently swallowing the error would make the composite believe it was delivered."""
    from revenue_agent.adapters.communication.whatsapp_handoff import WhatsAppHandoffAdapter

    class DeadWhatsApp:
        def send(self, *, to_phone_number, message):
            raise RuntimeError("Meta unavailable")

    adapter = WhatsAppHandoffAdapter(DeadWhatsApp(), ("+33600000001",), lambda _id: "")

    with pytest.raises(RuntimeError):
        adapter.escalate(
            opportunity=build_opportunity(), reason="x", urgency="haute", context_brief="brief"
        )
