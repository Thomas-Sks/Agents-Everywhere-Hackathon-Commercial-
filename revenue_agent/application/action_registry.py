"""Registry of sales actions — everything the agent knows how to do.

Two drivers consume this registry:

* the LLM decision engine, which exposes every action as a LangGraph tool;
* the Retell webhook, called during a phone call, when it is the voice agent deciding to
  execute an action.

Both must produce exactly the same effect — hence a single registry rather than two parallel
implementations doomed to drift apart. Every action is named, resolves its contact details from
the CRM, and systematically records its trace: an action that leaves nothing behind in the CRM
never happened as far as the human sales rep is concerned.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from revenue_agent.config import PolicySettings
from revenue_agent.domain import policy
from revenue_agent.domain.approvals import PendingApproval
from revenue_agent.domain.errors import ChannelUnavailable
from revenue_agent.domain.models import Channel, Interaction, Objection, Opportunity, Stance
from revenue_agent.domain.policy import ActionKind, OutboundAction, Verdict
from revenue_agent.domain.review import MessageUnderReview
from revenue_agent.domain.triage import KnownDealState
from revenue_agent.ports.approvals import ApprovalPort
from revenue_agent.ports.communication import EmailPort, HandoffPort, VoicePort, WhatsAppPort
from revenue_agent.ports.crm import CatalogPort, CrmPort
from revenue_agent.ports.intelligence import EnrichmentPort, MessageReviewPort
from revenue_agent.ports.state import ScanStatePort

logger = logging.getLogger(__name__)

MAX_HISTORY_EXPOSED = 15


class ActionRegistry:
    def __init__(
        self,
        *,
        crm: CrmPort,
        catalog: CatalogPort,
        email: EmailPort,
        whatsapp: WhatsAppPort,
        voice: VoicePort,
        handoff: HandoffPort,
        enrichment: EnrichmentPort,
        scan_state: ScanStatePort,
        approvals: ApprovalPort,
        reviewer: MessageReviewPort,
        policy_settings: PolicySettings,
    ) -> None:
        self._crm = crm
        self._catalog = catalog
        self._email = email
        self._whatsapp = whatsapp
        self._voice = voice
        self._handoff = handoff
        self._enrichment = enrichment
        self._scan_state = scan_state
        self._approvals = approvals
        self._reviewer = reviewer
        self._policy = policy_settings

    # -- Reads --------------------------------------------------------------------

    def get_opportunity_context(self, opportunity_id: str) -> str:
        opportunity = self._crm.load_opportunity(opportunity_id)
        return json.dumps(serialise_opportunity(opportunity), ensure_ascii=False, indent=2)

    def get_product_info(self, product_id: str = "") -> str:
        if product_id:
            product = self._catalog.get_product(product_id)
            if product is None:
                return f"Produit '{product_id}' introuvable."
            products = [product]
        else:
            products = self._catalog.list_products()

        return json.dumps(
            [
                {
                    "id": product.id,
                    "nom": product.name,
                    "categorie": product.category,
                    "prix": product.price,
                    "description": product.description,
                }
                for product in products
            ],
            ensure_ascii=False,
            indent=2,
        )

    def research_prospect(self, company_name: str) -> str:
        insights = self._enrichment.research_company(company_name)
        if not insights:
            return f"Aucun signal récent trouvé pour {company_name}."
        return json.dumps(
            [
                {
                    "titre": insight.title,
                    "url": insight.url,
                    "resume": insight.summary,
                    "publie_le": insight.published_at.isoformat()
                    if insight.published_at
                    else None,
                }
                for insight in insights
            ],
            ensure_ascii=False,
            indent=2,
        )

    # -- CRM writes ---------------------------------------------------------------

    def record_interaction(self, opportunity_id: str, channel: str, summary: str) -> str:
        self._crm.record_interaction(
            opportunity_id,
            Interaction(
                channel=_parse_channel(channel),
                summary=summary,
                occurred_at=_now(),
            ),
        )
        self._remember_decision(opportunity_id)
        return "Interaction enregistrée dans le CRM."

    def update_opportunity(
        self,
        opportunity_id: str,
        stage: str = "",
        probability: int = -1,
        objection: str = "",
        objection_root_cause: str = "",
        next_steps: str = "",
    ) -> str:
        self._crm.update_opportunity(
            opportunity_id,
            stage=stage or None,
            probability=probability if probability >= 0 else None,
            objection=(
                Objection(
                    id=uuid.uuid4().hex[:6],
                    text=objection,
                    root_cause=objection_root_cause or "inconnue",
                    raised_at=_now(),
                )
                if objection
                else None
            ),
            next_steps=next_steps or None,
        )
        return "Opportunité mise à jour dans le CRM."

    def update_stakeholder(
        self,
        opportunity_id: str,
        name: str,
        stance: str,
        role: str = "",
        notes: str = "",
    ) -> str:
        """Record who decides, who influences, who blocks.

        The prompt asks the agent to keep this map up to date; without this action it could
        only ever read it. A stance observed on a call and never written down is lost at the
        next cycle — and the deal keeps looking healthy because nobody recorded that the budget
        holder is against it.
        """
        parsed = _parse_stance(stance)
        if parsed is None:
            valid = ", ".join(option.value for option in Stance)
            return f"Posture '{stance}' inconnue. Valeurs acceptées : {valid}."

        if not name.strip():
            return "Le nom de l'interlocuteur est obligatoire."

        self._crm.update_stakeholder(
            opportunity_id,
            name=name.strip(),
            stance=parsed,
            role=role.strip(),
            notes=notes.strip(),
        )
        self._trace(
            opportunity_id,
            Channel.DECISION,
            f"Partie prenante mise à jour : {name.strip()} — posture {parsed.value}",
        )
        return f"{name.strip()} enregistré(e) comme « {parsed.value} » dans le CRM."

    def resolve_objection(self, opportunity_id: str, objection_id: str, resolution: str) -> str:
        """Close an objection that has been dealt with.

        Without this action an objection stayed open forever: the context was polluted on every
        cycle and stakes-based routing remained pinned to the most expensive model.
        """
        if not self._crm.resolve_objection(opportunity_id, objection_id, resolution):
            return (
                f"Objection '{objection_id}' introuvable sur cette opportunité. "
                "Relis le contexte pour récupérer l'identifiant exact."
            )
        self._trace(
            opportunity_id,
            Channel.DECISION,
            f"Objection {objection_id} résolue — {resolution}",
        )
        return f"Objection {objection_id} marquée comme résolue."

    # -- Communication ------------------------------------------------------------

    def send_email(self, opportunity_id: str, subject: str, body: str) -> str:
        opportunity = self._crm.load_opportunity(opportunity_id)
        address = opportunity.reachable_channels().email
        if not address:
            raise ChannelUnavailable(opportunity_id, "email")

        refusal = self._guard(
            opportunity,
            ActionKind.EMAIL,
            recipient=address,
            content=f"{subject}\n{body}",
            payload={"subject": subject, "body": body},
        )
        if refusal is not None:
            return refusal

        message_id = self._email.send(to=address, subject=subject, body=body)
        self._after_send(
            opportunity_id, Channel.EMAIL, f"Email envoyé à {address} — objet : {subject}"
        )
        return f"Email envoyé à {address} (id={message_id})."

    def send_whatsapp_message(self, opportunity_id: str, message: str) -> str:
        opportunity = self._crm.load_opportunity(opportunity_id)
        phone = opportunity.reachable_channels().phone
        if not phone:
            raise ChannelUnavailable(opportunity_id, "whatsapp")

        refusal = self._guard(
            opportunity,
            ActionKind.WHATSAPP,
            recipient=phone,
            content=message,
            payload={"message": message},
        )
        if refusal is not None:
            return refusal

        message_id = self._whatsapp.send(to_phone_number=phone, message=message)
        self._after_send(opportunity_id, Channel.WHATSAPP, f"Message WhatsApp envoyé à {phone}")
        return f"Message WhatsApp envoyé à {phone} (id={message_id})."

    def place_phone_call(self, opportunity_id: str, objective: str) -> str:
        opportunity = self._crm.load_opportunity(opportunity_id)
        phone = opportunity.reachable_channels().phone
        if not phone:
            raise ChannelUnavailable(opportunity_id, "voice")

        refusal = self._guard(
            opportunity,
            ActionKind.CALL,
            recipient=phone,
            content=objective,
            payload={"objective": objective},
        )
        if refusal is not None:
            return refusal

        call_id = self._voice.place_call(
            to_phone_number=phone, opportunity=opportunity, objective=objective
        )
        self._after_send(
            opportunity_id, Channel.VOICE, f"Appel déclenché vers {phone} — objectif : {objective}"
        )
        return f"Appel en cours vers {phone} (call_id={call_id})."

    # -- Guardrails ---------------------------------------------------------------

    def _guard(
        self,
        opportunity: Opportunity,
        kind: ActionKind,
        *,
        recipient: str,
        content: str,
        payload: dict[str, str],
    ) -> str | None:
        """Apply the policy before any outbound action.

        Returns `None` if the action may go out, otherwise the message to hand back to the
        model. We return a message rather than an exception so the agent can adapt — rewrite
        without the discount, pick another channel — instead of hitting an opaque crash.
        """
        review = (
            self._reviewer.review(_under_review(opportunity, kind, content))
            if policy.needs_message_review(self._policy)
            else None
        )

        decision = policy.evaluate(
            OutboundAction(
                kind=kind,
                opportunity_id=opportunity.id,
                recipient=recipient,
                content=content,
                opportunity_amount=opportunity.amount,
            ),
            settings=self._policy,
            catalogue_prices=self._catalogue_prices(),
            outbound_last_24h=self._approvals.count_recent_outbound(opportunity.id),
            review=review,
        )

        if decision.allowed:
            return None

        logger.warning(
            "Action %s sur %s : %s (règle %s)",
            kind.value,
            opportunity.id,
            decision.verdict.value,
            decision.rule,
        )
        self._crm.record_interaction(
            opportunity.id,
            Interaction(
                channel=Channel.DECISION,
                summary=f"[POLITIQUE] {kind.value} {decision.verdict.value} — {decision.reason}",
                occurred_at=_now(),
            ),
        )

        if decision.verdict is Verdict.BLOCK:
            return f"ACTION BLOQUÉE ({decision.rule}) : {decision.reason}"

        approval = self._approvals.submit(
            PendingApproval(
                id="",
                opportunity_id=opportunity.id,
                company=opportunity.company,
                kind=kind,
                recipient=recipient,
                rule=decision.rule,
                reason=decision.reason,
                requested_at=_now(),
                payload=payload,
            )
        )
        # A held action nobody is told about is not "pending", it is lost. The
        # notification is therefore inseparable from placing it in the queue.
        try:
            self._handoff.notify_pending_approval(
                opportunity=opportunity,
                approval_id=approval.id,
                channel=kind.value,
                reason=decision.reason,
                preview=" ".join(payload.values()),
            )
        except Exception:  # noqa: BLE001 - the action stays queued even if the ping fails
            logger.exception("Notification de validation non remise pour %s", approval.id)

        return (
            f"ACTION EN ATTENTE DE VALIDATION (réf. {approval.id}) : {decision.reason} "
            "Le message n'est pas parti. Un humain doit l'approuver."
        )

    def _catalogue_prices(self) -> tuple[float, ...]:
        return tuple(product.price for product in self._catalog.list_products())

    def _after_send(self, opportunity_id: str, channel: Channel, summary: str) -> None:
        self._approvals.record_sent(opportunity_id)
        self._trace(opportunity_id, channel, summary)

    def execute_approved(self, approval: PendingApproval) -> str:
        """Replay an action approved by a human, bypassing the approval rules but not the
        operational blocks (simulated mode, allow-listed recipients)."""
        opportunity = self._crm.load_opportunity(approval.opportunity_id)
        content = " ".join(approval.payload.values())

        decision = policy.evaluate(
            OutboundAction(
                kind=approval.kind,
                opportunity_id=approval.opportunity_id,
                recipient=approval.recipient,
                content=content,
                opportunity_amount=opportunity.amount,
            ),
            settings=self._policy,
            catalogue_prices=self._catalogue_prices(),
            outbound_last_24h=self._approvals.count_recent_outbound(approval.opportunity_id),
            human_approved=True,
        )
        if not decision.allowed:
            return f"Action toujours bloquée après validation ({decision.rule}) : {decision.reason}"

        if approval.kind is ActionKind.EMAIL:
            self._email.send(
                to=approval.recipient,
                subject=approval.payload.get("subject", ""),
                body=approval.payload.get("body", ""),
            )
            summary = f"Email envoyé à {approval.recipient} après validation humaine"
            channel = Channel.EMAIL
        elif approval.kind is ActionKind.WHATSAPP:
            self._whatsapp.send(
                to_phone_number=approval.recipient, message=approval.payload.get("message", "")
            )
            summary = f"Message WhatsApp envoyé à {approval.recipient} après validation humaine"
            channel = Channel.WHATSAPP
        else:
            self._voice.place_call(
                to_phone_number=approval.recipient,
                opportunity=opportunity,
                objective=approval.payload.get("objective", ""),
            )
            summary = f"Appel déclenché vers {approval.recipient} après validation humaine"
            channel = Channel.VOICE

        self._after_send(approval.opportunity_id, channel, summary)
        return summary

    # -- Decisions ----------------------------------------------------------------

    def schedule_follow_up(self, opportunity_id: str, reason: str, due_date: str) -> str:
        due_at = _parse_date(due_date)
        if due_at is None:
            return f"Date invalide : {due_date!r}. Format attendu AAAA-MM-JJ."

        self._scan_state.schedule_follow_up(opportunity_id, due_at)
        self._trace(
            opportunity_id,
            Channel.DECISION,
            f"Attente décidée jusqu'au {due_date} — motif : {reason}",
        )
        return f"Reprise programmée le {due_date}."

    def escalate_to_human(
        self, opportunity_id: str, reason: str, urgency: str, context_brief: str
    ) -> str:
        opportunity = self._crm.load_opportunity(opportunity_id)
        self._handoff.escalate(
            opportunity=opportunity,
            reason=reason,
            urgency=urgency,
            context_brief=context_brief,
        )
        self._trace(
            opportunity_id,
            Channel.HANDOFF,
            f"Escaladé à un humain ({urgency}) — {reason}",
        )
        return "Dossier transmis à un commercial humain avec le contexte complet."

    # -- Internal -----------------------------------------------------------------

    def _trace(self, opportunity_id: str, channel: Channel, summary: str) -> None:
        self._crm.record_interaction(
            opportunity_id,
            Interaction(channel=channel, summary=summary, occurred_at=_now()),
        )
        self._remember_decision(opportunity_id)

    def _remember_decision(self, opportunity_id: str) -> None:
        """Feeds the triage inactivity rule."""
        known = self._scan_state.get_known_states().get(opportunity_id, KnownDealState())
        self._scan_state.upsert_known_state(
            opportunity_id,
            KnownDealState(
                stage=known.stage,
                last_decision_at=_now(),
                follow_up_due_at=known.follow_up_due_at,
            ),
        )


def serialise_opportunity(opportunity: Opportunity) -> dict:
    """The opportunity as exposed to the model.

    Reachable channels are explicit: the agent has to know what it *can* do before choosing
    what to do.
    """
    channels = opportunity.reachable_channels()
    return {
        "id": opportunity.id,
        "entreprise": opportunity.company,
        "stade": opportunity.stage,
        "probabilite": opportunity.probability,
        "montant": opportunity.amount,
        "source": opportunity.source,
        "interlocuteurs": [
            {
                "nom": stakeholder.name,
                "role": stakeholder.role,
                "posture": stakeholder.stance.value,
                "email": stakeholder.email,
                "telephone": stakeholder.phone,
                "notes": stakeholder.notes,
            }
            for stakeholder in opportunity.stakeholders
        ],
        "objections_ouvertes": [
            {
                # The identifier is exposed so the agent can close the objection
                # precisely, without relying on a text match.
                "id": objection.id,
                "texte": objection.text,
                "cause_probable": objection.root_cause,
            }
            for objection in opportunity.unresolved_objections()
        ],
        "historique": [
            {
                "canal": interaction.channel.value,
                "resume": interaction.summary,
                "date": interaction.occurred_at.isoformat(),
            }
            for interaction in opportunity.history[-MAX_HISTORY_EXPOSED:]
        ],
        "prochaines_etapes": opportunity.next_steps,
        "risques": opportunity.risk_notes,
        "canaux_joignables": {
            "email": channels.email,
            "telephone": channels.phone,
            "disponibles": [channel.value for channel in channels.available],
        },
        "decideur_engage": opportunity.has_decision_maker_engaged(),
    }


def _under_review(opportunity: Opportunity, kind: ActionKind, content: str) -> MessageUnderReview:
    """The reviewer judges the message in context: the same sentence does not carry the same
    weight on first contact and at the end of a negotiation."""
    return MessageUnderReview(
        channel=kind.value,
        company=opportunity.company,
        stage=opportunity.stage,
        content=content,
        amount=opportunity.amount,
        open_objections=tuple(o.text for o in opportunity.unresolved_objections()),
        interactions_count=len(opportunity.history),
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_stance(raw: str) -> Stance | None:
    """Unlike channels, an unknown stance is not silently downgraded: a wrong stance corrupts
    the stakeholder map, so the model is told instead."""
    try:
        return Stance(raw.strip().casefold())
    except (ValueError, AttributeError):
        return None


def _parse_channel(raw: str) -> Channel:
    try:
        return Channel(raw.strip().lower())
    except (ValueError, AttributeError):
        return Channel.SIGNAL


def _parse_date(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").replace(tzinfo=UTC)
    except (ValueError, AttributeError):
        return None
