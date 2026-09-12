"""Use case: make use of the end of a phone call.

This is the link that closes the voice loop. Without it, a call is an orphan event: the agent
would have spoken to the prospect without anything that was said making its way back into the
CRM or influencing the next decision.

On the Retell side it is the `call_analyzed` event — not `call_ended` — that carries the
summary and the sentiment; that is therefore the one to listen for.
"""

from __future__ import annotations

import logging

from revenue_agent.application.run_decision_cycle import DecisionResult, RunDecisionCycle
from revenue_agent.domain.errors import CrmError
from revenue_agent.domain.models import CallOutcome
from revenue_agent.ports.crm import CrmPort

logger = logging.getLogger(__name__)


class HandleCallOutcome:
    def __init__(self, *, crm: CrmPort, decision_cycle: RunDecisionCycle) -> None:
        self._crm = crm
        self._decision_cycle = decision_cycle

    def execute(self, outcome: CallOutcome) -> DecisionResult | None:
        # The CRM write comes first: even if the decision cycle fails afterwards, the record of
        # the call must not be lost.
        try:
            self._crm.log_call(outcome.opportunity_id, outcome)
        except CrmError:
            logger.exception(
                "Impossible de logger l'appel dans le CRM pour l'opportunité %s",
                outcome.opportunity_id,
            )

        return self._decision_cycle.execute_safely(
            outcome.opportunity_id, _describe(outcome)
        )


def _describe(outcome: CallOutcome) -> str:
    lines = ["L'appel téléphonique avec le prospect vient de se terminer."]
    if outcome.summary:
        lines.append(f"Résumé de l'appel : {outcome.summary}")
    if outcome.sentiment:
        lines.append(f"Sentiment détecté : {outcome.sentiment}")
    if outcome.duration_seconds:
        lines.append(f"Durée : {outcome.duration_seconds} secondes.")
    if not outcome.successful:
        lines.append("L'appel n'a pas abouti (pas de réponse ou raccrochage immédiat).")
    if outcome.transcript:
        lines.append(f"\nTranscript :\n{outcome.transcript}")
    lines.append(
        "\nMets à jour ta compréhension de l'opportunité à partir de ce qui s'est réellement "
        "dit, puis décide de la suite."
    )
    return "\n".join(lines)
