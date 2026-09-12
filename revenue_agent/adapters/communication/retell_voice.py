"""Adapter voix — Retell AI (appels téléphoniques sortants).

Contrainte vérifiée de l'API : `retell_llm_dynamic_variables` n'accepte que des **chaînes de
caractères**. Tout le contexte de l'opportunité doit donc être aplati avant l'envoi — d'où
`_dynamic_variables`, qui sérialise explicitement plutôt que de laisser un dict imbriqué
échouer côté Retell.

Côté modèle : GPT-5.6 n'apparaît pas dans les LLM proposés nativement par Retell. L'agent
vocal tourne donc sur leur modèle, et notre moteur de décision (GPT-5.6) garde la stratégie
avant et après l'appel, ainsi que l'exécution des tools pendant l'appel via webhook.
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
                # Permet de retrouver l'opportunité au webhook `call_analyzed`, sans avoir à
                # deviner à partir du numéro de téléphone.
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
    """Aplatit le contexte en chaînes — seul format accepté par Retell."""
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
    """Vérifie la signature HMAC-SHA256 d'un webhook Retell (`X-Retell-Signature`).

    Comparaison en temps constant, sur le corps brut de la requête : re-sérialiser le JSON
    avant de signer invaliderait la comparaison au moindre écart d'espacement.
    """
    if not secret:
        raise VoiceError("retell", "secret de webhook non configuré")
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    provided = signature.split("=")[-1].strip()
    return hmac.compare_digest(expected, provided)
