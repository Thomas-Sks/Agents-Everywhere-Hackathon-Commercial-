"""Semantic review — a sales director reads the message before it goes out.

What the lexical layer cannot see: "I'll match their price", "we'll find common ground", "I
guarantee you a return on investment within six months". Those sentences commit the company
without containing a single keyword.

Three deliberate positions:

**The cheapest model.** One review per outbound action, on a short prompt: exactly the "high
volume, latency-constrained" profile the economy tier exists for. The extra cost is on the order
of $0.0003 per message.

**Fail closed.** If the model does not answer, answers badly, or returns invalid JSON, we treat
the message as one that must be reviewed by a human. An absent reviewer does not mean the
message is fine — it means it has not been reviewed.

**The context is provided.** The same sentence is innocuous on first contact and serious late in
a negotiation. The model receives the stage, the amount and the open objections.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from langchain_openai import ChatOpenAI

from revenue_agent.config import OpenRouterSettings
from revenue_agent.domain.review import MessageUnderReview, ReviewCategory, ReviewFinding

logger = logging.getLogger(__name__)

SOURCE = "llm"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "message_review.md"

_MAX_CONTENT_CHARS = 4000


class LlmMessageReviewer:
    def __init__(self, settings: OpenRouterSettings, model: str | None = None) -> None:
        self._model = ChatOpenAI(
            model=model or settings.model_routine,
            api_key=settings.api_key,
            base_url=settings.base_url,
            temperature=0,
            timeout=20,
            max_retries=1,
            default_headers={"X-Title": "Autonomous Revenue Agent — relecture"},
        )
        self._system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    def review(self, message: MessageUnderReview) -> ReviewFinding:
        try:
            response = self._model.invoke(
                [
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": _format(message)},
                ]
            )
            payload = _extract_json(_as_text(response.content))
        except Exception as exc:  # noqa: BLE001 - any failure must close, never open
            logger.warning("Relecture indisponible (%s) — le message est retenu", exc)
            return ReviewFinding.escalate(
                category=ReviewCategory.NONE,
                rationale=(
                    "La relecture automatique n'a pas pu s'exécuter. Le message est retenu par "
                    "précaution : un relecteur absent ne veut pas dire un message validé."
                ),
                source=SOURCE,
            )

        if payload is None:
            logger.warning("Relecture illisible — le message est retenu")
            return ReviewFinding.escalate(
                category=ReviewCategory.NONE,
                rationale="La relecture automatique a renvoyé une réponse illisible.",
                source=SOURCE,
            )

        if not payload.get("requires_human"):
            return ReviewFinding.clear(source=SOURCE)

        return ReviewFinding.escalate(
            category=_parse_category(payload.get("category")),
            rationale=str(payload.get("rationale") or "").strip(),
            quote=str(payload.get("quote") or "").strip(),
            source=SOURCE,
        )


def _format(message: MessageUnderReview) -> str:
    objections = (
        " ; ".join(message.open_objections) if message.open_objections else "aucune connue"
    )
    amount = f"{message.amount:,.0f} €" if message.amount is not None else "non renseigné"
    return (
        f"Canal : {message.channel}\n"
        f"Prospect : {message.company}\n"
        f"Stade de l'opportunité : {message.stage}\n"
        f"Montant : {amount}\n"
        f"Objections ouvertes : {objections}\n"
        f"Échanges déjà eus : {message.interactions_count}\n\n"
        "--- Message à relire ---\n"
        f"{message.content[:_MAX_CONTENT_CHARS]}"
    )


def _as_text(content) -> str:
    return content if isinstance(content, str) else str(content)


def _extract_json(text: str) -> dict | None:
    """Tolerates a code fence or prose around the JSON, without ever guessing the verdict."""
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        braces = re.search(r"\{.*\}", candidate, re.DOTALL)
        if braces:
            candidate = braces.group(0)

    try:
        payload = json.loads(candidate)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_category(raw: str | None) -> ReviewCategory:
    try:
        return ReviewCategory(str(raw))
    except ValueError:
        return ReviewCategory.NONE
