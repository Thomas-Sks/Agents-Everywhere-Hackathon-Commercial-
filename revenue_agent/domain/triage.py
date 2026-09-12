"""Triage déterministe des opportunités — le cœur du scan périodique.

Aucun appel LLM, aucun appel réseau : c'est volontaire. Un scan peut remonter des centaines de
deals modifiés ; faire raisonner un modèle sur chacun coûterait cher pour, la plupart du temps,
conclure qu'il n'y a rien à faire. Le triage répond à la question bon marché — « cette
opportunité mérite-t-elle qu'on y réfléchisse maintenant ? » — et seules celles qui passent
déclenchent un cycle de décision.

Fonctions pures, donc testables sans infrastructure.
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


# Un même deal peut satisfaire plusieurs règles sur un tick. L'ordre ci-dessous tranche :
# un signal frais (le prospect a bougé) prime toujours sur un signal d'absence (rien n'a bougé).
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
    """Ce que l'application sait déjà d'un deal, d'un scan sur l'autre."""

    stage: str = ""
    last_decision_at: datetime | None = None
    follow_up_due_at: datetime | None = None


def select_triggers(
    snapshots: list[DealSnapshot],
    known_states: dict[str, KnownDealState],
    now: datetime,
    inactivity_days: int,
) -> list[Trigger]:
    """Sélectionne les opportunités méritant un cycle de décision.

    `snapshots` = ce que le CRM signale comme modifié depuis le dernier scan.
    `known_states` = la mémoire applicative, qui permet de détecter aussi les *absences*
    (relance arrivée à échéance, silence prolongé) qu'aucun scan de modifications ne remonte.
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
    """Un cycle de décision par opportunité et par scan, au maximum."""
    best: dict[str, Trigger] = {}
    for trigger in triggers:
        current = best.get(trigger.opportunity_id)
        if current is None or trigger.priority < current.priority:
            best[trigger.opportunity_id] = trigger
    return sorted(best.values(), key=lambda t: (t.priority, t.opportunity_id))
