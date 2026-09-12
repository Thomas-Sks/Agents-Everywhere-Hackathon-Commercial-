"""Relecture sémantique — un directeur commercial relit le message avant envoi.

Ce que la couche lexicale ne peut pas voir : « je m'aligne sur leur tarif », « on trouvera un
terrain d'entente », « je vous garantis un retour sur investissement en six mois ». Ces phrases
engagent l'entreprise sans contenir aucun mot-clé.

Trois partis pris :

**Le modèle le moins cher.** Une relecture par action sortante, sur un prompt court : c'est
exactement le profil « fort volume, latence contrainte » pour lequel le tier économique existe.
Le surcoût est de l'ordre de 0,0003 $ par message.

**Échec fermé.** Si le modèle ne répond pas, répond mal, ou renvoie du JSON invalide, on
considère que le message doit être relu par un humain. Un relecteur absent ne signifie pas que
le message est bon — il signifie qu'il n'a pas été relu.

**Le contexte est fourni.** La même phrase est anodine au premier contact et grave en fin de
négociation. Le modèle reçoit le stade, le montant et les objections ouvertes.
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
        except Exception as exc:  # noqa: BLE001 - toute défaillance doit fermer, pas ouvrir
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
    """Tolère un bloc de code ou une phrase autour du JSON, sans jamais deviner le verdict."""
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
