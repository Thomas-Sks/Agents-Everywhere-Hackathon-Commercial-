"""Autonomy limits enforced by code.

These are the most important tests in the repository: they verify what stops a message from
reaching a real prospect. Unlike the prompt's instructions, these guarantees do not depend on
what the model decides to do.
"""

from __future__ import annotations

import pytest

from revenue_agent.adapters.intelligence.lexical_reviewer import LexicalMessageReviewer
from revenue_agent.config import AgentMode, PolicySettings
from revenue_agent.domain.policy import (
    ActionKind,
    OutboundAction,
    Verdict,
    evaluate,
)
from revenue_agent.domain.review import MessageUnderReview

CATALOGUE = (550.0, 4800.0, 22000.0)


def action(
    content: str = "Hello, would you be available on Tuesday?", **overrides
) -> OutboundAction:
    defaults = {
        "kind": ActionKind.EMAIL,
        "opportunity_id": "acme-co",
        "recipient": "julie@acme.example",
        "content": content,
        "opportunity_amount": 10_000.0,
    }
    return OutboundAction(**{**defaults, **overrides})


def autonomous(**overrides) -> PolicySettings:
    return PolicySettings(mode=AgentMode.AUTONOMOUS, **overrides)


def decide(act: OutboundAction, settings: PolicySettings, **kwargs):
    """Run the message through the lexical baseline, exactly as ActionRegistry does live."""
    kwargs.setdefault(
        "review",
        LexicalMessageReviewer().review(
            MessageUnderReview(
                channel=act.kind.value,
                company="Acme Co",
                stage="qualification",
                content=act.content,
                amount=act.opportunity_amount,
            )
        ),
    )
    return evaluate(act, settings=settings, catalogue_prices=CATALOGUE, **kwargs)


# -- Mode ---------------------------------------------------------------------------


def test_the_default_mode_is_supervised():
    """An agent that writes to real prospects must ship locked down."""
    assert PolicySettings().mode is AgentMode.SUPERVISED


def test_supervised_mode_requires_approval_for_everything():
    decision = decide(action(), PolicySettings(mode=AgentMode.SUPERVISED))

    assert decision.verdict is Verdict.REQUIRE_APPROVAL
    assert decision.rule == "supervised_mode"


def test_dry_run_mode_blocks_everything():
    decision = decide(action(), PolicySettings(mode=AgentMode.DRY_RUN))

    assert decision.verdict is Verdict.BLOCK


def test_autonomous_mode_lets_an_innocuous_action_through():
    assert decide(action(), autonomous()).verdict is Verdict.ALLOW


# -- Commercial commitments ---------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "I can offer you an exceptional discount.",
        "We could consider a rebate at that volume.",
        "A 15% reduction is possible.",
        "Exceptionally, -20% on the first year.",
        "I will waive the setup fee for you.",
        "The first month would be free of charge.",
        "Training is complimentary.",
    ],
)
def test_every_commercial_commitment_requires_approval(content):
    """The heart of the critique: nothing but code should structurally stop a discount from
    going out."""
    decision = decide(action(content), autonomous())

    assert decision.verdict is Verdict.REQUIRE_APPROVAL
    assert decision.rule.startswith("review_")


def test_a_message_without_any_commitment_goes_through():
    decision = decide(action("Would you like me to call you back on Thursday?"), autonomous())

    assert decision.verdict is Verdict.ALLOW


# -- Hallucinated prices ------------------------------------------------------------


def test_a_price_absent_from_the_catalogue_is_blocked():
    decision = decide(action("Our solution is 3,200 EUR per year."), autonomous())

    assert decision.verdict is Verdict.BLOCK
    assert decision.rule == "price_not_in_catalogue"


def test_a_price_from_the_catalogue_is_accepted():
    decision = decide(action("The GTX Pro is 4800 EUR."), autonomous())

    assert decision.verdict is Verdict.ALLOW


def test_numbers_without_a_currency_marker_are_not_prices():
    """"10 users" or a year must not trigger a pricing block."""
    decision = decide(
        action("The licence covers 10 users, available since 2024."), autonomous()
    )

    assert decision.verdict is Verdict.ALLOW


def test_a_price_block_outranks_an_approval_request():
    """Two rules apply: the block must win, never the other way round."""
    decision = decide(
        action("Exceptional discount: 3,200 EUR instead of list price."), autonomous()
    )

    assert decision.verdict is Verdict.BLOCK


# -- Recipients and cadence ---------------------------------------------------------


def test_a_recipient_outside_the_allow_list_is_blocked():
    settings = autonomous(allowed_recipients=("demo@exemple.test",))

    decision = decide(action(recipient="vrai.prospect@entreprise.com"), settings)

    assert decision.verdict is Verdict.BLOCK
    assert decision.rule == "recipient_not_allowed"


def test_an_allowed_recipient_goes_through():
    settings = autonomous(allowed_recipients=("julie@acme.example",))

    assert decide(action(), settings).verdict is Verdict.ALLOW


def test_the_maximum_cadence_protects_the_prospect():
    decision = decide(action(), autonomous(max_outbound_per_day=3), outbound_last_24h=3)

    assert decision.verdict is Verdict.BLOCK
    assert decision.rule == "rate_limit"


# -- Amount -------------------------------------------------------------------------


def test_a_large_amount_requires_approval():
    decision = decide(
        action(opportunity_amount=120_000.0), autonomous(max_autonomous_amount=50_000)
    )

    assert decision.verdict is Verdict.REQUIRE_APPROVAL
    assert decision.rule == "high_amount"


# -- Replay after human approval ----------------------------------------------------


def test_a_human_approval_lifts_the_approval_rules():
    decision = decide(
        action("I can offer you a 15% discount."), autonomous(), human_approved=True
    )

    assert decision.verdict is Verdict.ALLOW


def test_a_human_approval_does_not_lift_dry_run_mode():
    """An operator arbitrates the sales call, not the operational guardrails."""
    decision = decide(action(), PolicySettings(mode=AgentMode.DRY_RUN), human_approved=True)

    assert decision.verdict is Verdict.BLOCK


def test_a_human_approval_does_not_lift_the_recipient_allow_list():
    settings = autonomous(allowed_recipients=("demo@exemple.test",))

    decision = decide(action(recipient="vrai@client.com"), settings, human_approved=True)

    assert decision.verdict is Verdict.BLOCK
