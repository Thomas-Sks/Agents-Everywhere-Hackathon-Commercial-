"""Triage decides who consumes an LLM cycle — its rules deserve to be locked down."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from revenue_agent.domain.models import DealSnapshot
from revenue_agent.domain.triage import KnownDealState, TriggerKind, select_triggers

NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


def snapshot(deal_id: str = "1", stage: str = "qualification") -> DealSnapshot:
    return DealSnapshot(id=deal_id, name="Acme Co", stage=stage, last_modified_at=NOW)


def test_an_unknown_deal_is_a_new_lead():
    triggers = select_triggers([snapshot()], {}, NOW, inactivity_days=14)

    assert len(triggers) == 1
    assert triggers[0].kind is TriggerKind.NEW_LEAD


def test_a_stage_change_is_detected():
    known = {"1": KnownDealState(stage="discovery")}

    triggers = select_triggers([snapshot(stage="negotiation")], known, NOW, inactivity_days=14)

    assert triggers[0].kind is TriggerKind.STAGE_CHANGED
    assert "discovery" in triggers[0].reason and "negotiation" in triggers[0].reason


def test_a_deal_modified_without_a_stage_change_is_ignored():
    """Any old change in the CRM does not justify making a model reason about it."""
    known = {"1": KnownDealState(stage="qualification")}

    assert select_triggers([snapshot()], known, NOW, inactivity_days=14) == []


def test_a_scheduled_follow_up_that_has_come_due():
    known = {"1": KnownDealState(stage="qualification", follow_up_due_at=NOW - timedelta(hours=1))}

    triggers = select_triggers([], known, NOW, inactivity_days=14)

    assert triggers[0].kind is TriggerKind.SCHEDULED_FOLLOW_UP


def test_a_future_follow_up_does_not_fire():
    known = {"1": KnownDealState(stage="qualification", follow_up_due_at=NOW + timedelta(days=30))}

    assert select_triggers([], known, NOW, inactivity_days=14) == []


def test_prolonged_inactivity_wakes_the_opportunity_up():
    known = {"1": KnownDealState(stage="qualification", last_decision_at=NOW - timedelta(days=20))}

    triggers = select_triggers([], known, NOW, inactivity_days=14)

    assert triggers[0].kind is TriggerKind.INACTIVITY
    assert "20 jours" in triggers[0].reason


def test_inactivity_below_the_threshold_is_ignored():
    known = {"1": KnownDealState(stage="qualification", last_decision_at=NOW - timedelta(days=3))}

    assert select_triggers([], known, NOW, inactivity_days=14) == []


def test_only_the_highest_priority_trigger_per_opportunity_is_kept():
    """A fresh signal (the prospect moved) outranks a signal of absence (nothing moved)."""
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


def test_results_are_sorted_by_priority():
    snapshots = [snapshot("1", stage="negotiation"), snapshot("2")]
    known = {"1": KnownDealState(stage="discovery")}

    triggers = select_triggers(snapshots, known, NOW, inactivity_days=14)

    assert [t.kind for t in triggers] == [TriggerKind.STAGE_CHANGED, TriggerKind.NEW_LEAD]
