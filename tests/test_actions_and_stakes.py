"""Channel resolution, stakes-based routing, and the guardrails around actions."""

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
            # By default the action tests run in autonomous mode: the policy has its own suite
            # (test_policy.py), here we verify the mechanics of the actions themselves.
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


# -- Reachable channels ------------------------------------------------------------


def test_the_email_address_comes_from_the_crm_not_the_model(harness):
    harness.registry.send_email("acme-co", "Subject", "Body")

    assert harness.email.sent[0]["to"] == "julie@acme.example"


def test_an_unavailable_channel_raises_an_explicit_error(crm, harness):
    crm.opportunities["acme-co"] = build_opportunity(email=None)

    with pytest.raises(ChannelUnavailable) as exc:
        harness.registry.send_email("acme-co", "Subject", "Body")

    assert "email" in str(exc.value)


def test_a_call_is_impossible_without_a_phone_number(crm, harness):
    crm.opportunities["acme-co"] = build_opportunity(phone=None)

    with pytest.raises(ChannelUnavailable):
        harness.registry.place_phone_call("acme-co", "qualify the budget")


def test_the_champion_takes_priority_over_other_contacts():
    opportunity = replace(
        build_opportunity(),
        stakeholders=(
            Stakeholder(name="Blocker", email="non@acme.example", stance=Stance.BLOCKER),
            Stakeholder(name="Champion", email="oui@acme.example", stance=Stance.CHAMPION),
        ),
    )

    assert opportunity.reachable_channels().email == "oui@acme.example"


def test_an_opportunity_without_contact_details_has_no_channel():
    opportunity = build_opportunity(email=None, phone=None)

    assert opportunity.reachable_channels().is_empty is True


def test_every_action_leaves_a_trace_in_the_crm(crm, harness):
    harness.registry.send_email("acme-co", "Subject", "Body")

    assert crm.interactions, "an action with no CRM trace does not exist for the human rep"


# -- Stakes-based routing ----------------------------------------------------------


def test_a_large_amount_routes_to_the_strategic_model():
    assert stakes.requires_strategic_reasoning(build_opportunity(amount=120_000)) is True


def test_a_small_routine_deal_stays_on_the_economy_tier():
    assert stakes.requires_strategic_reasoning(build_opportunity(amount=3_000)) is False


def test_an_unresolved_objection_justifies_the_strategic_model():
    opportunity = build_opportunity(
        objections=(Objection(text="Too expensive", root_cause="budget"),)
    )

    assert stakes.requires_strategic_reasoning(opportunity) is True


def test_a_late_stage_justifies_the_strategic_model():
    assert stakes.requires_strategic_reasoning(build_opportunity(stage="negotiation")) is True


def test_a_stage_change_justifies_the_strategic_model():
    assert (
        stakes.requires_strategic_reasoning(build_opportunity(), TriggerKind.STAGE_CHANGED) is True
    )


def test_the_routing_rationale_is_explainable():
    assert "amount" in stakes.explain(build_opportunity(amount=120_000))
    assert stakes.explain(build_opportunity(amount=1_000)) == "routine decision"


# -- Decision cycle ----------------------------------------------------------------


def test_the_cycle_passes_the_stakes_to_the_agent(crm, scan_state, agent):
    crm.opportunities["acme-co"] = build_opportunity(amount=200_000)
    cycle = RunDecisionCycle(crm=crm, agent=agent, scan_state=scan_state)

    result = cycle.execute("acme-co", "The prospect is asking for a discount")

    assert result.strategic is True
    assert agent.calls[0]["strategic"] is True


def test_the_cycle_records_the_stage_for_the_next_scan(crm, scan_state, agent):
    RunDecisionCycle(crm=crm, agent=agent, scan_state=scan_state).execute("acme-co", "event")

    assert scan_state.states["acme-co"].stage == "qualification"
    assert scan_state.states["acme-co"].last_decision_at is not None


def test_execute_safely_absorbs_an_unknown_opportunity(crm, scan_state, agent):
    cycle = RunDecisionCycle(crm=crm, agent=agent, scan_state=scan_state)

    assert cycle.execute_safely("inexistante", "event") is None


# -- The policy is actually enforced, not merely computed ---------------------------


def test_in_supervised_mode_no_email_reaches_the_provider(crm, scan_state):
    """The decisive test: the policy must prevent the send, not merely advise against it."""
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))

    result = harness.registry.send_email("acme-co", "Follow-up", "Hello Julie")

    assert harness.email.sent == [], "the email must never reach the adapter"
    assert len(harness.approvals.submitted) == 1
    assert "AWAITING APPROVAL" in result


def test_a_discount_is_held_even_in_autonomous_mode(crm, scan_state):
    harness = build_harness(crm, scan_state)

    result = harness.registry.send_email("acme-co", "Offer", "I can offer you a 15% discount.")

    assert harness.email.sent == []
    assert harness.approvals.submitted[0].rule.startswith("review_")
    assert "AWAITING APPROVAL" in result


def test_a_hallucinated_price_is_blocked_without_creating_a_request(crm, scan_state):
    """An invented price is not a commercial judgement call: there is nothing to approve."""
    harness = build_harness(crm, scan_state)
    harness.registry._catalog.products.clear()

    result = harness.registry.send_email("acme-co", "Pricing", "That will be 3,200 EUR per year.")

    assert harness.email.sent == []
    assert harness.approvals.submitted == []
    assert "BLOCKED" in result


def test_after_human_approval_the_message_actually_goes_out(crm, scan_state):
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))
    harness.registry.send_email("acme-co", "Follow-up", "Hello Julie")
    approval = harness.approvals.submitted[0]

    harness.registry.execute_approved(approval)

    assert len(harness.email.sent) == 1
    assert harness.email.sent[0]["subject"] == "Follow-up"


def test_a_phone_call_is_subject_to_the_same_policy(crm, scan_state):
    harness = build_harness(crm, scan_state, PolicySettings(mode=AgentMode.SUPERVISED))

    harness.registry.place_phone_call("acme-co", "qualify the budget")

    assert harness.voice.calls == []
    assert len(harness.approvals.submitted) == 1


def test_the_cadence_blocks_the_nth_message(crm, scan_state):
    harness = build_harness(
        crm, scan_state, PolicySettings(mode=AgentMode.AUTONOMOUS, max_outbound_per_day=2)
    )
    harness.approvals.sent["acme-co"] = 2

    result = harness.registry.send_email("acme-co", "Relance", "A quick reminder")

    assert harness.email.sent == []
    assert "BLOCKED" in result
