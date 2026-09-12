"""Modèle de domaine — aucune dépendance à un framework, un SDK ou un transport.

C'est ici que vit la représentation d'une opportunité commerciale telle que le métier la
comprend. Les adapters (HubSpot, JSON local...) traduisent vers ces types ; le moteur de
décision ne connaît qu'eux.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Channel(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    VOICE = "voice"
    MEETING = "meeting"
    SIGNAL = "signal"
    HANDOFF = "handoff"
    DECISION = "decision"


class Stance(StrEnum):
    CHAMPION = "champion"
    DECISION_MAKER = "decideur"
    BLOCKER = "opposant"
    NEUTRAL = "neutre"
    UNKNOWN = "inconnu"


@dataclass(frozen=True, slots=True)
class Stakeholder:
    name: str
    role: str = ""
    email: str | None = None
    phone: str | None = None
    stance: Stance = Stance.UNKNOWN
    notes: str = ""


@dataclass(frozen=True, slots=True)
class Objection:
    text: str
    root_cause: str = "inconnue"
    resolved: bool = False
    raised_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Interaction:
    channel: Channel
    summary: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class Product:
    id: str
    name: str
    category: str
    price: float
    description: str


@dataclass(frozen=True, slots=True)
class ReachableChannels:
    """Ce que l'agent peut réellement faire pour cette opportunité.

    Un agent ne peut pas choisir un canal dont il n'a pas la coordonnée : c'est la contrainte
    qui relie « récupérer la data du CRM » à « envoyer un message ».
    """

    email: str | None = None
    phone: str | None = None

    @property
    def available(self) -> tuple[Channel, ...]:
        channels: list[Channel] = []
        if self.email:
            channels.append(Channel.EMAIL)
        if self.phone:
            channels.extend((Channel.WHATSAPP, Channel.VOICE))
        return tuple(channels)

    @property
    def is_empty(self) -> bool:
        return not self.available


@dataclass(frozen=True, slots=True)
class Opportunity:
    id: str
    company: str
    stage: str = "discovery"
    probability: int = 0
    amount: float | None = None
    stakeholders: tuple[Stakeholder, ...] = ()
    objections: tuple[Objection, ...] = ()
    history: tuple[Interaction, ...] = ()
    next_steps: str = ""
    risk_notes: str = ""
    last_activity_at: datetime | None = None
    source: str = "local"

    def reachable_channels(self) -> ReachableChannels:
        """Première coordonnée disponible, en privilégiant le champion puis le décideur."""
        ordered = sorted(self.stakeholders, key=self._stakeholder_priority)
        email = next((s.email for s in ordered if s.email), None)
        phone = next((s.phone for s in ordered if s.phone), None)
        return ReachableChannels(email=email, phone=phone)

    @staticmethod
    def _stakeholder_priority(stakeholder: Stakeholder) -> int:
        order = {
            Stance.CHAMPION: 0,
            Stance.DECISION_MAKER: 1,
            Stance.NEUTRAL: 2,
            Stance.UNKNOWN: 3,
            Stance.BLOCKER: 4,
        }
        return order.get(stakeholder.stance, 3)

    def unresolved_objections(self) -> tuple[Objection, ...]:
        return tuple(o for o in self.objections if not o.resolved)

    def has_decision_maker_engaged(self) -> bool:
        return any(s.stance is Stance.DECISION_MAKER for s in self.stakeholders)


@dataclass(frozen=True, slots=True)
class DealSnapshot:
    """Vue légère renvoyée par le scan du CRM — assez pour trier, pas assez pour décider.

    Le triage travaille sur ces snapshots (aucun appel LLM, aucun appel réseau
    supplémentaire) ; seules les opportunités retenues sont ensuite chargées en entier.
    """

    id: str
    name: str
    stage: str
    last_modified_at: datetime
    amount: float | None = None


@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[DealSnapshot, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ProspectInsight:
    """Résultat d'un enrichissement web (Exa) sur l'entreprise du prospect."""

    title: str
    url: str
    summary: str
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CallOutcome:
    """Résultat d'un appel téléphonique, tel que remonté par la plateforme voix."""

    opportunity_id: str
    transcript: str
    summary: str = ""
    sentiment: str = ""
    duration_seconds: int = 0
    successful: bool = True
    metadata: dict[str, str] = field(default_factory=dict)
