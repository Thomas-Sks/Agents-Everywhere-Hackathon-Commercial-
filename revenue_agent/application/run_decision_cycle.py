"""Use case : faire raisonner l'agent sur un événement et exécuter sa décision.

Point d'entrée unique de toute la logique commerciale. Qu'un événement vienne du scan du CRM,
d'un message WhatsApp entrant ou de la fin d'un appel téléphonique, il finit ici — c'est ce qui
garantit qu'un prospect ne « recommence pas son histoire » en changeant de canal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from revenue_agent.domain import stakes
from revenue_agent.domain.errors import OpportunityNotFound
from revenue_agent.domain.triage import KnownDealState, TriggerKind
from revenue_agent.ports.crm import CrmPort
from revenue_agent.ports.intelligence import DecisionAgentPort
from revenue_agent.ports.state import ScanStatePort

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DecisionResult:
    opportunity_id: str
    summary: str
    strategic: bool
    routing_reason: str


class RunDecisionCycle:
    def __init__(
        self, *, crm: CrmPort, agent: DecisionAgentPort, scan_state: ScanStatePort
    ) -> None:
        self._crm = crm
        self._agent = agent
        self._scan_state = scan_state

    def execute(
        self, opportunity_id: str, event: str, *, trigger_kind: TriggerKind | None = None
    ) -> DecisionResult:
        opportunity = self._crm.load_opportunity(opportunity_id)

        strategic = stakes.requires_strategic_reasoning(opportunity, trigger_kind)
        routing_reason = stakes.explain(opportunity, trigger_kind)
        logger.info(
            "Opportunité %s routée en %s — %s",
            opportunity_id,
            "stratégique" if strategic else "routine",
            routing_reason,
        )

        summary = self._agent.decide(opportunity=opportunity, event=event, strategic=strategic)

        # Le scan a réveillé cette opportunité : la relance programmée est consommée, et on
        # mémorise le stade pour détecter le prochain changement.
        self._scan_state.clear_follow_up(opportunity_id)
        self._scan_state.upsert_known_state(
            opportunity_id,
            KnownDealState(
                stage=opportunity.stage,
                last_decision_at=datetime.now(UTC),
                follow_up_due_at=None,
            ),
        )

        return DecisionResult(
            opportunity_id=opportunity_id,
            summary=summary,
            strategic=strategic,
            routing_reason=routing_reason,
        )

    def execute_safely(
        self, opportunity_id: str, event: str, *, trigger_kind: TriggerKind | None = None
    ) -> DecisionResult | None:
        """Variante tolérante utilisée par le scan : l'échec d'une opportunité ne doit pas
        interrompre le traitement des suivantes."""
        try:
            return self.execute(opportunity_id, event, trigger_kind=trigger_kind)
        except OpportunityNotFound:
            logger.warning("Opportunité %s introuvable — ignorée", opportunity_id)
        except Exception:  # noqa: BLE001 - une boucle autonome ne doit jamais s'arrêter
            logger.exception("Cycle de décision en échec pour l'opportunité %s", opportunity_id)
        return None
