"""Composition root — the only place in the code where concrete implementations are chosen.

All graceful degradation is decided here: no HubSpot configured, we wire up the JSON CRM; no
Resend, email goes to the console. The domain and the use cases know nothing of these
arbitrations — they only ever see ports. That is what makes it possible to go from an offline
demo to a real run by changing environment variables, without touching a line of business
logic.
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
from revenue_agent.adapters.communication.whatsapp_handoff import WhatsAppHandoffAdapter
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
from revenue_agent.ports.communication import HandoffPort, WhatsAppPort
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
    handoff = _build_handoff(settings, whatsapp)
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


def _build_handoff(settings: Settings, whatsapp: WhatsAppPort) -> HandoffPort:
    """Real destinations for the handoff.

    The console remains a last resort, never the sole destination: this is the most important
    gesture in the product, it cannot end in a stream nobody reads.

    The three destinations are complementary rather than redundant. The HubSpot task is durable
    but passive — it waits for someone to open the CRM. Teams lands where the team works.
    WhatsApp lands in a pocket, which for an approval that needs an answer within the hour is
    often the only one that gets a reply.
    """
    destinations: list[HandoffPort] = []

    if settings.hubspot.enabled:
        destinations.append(
            HubSpotHandoffAdapter(settings.hubspot.token, settings.handoff.hubspot_owner_id)
        )
    if settings.handoff.teams_enabled:
        destinations.append(
            TeamsHandoffAdapter(
                settings.handoff.teams_webhook_url, settings.handoff.approval_url
            )
        )
    if settings.handoff.whatsapp_enabled:
        # Reuses the Meta connection already wired for prospects — only the recipient changes.
        # Deliberately bypasses the outbound policy: a rate cap meant to stop us hounding a
        # prospect must never stop us warning a colleague that an action is waiting.
        destinations.append(
            WhatsAppHandoffAdapter(
                whatsapp,
                settings.handoff.whatsapp_recipients,
                settings.handoff.approval_url,
            )
        )

    if not destinations:
        logger.warning(
            "No handoff destination configured — handoffs and approval requests will not "
            "reach any human outside the logs"
        )
        return ConsoleHandoffAdapter()

    return CompositeHandoffAdapter(destinations, ConsoleHandoffAdapter())


def _build_reviewer(settings: Settings) -> MessageReviewPort:
    """Two-layer review: the lexical baseline always, semantic judgement whenever a model is
    available.

    The baseline is never removed. A classifier sees what a dictionary cannot, but it is
    probabilistic and fallible: replacing the baseline with it alone would trade a guarantee for
    a probability.
    """
    lexical = LexicalMessageReviewer()
    if not settings.openrouter.enabled:
        logger.warning(
            "Semantic review unavailable (no OpenRouter key) — "
            "only the lexical baseline protects outbound messages"
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

    # Late import: LangChain and LangGraph are heavy to load, and there is no point paying for
    # that when the application runs without an LLM (tests, offline demo).
    from revenue_agent.adapters.intelligence.langgraph_agent import LangGraphDecisionAgent

    return LangGraphDecisionAgent(settings=settings, actions=actions)


def _log_wiring(settings: Settings) -> None:
    degraded = settings.degraded_components()
    if degraded:
        logger.warning("Components in simulated mode: %s", " ; ".join(degraded))
    else:
        logger.info("All integrations are configured.")
