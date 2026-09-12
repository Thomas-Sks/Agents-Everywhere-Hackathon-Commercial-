"""Adapter du moteur de décision — LangGraph + GPT-5.6 via OpenRouter.

Deux points de conception méritent d'être explicités :

**Le routage par enjeu.** Une relance de routine et une négociation à 200 k€ ne méritent pas le
même modèle. `decide(strategic=True)` route vers un modèle plus capable ; le reste tourne sur le
tier économique. C'est le prolongement direct de la thèse produit : l'agent décide aussi combien
de raisonnement l'affaire justifie.

**Les tools ne contiennent aucune logique.** Ils exposent au modèle les actions de
`ActionRegistry` — les mêmes que celles appelées par l'agent vocal via webhook. Ici ne vivent
que les descriptions destinées au modèle, qui sont sa véritable documentation d'usage.

Une erreur d'adapter est convertie en message rendu au modèle plutôt qu'en exception : l'agent
peut alors changer de canal ou escalader, ce qu'un crash lui interdirait.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from revenue_agent.application.action_registry import ActionRegistry
from revenue_agent.config import Settings
from revenue_agent.domain.errors import AdapterError, RevenueAgentError
from revenue_agent.domain.models import Opportunity

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "decision_engine.md"


def _guard(func: Callable[..., str]) -> Callable[..., str]:
    """Convertit les échecs en réponse textuelle exploitable par le modèle."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return func(*args, **kwargs)
        except (AdapterError, RevenueAgentError) as exc:
            logger.warning("Tool %s en échec : %s", func.__name__, exc)
            return f"ERREUR : {exc}. Adapte ta décision en conséquence."

    return wrapper


class LangGraphDecisionAgent:
    def __init__(self, *, settings: Settings, actions: ActionRegistry) -> None:
        self._settings = settings
        self._actions = actions
        self._checkpointer = MemorySaver()
        self._tools = self._build_tools()
        self._agents: dict[str, Any] = {}

    def decide(self, *, opportunity: Opportunity, event: str, strategic: bool = False) -> str:
        model_name = (
            self._settings.openrouter.model_strategic
            if strategic
            else self._settings.openrouter.model_routine
        )
        logger.info(
            "Cycle de décision — opportunité %s, modèle %s (%s)",
            opportunity.id,
            model_name,
            "stratégique" if strategic else "routine",
        )

        result = self._agent_for(model_name).invoke(
            {"messages": [{"role": "user", "content": _build_user_message(opportunity, event)}]},
            config={"configurable": {"thread_id": opportunity.id}},
        )
        return _extract_final_text(result)

    def _agent_for(self, model_name: str):
        if model_name not in self._agents:
            self._agents[model_name] = create_agent(
                model=self._build_model(model_name),
                tools=self._tools,
                system_prompt=self._system_prompt(),
                checkpointer=self._checkpointer,
            )
        return self._agents[model_name]

    def _build_model(self, model_name: str) -> ChatOpenAI:
        openrouter = self._settings.openrouter
        return ChatOpenAI(
            model=model_name,
            api_key=openrouter.api_key,
            base_url=openrouter.base_url,
            temperature=0.3,
            timeout=90,
            max_retries=2,
            default_headers={"X-Title": "Autonomous Revenue Agent"},
            # Fallback natif OpenRouter : si le modèle principal est indisponible, la requête
            # bascule de leur côté, sans retry à gérer chez nous.
            model_kwargs={"models": list(openrouter.fallback_models)},
        )

    def _system_prompt(self) -> str:
        return PROMPT_PATH.read_text(encoding="utf-8").replace(
            "{{COMPANY_NAME}}", self._settings.company_name
        )

    def _build_tools(self) -> list:
        actions = self._actions

        @tool
        @_guard
        def get_opportunity_context(opportunity_id: str) -> str:
            """Relire l'état complet d'une opportunité avant toute décision : entreprise,
            interlocuteurs et leurs rôles, stade, objections ouvertes, historique, et surtout
            les canaux réellement joignables.

            Args:
                opportunity_id: Identifiant de l'opportunité.
            """
            return actions.get_opportunity_context(opportunity_id)

        @tool
        @_guard
        def get_product_info(product_id: str = "") -> str:
            """Consulter les fiches produits (nom, catégorie, prix, description). Ne jamais
            citer un prix ou une caractéristique sans être passé par cet outil.

            Args:
                product_id: Identifiant d'un produit précis, ou vide pour tout le catalogue.
            """
            return actions.get_product_info(product_id)

        @tool
        @_guard
        def research_prospect(company_name: str) -> str:
            """Chercher sur le web l'actualité récente du prospect (levée de fonds,
            recrutements, nomination de dirigeants) pour combler ce que le CRM ne dit pas.
            À utiliser quand il manque un élément de contexte pour décider.

            Args:
                company_name: Nom de l'entreprise à rechercher.
            """
            return actions.research_prospect(company_name)

        @tool
        @_guard
        def record_interaction(opportunity_id: str, channel: str, summary: str) -> str:
            """Consigner dans le CRM ce qui vient de se passer ou d'être appris. À appeler
            après chaque échange significatif, même si aucune action externe n'est prise.

            Args:
                opportunity_id: Identifiant de l'opportunité.
                channel: Canal concerné ("email", "whatsapp", "voice", "signal", "decision").
                summary: Résumé factuel de l'événement ou de l'apprentissage.
            """
            return actions.record_interaction(opportunity_id, channel, summary)

        @tool
        @_guard
        def update_opportunity(
            opportunity_id: str,
            stage: str = "",
            probability: int = -1,
            objection: str = "",
            objection_root_cause: str = "",
            next_steps: str = "",
        ) -> str:
            """Mettre à jour la représentation de l'opportunité dans le CRM.

            Args:
                opportunity_id: Identifiant de l'opportunité.
                stage: Nouveau stade commercial, si changé.
                probability: Nouvelle probabilité de conversion (0-100), -1 si inchangée.
                objection: Objection nouvellement détectée, le cas échéant.
                objection_root_cause: Cause profonde estimée de cette objection.
                next_steps: Prochaine étape décidée.
            """
            return actions.update_opportunity(
                opportunity_id,
                stage=stage,
                probability=probability,
                objection=objection,
                objection_root_cause=objection_root_cause,
                next_steps=next_steps,
            )

        @tool
        @_guard
        def send_email(opportunity_id: str, subject: str, body: str) -> str:
            """Envoyer un email au prospect. L'adresse est résolue depuis le CRM : ne la
            devine jamais.

            Args:
                opportunity_id: Identifiant de l'opportunité.
                subject: Objet de l'email.
                body: Corps du message.
            """
            return actions.send_email(opportunity_id, subject, body)

        @tool
        @_guard
        def send_whatsapp_message(opportunity_id: str, message: str) -> str:
            """Envoyer un message WhatsApp au prospect. Le numéro est résolu depuis le CRM.

            Args:
                opportunity_id: Identifiant de l'opportunité.
                message: Contenu du message.
            """
            return actions.send_whatsapp_message(opportunity_id, message)

        @tool
        @_guard
        def place_phone_call(opportunity_id: str, objective: str) -> str:
            """Déclencher un appel téléphonique sortant, conduit par l'agent vocal. À réserver
            aux situations où la voix apporte plus que l'écrit.

            Args:
                opportunity_id: Identifiant de l'opportunité.
                objective: Objectif précis de l'appel, transmis à l'agent vocal.
            """
            return actions.place_phone_call(opportunity_id, objective)

        @tool
        @_guard
        def schedule_follow_up(opportunity_id: str, reason: str, due_date: str) -> str:
            """Décider volontairement de ne rien faire maintenant et programmer une reprise.
            C'est une décision commerciale à part entière : le scan périodique réveillera
            l'opportunité à la date indiquée.

            Args:
                opportunity_id: Identifiant de l'opportunité.
                reason: Pourquoi agir maintenant serait contre-productif.
                due_date: Date de reprise au format AAAA-MM-JJ.
            """
            return actions.schedule_follow_up(opportunity_id, reason, due_date)

        @tool
        @_guard
        def escalate_to_human(
            opportunity_id: str, reason: str, urgency: str, context_brief: str
        ) -> str:
            """Passer la main à un commercial humain avec l'intégralité du contexte.

            context_brief doit contenir : qui est l'interlocuteur, son problème réel, la cause
            profonde de ses objections, qui d'autre décide, ce qui a été demandé, et ce qu'il
            reste à traiter au prochain échange. Jamais « appelle Jean, il est intéressé ».

            Args:
                opportunity_id: Identifiant de l'opportunité.
                reason: Pourquoi la situation dépasse les limites d'autonomie de l'agent.
                urgency: "faible", "normale" ou "haute".
                context_brief: Brief complet et structuré pour le commercial humain.
            """
            return actions.escalate_to_human(opportunity_id, reason, urgency, context_brief)

        return [
            get_opportunity_context,
            get_product_info,
            research_prospect,
            record_interaction,
            update_opportunity,
            send_email,
            send_whatsapp_message,
            place_phone_call,
            schedule_follow_up,
            escalate_to_human,
        ]


def _build_user_message(opportunity: Opportunity, event: str) -> str:
    channels = opportunity.reachable_channels()
    available = ", ".join(channel.value for channel in channels.available) or "aucun"
    return (
        f"Nouvel événement sur l'opportunité '{opportunity.id}' ({opportunity.company}) :\n\n"
        f"{event}\n\n"
        f"Canaux réellement disponibles pour ce prospect : {available}.\n"
        "Relis d'abord l'état complet de l'opportunité, puis décide et exécute la meilleure "
        "action commerciale maintenant — ou justifie explicitement pourquoi attendre, en "
        "programmant la reprise."
    )


def _extract_final_text(result: dict) -> str:
    for message in reversed(result.get("messages", [])):
        if getattr(message, "type", None) == "ai":
            content = message.content
            text = content if isinstance(content, str) else str(content)
            if text.strip():
                return text.strip()
    return ""
