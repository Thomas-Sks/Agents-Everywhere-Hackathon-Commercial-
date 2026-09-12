"""Registre des actions commerciales — l'ensemble de ce que l'agent sait faire.

Deux pilotes consomment ce registre :

* le moteur de décision LLM, qui expose chaque action comme un tool LangGraph ;
* le webhook Retell, appelé pendant un appel téléphonique, quand c'est l'agent vocal qui
  décide d'exécuter une action.

Les deux doivent produire exactement le même effet — d'où un registre unique plutôt que deux
implémentations parallèles vouées à diverger. Toute action est nommée, résout ses coordonnées
depuis le CRM, et consigne systématiquement sa trace : une action dont il ne reste rien dans
le CRM n'a pas eu lieu du point de vue du commercial humain.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from revenue_agent.config import PolicySettings
from revenue_agent.domain import policy
from revenue_agent.domain.approvals import PendingApproval
from revenue_agent.domain.errors import ChannelUnavailable
from revenue_agent.domain.models import Channel, Interaction, Objection, Opportunity
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

    # -- Lecture ------------------------------------------------------------------

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

    # -- Écriture CRM -------------------------------------------------------------

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

    # -- Garde-fous ---------------------------------------------------------------

    def _guard(
        self,
        opportunity: Opportunity,
        kind: ActionKind,
        *,
        recipient: str,
        content: str,
        payload: dict[str, str],
    ) -> str | None:
        """Applique la politique avant toute action sortante.

        Retourne `None` si l'action peut partir, sinon le message à rendre au modèle. On rend
        un message plutôt qu'une exception pour que l'agent puisse s'adapter — réécrire sans
        la remise, choisir un autre canal — au lieu de subir un plantage opaque.
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
        """Rejoue une action validée par un humain, en contournant les règles de validation
        mais pas les blocages d'exploitation (mode simulation, destinataires autorisés)."""
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

    # -- Décisions ----------------------------------------------------------------

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

    # -- Interne ------------------------------------------------------------------

    def _trace(self, opportunity_id: str, channel: Channel, summary: str) -> None:
        self._crm.record_interaction(
            opportunity_id,
            Interaction(channel=channel, summary=summary, occurred_at=_now()),
        )
        self._remember_decision(opportunity_id)

    def _remember_decision(self, opportunity_id: str) -> None:
        """Nourrit la règle d'inactivité du triage."""
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
    """Vue de l'opportunité telle qu'exposée au modèle.

    Les canaux joignables sont explicites : l'agent doit savoir ce qu'il peut faire avant de
    choisir quoi faire.
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
            {"texte": objection.text, "cause_probable": objection.root_cause}
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
    """Le relecteur juge le message dans son contexte : la même phrase n'a pas le même poids
    au premier contact et en fin de négociation."""
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
