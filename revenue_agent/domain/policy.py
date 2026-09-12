"""Autonomy limits — enforced by code, not by the prompt.

The decision engine's prompt tells the model what it is allowed to do. This policy enforces it.
The distinction is fundamental: a prompt is an instruction, an access control is a guarantee.
Nothing should depend on the model's goodwill to stop an email from reaching a real prospect,
and `escalate_to_human` cannot be the only protection since it is an action the model *chooses*
to call.

Every rule is a pure function evaluated **before** an outbound action is executed. They are
therefore exhaustively testable, and auditable: every refusal names the rule that produced it.
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
    CALL = "phone call"


class Verdict(StrEnum):
    ALLOW = "allowed"
    REQUIRE_APPROVAL = "approval required"
    BLOCK = "blocked"


@dataclass(frozen=True, slots=True)
class OutboundAction:
    """An action about to reach a real prospect."""

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


# An amount explicitly denominated in euros. Deliberately restrictive: we only catch digits
# adjacent to a currency marker, so as not to mistake "10 users" or a year for a price.
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
    """Verdict on an outbound action.

    The rules are ordered from the most restrictive to the most permissive: the first refusal
    wins, and a block always takes precedence over an approval request.

    `review` is the verdict of the message review (lexical, then semantic): it is computed
    outside the domain, which thus remains a pure function with no network call.

    `human_approved` is the replay of an action a human explicitly approved: the approval rules
    are then lifted, but **not the blocks** — an operator who approves a discount must not be
    able to bypass simulated mode or the allow-list of recipients, which are operational
    guardrails rather than sales judgements.
    """
    if settings.mode is AgentMode.DRY_RUN:
        return PolicyDecision(
            Verdict.BLOCK,
            "dry_run_mode",
            "The agent is running in simulation mode: no outbound action is emitted.",
        )

    if settings.allowed_recipients and not _is_allowed(action.recipient, settings):
        return PolicyDecision(
            Verdict.BLOCK,
            "recipient_not_allowed",
            f"{action.recipient} is not on the list of allowed recipients.",
        )

    unknown_price = _unknown_price_quoted(action.content, catalogue_prices)
    if unknown_price is not None:
        return PolicyDecision(
            Verdict.BLOCK,
            "price_not_in_catalogue",
            f"The message quotes a price ({unknown_price:,.0f} €) that is not in the catalogue. "
            "An invented price commits the company: check the product catalogue.",
        )

    if outbound_last_24h >= settings.max_outbound_per_day:
        return PolicyDecision(
            Verdict.BLOCK,
            "rate_limit",
            f"{outbound_last_24h} message(s) already sent to this prospect in 24 h "
            f"(maximum {settings.max_outbound_per_day}). Pushing further would hurt the "
            "relationship.",
        )

    if human_approved:
        return PolicyDecision(
            Verdict.ALLOW, "human_approved", "Action explicitly approved by a human."
        )

    if settings.mode is AgentMode.SUPERVISED:
        return PolicyDecision(
            Verdict.REQUIRE_APPROVAL,
            "supervised_mode",
            "The agent is in supervised mode: every outbound action awaits approval.",
        )

    if review is not None and review.requires_human:
        return PolicyDecision(
            Verdict.REQUIRE_APPROVAL,
            f"review_{review.category.value}",
            review.describe(),
        )

    if (
        action.opportunity_amount is not None
        and action.opportunity_amount >= settings.max_autonomous_amount
    ):
        return PolicyDecision(
            Verdict.REQUIRE_APPROVAL,
            "high_amount",
            f"The opportunity is worth {action.opportunity_amount:,.0f} €, above the autonomy "
            f"threshold ({settings.max_autonomous_amount:,.0f} €).",
        )

    return PolicyDecision(Verdict.ALLOW, "autonomy", "Action within the granted limits.")


def needs_message_review(settings: PolicySettings) -> bool:
    """Review is only useful in autonomous mode.

    In the other modes the verdict is known in advance — everything is held or everything is
    blocked — and paying for a model call to confirm a decision already made would be waste.
    """
    return settings.mode is AgentMode.AUTONOMOUS


def _is_allowed(recipient: str, settings: PolicySettings) -> bool:
    normalised = recipient.strip().lower()
    return any(normalised == allowed.strip().lower() for allowed in settings.allowed_recipients)


def _unknown_price_quoted(content: str, catalogue_prices: tuple[float, ...]) -> float | None:
    """First quoted price that matches no price in the catalogue.

    Structurally prevents a pricing hallucination from reaching a prospect — the prompt already
    asks the model to check, but asking is not guaranteeing.
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
    # French formatting: the dot separates thousands, the comma separates decimals.
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None
