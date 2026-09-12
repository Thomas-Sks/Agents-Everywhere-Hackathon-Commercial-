"""Decision engine adapter — LangGraph + GPT-5.6 via OpenRouter.

Two design points are worth spelling out:

**Stakes-based routing.** A routine follow-up and a €200k negotiation do not deserve the same
model. `decide(strategic=True)` routes to a more capable model; everything else runs on the
economy tier. This is a direct extension of the product thesis: the agent also decides how much
reasoning the deal warrants.

**The tools contain no logic.** They expose `ActionRegistry`'s actions to the model — the very
same ones the voice agent calls through the webhook. All that lives here are the descriptions
aimed at the model, which are its real usage documentation.

An adapter error is turned into a message handed back to the model rather than an exception:
the agent can then switch channel or escalate, which a crash would forbid.
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
    """Turns failures into a textual response the model can work with."""

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
            # Native OpenRouter fallback: if the primary model is unavailable, the request
            # switches over on their side, with no retry logic for us to manage.
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
            """Read back the full state of an opportunity before any decision: company,
            stakeholders and their roles, stage, open objections, history, and above all the
            channels that are actually reachable.

            Args:
                opportunity_id: Identifier of the opportunity.
            """
            return actions.get_opportunity_context(opportunity_id)

        @tool
        @_guard
        def get_product_info(product_id: str = "") -> str:
            """Look up the product sheets (name, category, price, description). Never quote a
            price or a feature without having gone through this tool first.

            Args:
                product_id: Identifier of a specific product, or empty for the whole catalogue.
            """
            return actions.get_product_info(product_id)

        @tool
        @_guard
        def research_prospect(company_name: str) -> str:
            """Search the web for the prospect's recent news (funding rounds, hiring,
            executive appointments) to fill in what the CRM does not say. Use it when a piece
            of context is missing before deciding.

            Args:
                company_name: Name of the company to research.
            """
            return actions.research_prospect(company_name)

        @tool
        @_guard
        def record_interaction(opportunity_id: str, channel: str, summary: str) -> str:
            """Record in the CRM what has just happened or been learned. Call it after every
            significant exchange, even when no external action is taken.

            Args:
                opportunity_id: Identifier of the opportunity.
                channel: Channel concerned ("email", "whatsapp", "voice", "signal", "decision").
                summary: Factual summary of the event or of what was learned.
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
            """Update the representation of the opportunity in the CRM.

            Args:
                opportunity_id: Identifier of the opportunity.
                stage: New sales stage, if it changed.
                probability: New conversion probability (0-100), -1 if unchanged.
                objection: Newly detected objection, if any.
                objection_root_cause: Estimated root cause of that objection.
                next_steps: Next step decided on.
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
        def update_stakeholder(
            opportunity_id: str,
            name: str,
            stance: str,
            role: str = "",
            notes: str = "",
        ) -> str:
            """Consigner qui décide, qui influence, qui bloque. Une vente complexe échoue
            rarement à cause du produit seul : tiens cette carte à jour dès que tu apprends
            le rôle réel de quelqu'un, y compris pour une personne jamais contactée dont on
            t'a seulement parlé.

            Args:
                opportunity_id: Identifiant de l'opportunité.
                name: Nom de la personne, tel qu'il apparaît dans le CRM si elle y figure.
                stance: "champion", "decideur", "opposant", "neutre" ou "inconnu".
                role: Fonction dans l'entreprise, si connue.
                notes: Ce qui justifie cette posture, en une phrase.
            """
            return actions.update_stakeholder(opportunity_id, name, stance, role, notes)

        @tool
        @_guard
        def resolve_objection(opportunity_id: str, objection_id: str, resolution: str) -> str:
            """Close an objection you have dealt with. Do it as soon as an objection is no
            longer live — the prospect got their answer, the constraint disappeared, or it
            turned out to be unfounded.

            An objection left open once it is settled durably distorts the reading of the
            opportunity.

            Args:
                opportunity_id: Identifier of the opportunity.
                objection_id: Identifier of the objection, exactly as shown in the context.
                resolution: How it was lifted, in one sentence.
            """
            return actions.resolve_objection(opportunity_id, objection_id, resolution)

        @tool
        @_guard
        def send_email(opportunity_id: str, subject: str, body: str) -> str:
            """Send an email to the prospect. The address is resolved from the CRM: never
            guess it.

            Args:
                opportunity_id: Identifier of the opportunity.
                subject: Subject line of the email.
                body: Body of the message.
            """
            return actions.send_email(opportunity_id, subject, body)

        @tool
        @_guard
        def send_whatsapp_message(opportunity_id: str, message: str) -> str:
            """Send a WhatsApp message to the prospect. The number is resolved from the CRM.

            Args:
                opportunity_id: Identifier of the opportunity.
                message: Content of the message.
            """
            return actions.send_whatsapp_message(opportunity_id, message)

        @tool
        @_guard
        def place_phone_call(opportunity_id: str, objective: str) -> str:
            """Trigger an outbound phone call, conducted by the voice agent. Reserve it for
            situations where the voice brings more than the written word.

            Args:
                opportunity_id: Identifier of the opportunity.
                objective: Precise objective of the call, passed on to the voice agent.
            """
            return actions.place_phone_call(opportunity_id, objective)

        @tool
        @_guard
        def schedule_follow_up(opportunity_id: str, reason: str, due_date: str) -> str:
            """Deliberately decide to do nothing now and schedule a re-engagement. This is a
            sales decision in its own right: the periodic scan will wake the opportunity up on
            the given date.

            Args:
                opportunity_id: Identifier of the opportunity.
                reason: Why acting now would be counterproductive.
                due_date: Re-engagement date in YYYY-MM-DD format.
            """
            return actions.schedule_follow_up(opportunity_id, reason, due_date)

        @tool
        @_guard
        def escalate_to_human(
            opportunity_id: str, reason: str, urgency: str, context_brief: str
        ) -> str:
            """Hand the deal over to a human sales rep with the full context.

            context_brief must contain: who the contact is, their real problem, the root cause
            of their objections, who else decides, what was asked for, and what remains to be
            handled at the next exchange. Never "call Jean, he's interested".

            Args:
                opportunity_id: Identifier of the opportunity.
                reason: Why the situation exceeds the agent's autonomy limits.
                urgency: "faible", "normale" or "haute" (these exact values are mapped to
                    HubSpot task priorities).
                context_brief: Full, structured brief for the human sales rep.
            """
            return actions.escalate_to_human(opportunity_id, reason, urgency, context_brief)

        return [
            get_opportunity_context,
            get_product_info,
            research_prospect,
            record_interaction,
            update_opportunity,
            update_stakeholder,
            resolve_objection,
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
