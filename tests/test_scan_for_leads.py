"""Scan loop: budget, overlap window, resilience to outages."""

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
    """Put the scanner into steady state: the very first scan has different semantics
    (taking inventory of the book of business), covered by its own tests further down."""
    scan_state.pointer = NOW - timedelta(minutes=10)


def test_a_new_lead_triggers_a_decision_cycle(crm, scan_state, agent):
    mark_already_running(scan_state)
    crm.snapshots = [
        DealSnapshot(id="acme-co", name="Acme Co", stage="qualification", last_modified_at=NOW)
    ]

    report = build_scanner(crm, scan_state, agent).execute()

    assert report.detected == 1
    assert len(agent.calls) == 1
    assert agent.calls[0]["opportunity_id"] == "acme-co"


def test_the_per_scan_budget_is_respected_and_the_surplus_deferred(crm, scan_state, agent):
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


def test_the_pointer_does_not_advance_when_the_crm_is_unavailable(crm, scan_state, agent):
    """Otherwise the missed window would be treated as processed, and the leads lost."""
    crm.fail_on_search = True
    scan_state.pointer = NOW - timedelta(hours=1)

    report = build_scanner(crm, scan_state, agent).execute()

    assert report.crm_available is False
    assert scan_state.pointer == NOW - timedelta(hours=1)


def test_the_pointer_advances_after_a_successful_scan(crm, scan_state, agent):
    build_scanner(crm, scan_state, agent).execute()

    assert scan_state.pointer is not None


def test_one_failing_opportunity_does_not_interrupt_the_following_ones(crm, scan_state, agent):
    """A deal missing from the CRM must not stop the autonomous loop."""
    mark_already_running(scan_state)
    crm.snapshots = [
        DealSnapshot(id="fantome", name="Inconnu", stage="x", last_modified_at=NOW),
        DealSnapshot(id="acme-co", name="Acme Co", stage="qualification", last_modified_at=NOW),
    ]

    report = build_scanner(crm, scan_state, agent).execute()

    assert report.detected == 2
    assert [item.opportunity_id for item in report.processed] == ["acme-co"]


def test_the_first_scan_adopts_the_book_without_re_engaging_active_deals(
    crm, scan_state, agent
):
    """Day 1: you do not write to 500 prospects just because the agent was installed."""
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


def test_the_first_scan_immediately_wakes_dormant_deals(crm, scan_state, agent):
    """The mirror image of the previous test: a deal forgotten for months is the day-1 value."""
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
    assert report.processed[0].trigger == "inactivity"


def test_the_scheduled_follow_up_is_consumed_once_processed(crm, scan_state, agent):
    scan_state.states["acme-co"] = KnownDealState(
        stage="qualification", follow_up_due_at=NOW - timedelta(days=1)
    )

    build_scanner(crm, scan_state, agent).execute()

    assert scan_state.states["acme-co"].follow_up_due_at is None
