"""Deterministic triage of opportunities — the heart of the periodic scan.

No LLM call, no network call: that is deliberate. A scan can surface hundreds of modified
deals; having a model reason over each one would cost a fortune to conclude, most of the time,
that there is nothing to do. Triage answers the cheap question — "does this opportunity deserve
thinking about right now?" — and only the ones that pass trigger a decision cycle.

Pure functions, therefore testable without infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from revenue_agent.domain.models import DealSnapshot


class TriggerKind(StrEnum):
    STAGE_CHANGED = "changement_de_stade"
    NEW_LEAD = "nouveau_lead"
    SCHEDULED_FOLLOW_UP = "relance_programmee"
    INACTIVITY = "inactivite"


# A single deal can satisfy several rules on the same tick. The ordering below settles it:
# a fresh signal (the prospect moved) always wins over a signal of absence (nothing moved).
_PRIORITY: dict[TriggerKind, int] = {
    TriggerKind.STAGE_CHANGED: 0,
    TriggerKind.NEW_LEAD: 1,
    TriggerKind.SCHEDULED_FOLLOW_UP: 2,
    TriggerKind.INACTIVITY: 3,
}


@dataclass(frozen=True, slots=True)
class Trigger:
    opportunity_id: str
    kind: TriggerKind
    reason: str
    detected_at: datetime

    @property
    def priority(self) -> int:
        return _PRIORITY[self.kind]


@dataclass(frozen=True, slots=True)
class KnownDealState:
    """What the application already knows about a deal, from one scan to the next."""

    stage: str = ""
    last_decision_at: datetime | None = None
    follow_up_due_at: datetime | None = None


def select_triggers(
    snapshots: list[DealSnapshot],
    known_states: dict[str, KnownDealState],
    now: datetime,
    inactivity_days: int,
) -> list[Trigger]:
    """Select the opportunities that deserve a decision cycle.

    `snapshots` = what the CRM reports as modified since the last scan.
    `known_states` = the application's own memory, which also makes it possible to detect
    *absences* (a follow-up falling due, prolonged silence) that no scan of modifications
    would ever surface.
    """
    candidates: list[Trigger] = []
    candidates.extend(_from_snapshots(snapshots, known_states, now))
    candidates.extend(_from_known_states(known_states, now, inactivity_days))
    return _keep_highest_priority_per_opportunity(candidates)


def _from_snapshots(
    snapshots: list[DealSnapshot],
    known_states: dict[str, KnownDealState],
    now: datetime,
) -> list[Trigger]:
    triggers: list[Trigger] = []
    for snapshot in snapshots:
        known = known_states.get(snapshot.id)

        if known is None:
            triggers.append(
                Trigger(
                    opportunity_id=snapshot.id,
                    kind=TriggerKind.NEW_LEAD,
                    reason=(
                        f"Nouveau lead détecté dans le CRM : « {snapshot.name} », "
                        f"au stade « {snapshot.stage} »."
                    ),
                    detected_at=now,
                )
            )
            continue

        if known.stage and known.stage != snapshot.stage:
            triggers.append(
                Trigger(
                    opportunity_id=snapshot.id,
                    kind=TriggerKind.STAGE_CHANGED,
                    reason=(
                        f"L'opportunité « {snapshot.name} » est passée du stade "
                        f"« {known.stage} » à « {snapshot.stage} »."
                    ),
                    detected_at=now,
                )
            )

    return triggers


def _from_known_states(
    known_states: dict[str, KnownDealState],
    now: datetime,
    inactivity_days: int,
) -> list[Trigger]:
    triggers: list[Trigger] = []
    inactivity_threshold = now - timedelta(days=inactivity_days)

    for opportunity_id, state in known_states.items():
        if state.follow_up_due_at is not None and state.follow_up_due_at <= now:
            triggers.append(
                Trigger(
                    opportunity_id=opportunity_id,
                    kind=TriggerKind.SCHEDULED_FOLLOW_UP,
                    reason=(
                        "L'échéance de relance fixée lors d'une décision d'attente est "
                        f"atteinte (prévue le {state.follow_up_due_at:%Y-%m-%d})."
                    ),
                    detected_at=now,
                )
            )
            continue

        if state.last_decision_at is not None and state.last_decision_at <= inactivity_threshold:
            days = (now - state.last_decision_at).days
            triggers.append(
                Trigger(
                    opportunity_id=opportunity_id,
                    kind=TriggerKind.INACTIVITY,
                    reason=(
                        f"Aucune action sur cette opportunité depuis {days} jours — "
                        "à réévaluer."
                    ),
                    detected_at=now,
                )
            )

    return triggers


def _keep_highest_priority_per_opportunity(triggers: list[Trigger]) -> list[Trigger]:
    """At most one decision cycle per opportunity, per scan."""
    best: dict[str, Trigger] = {}
    for trigger in triggers:
        current = best.get(trigger.opportunity_id)
        if current is None or trigger.priority < current.priority:
            best[trigger.opportunity_id] = trigger
    return sorted(best.values(), key=lambda t: (t.priority, t.opportunity_id))
