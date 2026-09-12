"""Use case : exploiter la fin d'un appel téléphonique.

C'est le maillon qui ferme la boucle vocale. Sans lui, un appel est un événement orphelin :
l'agent aurait parlé au prospect sans que rien de ce qui s'y est dit ne remonte dans le CRM ni
n'influence la décision suivante.

Côté Retell, c'est l'événement `call_analyzed` — et non `call_ended` — qui porte le résumé et
le sentiment ; c'est donc celui-là qu'il faut écouter.
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
        # L'écriture CRM passe en premier : même si le cycle de décision échoue derrière, la
        # trace de l'appel ne doit pas être perdue.
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
