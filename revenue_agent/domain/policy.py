"""Limites d'autonomie — appliquées par le code, pas par le prompt.

Le prompt du moteur de décision décrit au modèle ce qu'il a le droit de faire. Cette politique
l'impose. La distinction est fondamentale : un prompt est une consigne, un contrôle d'accès est
une garantie. Rien ne doit dépendre de la bonne volonté du modèle pour empêcher un email de
partir à un vrai prospect, et `escalate_to_human` ne peut pas être la seule protection puisque
c'est une action que le modèle *choisit* d'appeler.

Toutes les règles sont des fonctions pures évaluées **avant** l'exécution d'une action
sortante. Elles sont donc testables exhaustivement, et auditables : chaque refus nomme la règle
qui l'a produit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from revenue_agent.config import AgentMode, PolicySettings
from revenue_agent.domain.review import ReviewFinding


class ActionKind(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    CALL = "appel téléphonique"


class Verdict(StrEnum):
    ALLOW = "autorisé"
    REQUIRE_APPROVAL = "validation humaine requise"
    BLOCK = "bloqué"


@dataclass(frozen=True, slots=True)
class OutboundAction:
    """Une action sur le point de toucher un prospect réel."""

    kind: ActionKind
    opportunity_id: str
    recipient: str
    content: str
    opportunity_amount: float | None = None


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    verdict: Verdict
    rule: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.verdict is Verdict.REQUIRE_APPROVAL


# Un montant explicitement libellé en euros. Volontairement restrictif : on n'attrape que les
# chiffres accolés à un marqueur monétaire, pour ne pas confondre « 10 utilisateurs » ou une
# année avec un prix.
_PRICE_PATTERN = re.compile(
    r"(\d[\d\s.,]*)\s*(?:€|eur\b|euros?\b)|(?:€|eur\b)\s*(\d[\d\s.,]*)", re.IGNORECASE
)

_PRICE_TOLERANCE = 0.01


def evaluate(
    action: OutboundAction,
    *,
    settings: PolicySettings,
    catalogue_prices: tuple[float, ...] = (),
    outbound_last_24h: int = 0,
    review: ReviewFinding | None = None,
    human_approved: bool = False,
) -> PolicyDecision:
    """Verdict sur une action sortante.

    Les règles sont ordonnées de la plus restrictive à la plus permissive : le premier refus
    l'emporte, et un blocage prime toujours sur une demande de validation.

    `review` est le verdict de la relecture du message (lexicale puis sémantique) : il est
    calculé en dehors du domaine, qui reste ainsi une fonction pure sans appel réseau.

    `human_approved` est le rejeu d'une action explicitement validée par un humain : les règles
    de validation sont alors levées, mais **pas les blocages** — un opérateur qui approuve une
    remise ne doit pas pouvoir contourner le mode simulation ni la liste de destinataires
    autorisés, qui sont des garde-fous d'exploitation et non des arbitrages commerciaux.
    """
    if settings.mode is AgentMode.DRY_RUN:
        return PolicyDecision(
            Verdict.BLOCK,
            "mode_dry_run",
            "L'agent tourne en mode simulation : aucune action sortante n'est émise.",
        )

    if settings.allowed_recipients and not _is_allowed(action.recipient, settings):
        return PolicyDecision(
            Verdict.BLOCK,
            "destinataire_hors_liste",
            f"{action.recipient} ne figure pas dans la liste des destinataires autorisés.",
        )

    unknown_price = _unknown_price_quoted(action.content, catalogue_prices)
    if unknown_price is not None:
        return PolicyDecision(
            Verdict.BLOCK,
            "prix_hors_catalogue",
            f"Le message annonce un prix ({unknown_price:,.0f} €) absent du catalogue. "
            "Un prix inventé engage l'entreprise : vérifie le catalogue produit.",
        )

    if outbound_last_24h >= settings.max_outbound_per_day:
        return PolicyDecision(
            Verdict.BLOCK,
            "cadence_maximale",
            f"{outbound_last_24h} message(s) déjà envoyé(s) à ce prospect sur 24 h "
            f"(maximum {settings.max_outbound_per_day}). Insister nuirait à la relation.",
        )

    if human_approved:
        return PolicyDecision(
            Verdict.ALLOW, "validation_humaine", "Action explicitement validée par un humain."
        )

    if settings.mode is AgentMode.SUPERVISED:
        return PolicyDecision(
            Verdict.REQUIRE_APPROVAL,
            "mode_supervise",
            "L'agent est en mode supervisé : toute action sortante attend une validation.",
        )

    if review is not None and review.requires_human:
        return PolicyDecision(
            Verdict.REQUIRE_APPROVAL,
            f"relecture_{review.category.value}",
            review.describe(),
        )

    if (
        action.opportunity_amount is not None
        and action.opportunity_amount >= settings.max_autonomous_amount
    ):
        return PolicyDecision(
            Verdict.REQUIRE_APPROVAL,
            "montant_eleve",
            f"L'opportunité porte sur {action.opportunity_amount:,.0f} €, au-delà du seuil "
            f"d'autonomie ({settings.max_autonomous_amount:,.0f} €).",
        )

    return PolicyDecision(Verdict.ALLOW, "autonomie", "Action dans les limites accordées.")


def needs_message_review(settings: PolicySettings) -> bool:
    """La relecture ne sert qu'en mode autonome.

    Dans les autres modes, le verdict est connu d'avance — tout est retenu ou tout est bloqué —
    et payer un appel de modèle pour confirmer une décision déjà prise serait du gaspillage.
    """
    return settings.mode is AgentMode.AUTONOMOUS


def _is_allowed(recipient: str, settings: PolicySettings) -> bool:
    normalised = recipient.strip().lower()
    return any(normalised == allowed.strip().lower() for allowed in settings.allowed_recipients)


def _unknown_price_quoted(content: str, catalogue_prices: tuple[float, ...]) -> float | None:
    """Premier prix cité qui ne correspond à aucun prix du catalogue.

    Empêche structurellement une hallucination tarifaire de partir chez un prospect — le
    prompt demande déjà au modèle de vérifier, mais demander n'est pas garantir.
    """
    for quoted in _extract_prices(content):
        if not any(abs(quoted - known) <= _PRICE_TOLERANCE for known in catalogue_prices):
            return quoted
    return None


def _extract_prices(content: str) -> list[float]:
    prices: list[float] = []
    for match in _PRICE_PATTERN.finditer(content):
        raw = match.group(1) or match.group(2) or ""
        value = _to_float(raw)
        if value is not None:
            prices.append(value)
    return prices


def _to_float(raw: str) -> float | None:
    cleaned = raw.replace(" ", "").replace(" ", "").replace("\xa0", "")
    # Format français : le point sépare les milliers, la virgule les décimales.
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None
