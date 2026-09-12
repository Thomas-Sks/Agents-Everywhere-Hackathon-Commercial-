"""Domain model — no dependency on any framework, SDK or transport.

This is where the representation of a sales opportunity lives, as the business understands it.
Adapters (HubSpot, local JSON...) translate into these types; the decision engine knows nothing
else.
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
    DECISION_MAKER = "decision_maker"
    BLOCKER = "blocker"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


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
    """An objection raised by the prospect.

    `id` is essential: objections come up over the course of the conversation, and we need to be
    able to designate *which one* we are closing. Without an identifier, resolution can only rely
    on text matching, which breaks as soon as the wording varies.
    """

    text: str
    root_cause: str = "unknown"
    resolved: bool = False
    raised_at: datetime | None = None
    id: str = ""
    resolution: str = ""


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
    """What the agent can actually do for this opportunity.

    An agent cannot choose a channel it has no contact details for: this is the constraint that
    links "fetch the data from the CRM" to "send a message".
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
    # Sales rep who owns the deal on the CRM side — the natural recipient of a handoff.
    owner_id: str = ""

    def reachable_channels(self) -> ReachableChannels:
        """First available contact details, favouring the champion, then the decision maker."""
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
    """Lightweight view returned by the CRM scan — enough to triage, not enough to decide.

    Triage works on these snapshots (no LLM call, no extra network call); only the opportunities
    that pass are then loaded in full.
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
    """Result of a web enrichment lookup (Exa) on the prospect's company."""

    title: str
    url: str
    summary: str
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CallOutcome:
    """Outcome of a phone call, as reported by the voice platform."""

    opportunity_id: str
    transcript: str
    summary: str = ""
    sentiment: str = ""
    duration_seconds: int = 0
    successful: bool = True
    metadata: dict[str, str] = field(default_factory=dict)
