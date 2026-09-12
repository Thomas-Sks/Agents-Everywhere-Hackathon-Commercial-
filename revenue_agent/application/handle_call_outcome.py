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
                "Could not log the call in the CRM for opportunity %s",
                outcome.opportunity_id,
            )

        return self._decision_cycle.execute_safely(
            outcome.opportunity_id, _describe(outcome)
        )


def _describe(outcome: CallOutcome) -> str:
    lines = ["The phone call with the prospect has just ended."]
    if outcome.summary:
        lines.append(f"Call summary: {outcome.summary}")
    if outcome.sentiment:
        lines.append(f"Detected sentiment: {outcome.sentiment}")
    if outcome.duration_seconds:
        lines.append(f"Duration: {outcome.duration_seconds} seconds.")
    if not outcome.successful:
        lines.append("The call did not connect (no answer or immediate hang-up).")
    if outcome.transcript:
        lines.append(f"\nTranscript:\n{outcome.transcript}")
    lines.append(
        "\nUpdate your understanding of the opportunity from what was actually said, "
        "then decide what comes next."
    )
    return "\n".join(lines)
