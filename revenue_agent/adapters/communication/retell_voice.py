"""Voice adapter — Retell AI (outbound phone calls).

Verified API constraint: `retell_llm_dynamic_variables` only accepts **strings**. The whole
opportunity context must therefore be flattened before sending — hence `_dynamic_variables`,
which serialises explicitly rather than letting a nested dict fail on the Retell side.

On the model side: GPT-5.6 does not appear among the LLMs Retell offers natively. The voice
agent therefore runs on their model, while our decision engine (GPT-5.6) keeps the strategy
before and after the call, as well as tool execution during the call via webhook.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from revenue_agent.adapters.http import HttpClient
from revenue_agent.domain.errors import VoiceError
from revenue_agent.domain.models import Opportunity

logger = logging.getLogger(__name__)

MAX_VARIABLE_LENGTH = 1000


class RetellVoiceAdapter:
    def __init__(self, api_key: str, from_number: str, agent_id: str) -> None:
        self._from_number = from_number
        self._agent_id = agent_id
        self._http = HttpClient(
            base_url="https://api.retellai.com",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            error_factory=lambda message: VoiceError("retell", message),
        )

    def place_call(self, *, to_phone_number: str, opportunity: Opportunity, objective: str) -> str:
        payload = self._http.request(
            "POST",
            "/v2/create-phone-call",
            json={
                "from_number": self._from_number,
                "to_number": to_phone_number,
                "override_agent_id": self._agent_id,
                "retell_llm_dynamic_variables": _dynamic_variables(opportunity, objective),
                # Lets us find the opportunity again at the `call_analyzed` webhook, without
                # having to guess it from the phone number.
                "metadata": {"opportunity_id": opportunity.id},
            },
        )
        call_id = payload.get("call_id", "")
        logger.info(
            "Appel Retell déclenché vers %s (opportunité %s, call_id=%s)",
            to_phone_number,
            opportunity.id,
            call_id,
        )
        return call_id


def _dynamic_variables(opportunity: Opportunity, objective: str) -> dict[str, str]:
    """Flattens the context into strings — the only format Retell accepts."""
    contact = next(iter(opportunity.stakeholders), None)
    objections = " ; ".join(
        f"{objection.text} (cause probable : {objection.root_cause})"
        for objection in opportunity.unresolved_objections()
    )
    recent_history = " | ".join(
        f"{interaction.occurred_at:%d/%m} {interaction.summary}"
        for interaction in opportunity.history[-3:]
    )

    variables = {
        "company_name": opportunity.company,
        "contact_name": contact.name if contact else "",
        "contact_role": contact.role if contact else "",
        "deal_stage": opportunity.stage,
        "call_objective": objective,
        "open_objections": objections or "aucune objection connue",
        "recent_history": recent_history or "premier contact",
    }
    return {key: _truncate(value) for key, value in variables.items()}


def _truncate(value: str) -> str:
    if len(value) <= MAX_VARIABLE_LENGTH:
        return value
    return value[: MAX_VARIABLE_LENGTH - 1] + "…"


def verify_signature(*, payload: bytes, signature: str | None, secret: str) -> bool:
    """Verifies the HMAC-SHA256 signature of a Retell webhook (`X-Retell-Signature`).

    Constant-time comparison, over the raw request body: re-serialising the JSON before signing
    would invalidate the comparison at the slightest whitespace difference.
    """
    if not secret:
        raise VoiceError("retell", "secret de webhook non configuré")
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    provided = signature.split("=")[-1].strip()
    return hmac.compare_digest(expected, provided)
