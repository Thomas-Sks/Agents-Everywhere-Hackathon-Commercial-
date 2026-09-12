"""Composition root — le seul endroit du code où l'on choisit des implémentations concrètes.

Toute la dégradation gracieuse se décide ici : pas de HubSpot configuré, on branche le CRM
JSON ; pas de Resend, l'email part en console. Le domaine et les use cases n'ont aucune
connaissance de ces arbitrages — ils ne voient que des ports. C'est ce qui permet de passer
d'une démo hors-ligne à une exécution réelle en changeant des variables d'environnement, sans
toucher une ligne de logique métier.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from revenue_agent.adapters.approvals.json_file import JsonFileApprovalAdapter
from revenue_agent.adapters.catalog.json_file import JsonFileCatalogAdapter
from revenue_agent.adapters.communication.composite_handoff import CompositeHandoffAdapter
from revenue_agent.adapters.communication.console import (
    ConsoleEmailAdapter,
    ConsoleHandoffAdapter,
    ConsoleVoiceAdapter,
    ConsoleWhatsAppAdapter,
)
from revenue_agent.adapters.communication.hubspot_handoff import HubSpotHandoffAdapter
from revenue_agent.adapters.communication.meta_whatsapp import MetaWhatsAppAdapter
from revenue_agent.adapters.communication.resend_email import ResendEmailAdapter
from revenue_agent.adapters.communication.retell_voice import RetellVoiceAdapter
from revenue_agent.adapters.communication.teams_handoff import TeamsHandoffAdapter
from revenue_agent.adapters.crm.hubspot import HubSpotCrmAdapter
from revenue_agent.adapters.crm.json_file import JsonFileCrmAdapter
from revenue_agent.adapters.intelligence.composite_reviewer import LayeredMessageReviewer
from revenue_agent.adapters.intelligence.exa_enrichment import (
    ExaEnrichmentAdapter,
    NullEnrichmentAdapter,
)
from revenue_agent.adapters.intelligence.lexical_reviewer import LexicalMessageReviewer
from revenue_agent.adapters.intelligence.scripted_agent import ScriptedDecisionAgent
from revenue_agent.adapters.seed import ensure_seeded
from revenue_agent.adapters.state.json_file import JsonFileScanStateAdapter
from revenue_agent.adapters.storage.json_document import JsonDocument
from revenue_agent.application.action_registry import ActionRegistry
from revenue_agent.application.handle_call_outcome import HandleCallOutcome
from revenue_agent.application.review_approval import ReviewApproval
from revenue_agent.application.run_decision_cycle import RunDecisionCycle
from revenue_agent.application.scan_for_leads import ScanForLeads
from revenue_agent.config import Settings
from revenue_agent.ports.approvals import ApprovalPort
from revenue_agent.ports.communication import HandoffPort
from revenue_agent.ports.crm import CatalogPort, CrmPort
from revenue_agent.ports.intelligence import DecisionAgentPort, MessageReviewPort
from revenue_agent.ports.state import ScanStatePort

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Container:
    settings: Settings
    crm: CrmPort
    catalog: CatalogPort
    scan_state: ScanStatePort
    approvals: ApprovalPort
    actions: ActionRegistry
    agent: DecisionAgentPort
    decision_cycle: RunDecisionCycle
    review_approval: ReviewApproval
    scan_for_leads: ScanForLeads
    handle_call_outcome: HandleCallOutcome


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings.from_env()

    state_path = Path(settings.state_file)
    var_dir = state_path.parent
    crm_document = JsonDocument(var_dir / "crm.json")
    catalog_document = JsonDocument(var_dir / "catalog.json")
    state_document = JsonDocument(state_path)

    ensure_seeded(crm_document, catalog_document)

    crm: CrmPort = (
        HubSpotCrmAdapter(settings.hubspot.token)
        if settings.hubspot.enabled
        else JsonFileCrmAdapter(crm_document)
    )
    catalog: CatalogPort = JsonFileCatalogAdapter(catalog_document)
    scan_state: ScanStatePort = JsonFileScanStateAdapter(state_document)
    approvals: ApprovalPort = JsonFileApprovalAdapter(JsonDocument(var_dir / "approvals.json"))

    email = (
        ResendEmailAdapter(settings.resend.api_key, settings.resend.from_address)
        if settings.resend.enabled
        else ConsoleEmailAdapter()
    )
    whatsapp = (
        MetaWhatsAppAdapter(settings.whatsapp.token, settings.whatsapp.phone_number_id)
        if settings.whatsapp.enabled
        else ConsoleWhatsAppAdapter()
    )
    voice = (
        RetellVoiceAdapter(
            settings.retell.api_key, settings.retell.from_number, settings.retell.agent_id
        )
        if settings.retell.enabled
        else ConsoleVoiceAdapter()
    )
    handoff = _build_handoff(settings)
    enrichment = (
        ExaEnrichmentAdapter(settings.exa.api_key)
        if settings.exa.enabled
        else NullEnrichmentAdapter()
    )

    reviewer = _build_reviewer(settings)

    actions = ActionRegistry(
        crm=crm,
        catalog=catalog,
        email=email,
        whatsapp=whatsapp,
        voice=voice,
        handoff=handoff,
        enrichment=enrichment,
        scan_state=scan_state,
        approvals=approvals,
        reviewer=reviewer,
        policy_settings=settings.policy,
    )
    agent = _build_agent(settings=settings, crm=crm, actions=actions)
    decision_cycle = RunDecisionCycle(crm=crm, agent=agent, scan_state=scan_state)

    _log_wiring(settings)

    return Container(
        settings=settings,
        crm=crm,
        catalog=catalog,
        scan_state=scan_state,
        approvals=approvals,
        actions=actions,
        agent=agent,
        decision_cycle=decision_cycle,
        review_approval=ReviewApproval(approvals=approvals, actions=actions, crm=crm),
        scan_for_leads=ScanForLeads(
            crm=crm,
            scan_state=scan_state,
            decision_cycle=decision_cycle,
            settings=settings.scan,
        ),
        handle_call_outcome=HandleCallOutcome(crm=crm, decision_cycle=decision_cycle),
    )


def _build_handoff(settings: Settings) -> HandoffPort:
    """Destinations réelles du passage de relais.

    La console reste en dernier recours, jamais comme destination unique : c'est le geste le
    plus important du produit, il ne peut pas se terminer dans un flux que personne ne lit.
    """
    destinations: list[HandoffPort] = []

    if settings.hubspot.enabled:
        # Une tâche assignée sur le deal : durable, et là où le commercial travaille déjà.
        destinations.append(
            HubSpotHandoffAdapter(settings.hubspot.token, settings.handoff.hubspot_owner_id)
        )
    if settings.handoff.teams_enabled:
        # Le ping immédiat, avec le lien d'arbitrage.
        destinations.append(
            TeamsHandoffAdapter(
                settings.handoff.teams_webhook_url, settings.handoff.approval_url
            )
        )

    if not destinations:
        logger.warning(
            "Aucune destination de handoff configurée — les passages de relais et les "
            "demandes de validation n'atteindront aucun humain hors des logs"
        )
        return ConsoleHandoffAdapter()

    return CompositeHandoffAdapter(destinations, ConsoleHandoffAdapter())


def _build_reviewer(settings: Settings) -> MessageReviewPort:
    """Relecture en deux couches : le socle lexical toujours, le jugement sémantique si un
    modèle est disponible.

    Le socle n'est jamais retiré. Un classifieur voit ce qu'un dictionnaire ne peut pas voir,
    mais il est probabiliste et faillible : le remplacer par lui seul échangerait une garantie
    contre une probabilité.
    """
    lexical = LexicalMessageReviewer()
    if not settings.openrouter.enabled:
        logger.warning(
            "Relecture sémantique indisponible (pas de clé OpenRouter) — "
            "seul le socle lexical protège les envois"
        )
        return LayeredMessageReviewer(lexical=lexical, semantic=None)

    from revenue_agent.adapters.intelligence.llm_reviewer import LlmMessageReviewer

    return LayeredMessageReviewer(
        lexical=lexical, semantic=LlmMessageReviewer(settings.openrouter)
    )


def _build_agent(
    *, settings: Settings, crm: CrmPort, actions: ActionRegistry
) -> DecisionAgentPort:
    if not settings.openrouter.enabled:
        return ScriptedDecisionAgent(crm)

    # Import tardif : LangChain et LangGraph sont lourds à charger, inutile de les payer
    # quand l'application tourne sans LLM (tests, démo hors-ligne).
    from revenue_agent.adapters.intelligence.langgraph_agent import LangGraphDecisionAgent

    return LangGraphDecisionAgent(settings=settings, actions=actions)


def _log_wiring(settings: Settings) -> None:
    degraded = settings.degraded_components()
    if degraded:
        logger.warning("Composants en mode simulé : %s", " ; ".join(degraded))
    else:
        logger.info("Toutes les intégrations sont configurées.")
