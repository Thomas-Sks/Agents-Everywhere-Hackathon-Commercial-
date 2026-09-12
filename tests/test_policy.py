"""Limites d'autonomie appliquées par le code.

Ces tests sont les plus importants du dépôt : ils vérifient ce qui empêche un message
d'atteindre un vrai prospect. Contrairement aux consignes du prompt, ces garanties ne dépendent
pas de ce que le modèle décide de faire.
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


def action(content: str = "Bonjour, seriez-vous disponible mardi ?", **overrides) -> OutboundAction:
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
    """Passe le message par le socle lexical, comme le fait ActionRegistry en production."""
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


def test_le_mode_par_defaut_est_supervise():
    """Un agent qui écrit à de vrais prospects doit être verrouillé à l'installation."""
    assert PolicySettings().mode is AgentMode.SUPERVISED


def test_mode_supervise_exige_une_validation_pour_tout():
    decision = decide(action(), PolicySettings(mode=AgentMode.SUPERVISED))

    assert decision.verdict is Verdict.REQUIRE_APPROVAL
    assert decision.rule == "mode_supervise"


def test_mode_dry_run_bloque_tout():
    decision = decide(action(), PolicySettings(mode=AgentMode.DRY_RUN))

    assert decision.verdict is Verdict.BLOCK


def test_mode_autonome_laisse_passer_une_action_anodine():
    assert decide(action(), autonomous()).verdict is Verdict.ALLOW


# -- Engagements commerciaux --------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "Je peux vous proposer une remise exceptionnelle.",
        "Nous pouvons envisager un rabais sur ce volume.",
        "Une réduction de 15 % est envisageable.",
        "Exceptionnellement, -20% sur la première année.",
        "Je vous fais un geste commercial.",
        "Le premier mois serait gratuit.",
        "La formation vous est offerte.",
    ],
)
def test_tout_engagement_commercial_exige_une_validation(content):
    """Le cœur de la critique : rien ne doit empêcher structurellement une remise de partir,
    sauf du code."""
    decision = decide(action(content), autonomous())

    assert decision.verdict is Verdict.REQUIRE_APPROVAL
    assert decision.rule.startswith("relecture_")


def test_un_message_sans_engagement_passe():
    decision = decide(action("Souhaitez-vous que je vous rappelle jeudi ?"), autonomous())

    assert decision.verdict is Verdict.ALLOW


# -- Prix hallucinés ----------------------------------------------------------------


def test_un_prix_absent_du_catalogue_est_bloque():
    decision = decide(action("Notre solution est à 3 200 € par an."), autonomous())

    assert decision.verdict is Verdict.BLOCK
    assert decision.rule == "prix_hors_catalogue"


def test_un_prix_du_catalogue_est_accepte():
    decision = decide(action("La GTX Pro est à 4800 €."), autonomous())

    assert decision.verdict is Verdict.ALLOW


def test_les_nombres_sans_marqueur_monetaire_ne_sont_pas_des_prix():
    """« 10 utilisateurs » ou une année ne doivent pas déclencher un blocage tarifaire."""
    decision = decide(
        action("La licence couvre 10 utilisateurs, disponible depuis 2024."), autonomous()
    )

    assert decision.verdict is Verdict.ALLOW


def test_un_blocage_prix_prime_sur_une_demande_de_validation():
    """Deux règles s'appliquent : le blocage doit gagner, jamais l'inverse."""
    decision = decide(
        action("Remise exceptionnelle : 3 200 € au lieu du tarif public."), autonomous()
    )

    assert decision.verdict is Verdict.BLOCK


# -- Destinataires et cadence -------------------------------------------------------


def test_destinataire_hors_liste_bloque():
    settings = autonomous(allowed_recipients=("demo@exemple.test",))

    decision = decide(action(recipient="vrai.prospect@entreprise.com"), settings)

    assert decision.verdict is Verdict.BLOCK
    assert decision.rule == "destinataire_hors_liste"


def test_destinataire_autorise_passe():
    settings = autonomous(allowed_recipients=("julie@acme.example",))

    assert decide(action(), settings).verdict is Verdict.ALLOW


def test_la_cadence_maximale_protege_le_prospect():
    decision = decide(action(), autonomous(max_outbound_per_day=3), outbound_last_24h=3)

    assert decision.verdict is Verdict.BLOCK
    assert decision.rule == "cadence_maximale"


# -- Montant ------------------------------------------------------------------------


def test_un_gros_montant_exige_une_validation():
    decision = decide(
        action(opportunity_amount=120_000.0), autonomous(max_autonomous_amount=50_000)
    )

    assert decision.verdict is Verdict.REQUIRE_APPROVAL
    assert decision.rule == "montant_eleve"


# -- Rejeu après validation humaine -------------------------------------------------


def test_une_validation_humaine_leve_les_regles_de_validation():
    decision = decide(
        action("Je vous propose une remise de 15 %."), autonomous(), human_approved=True
    )

    assert decision.verdict is Verdict.ALLOW


def test_une_validation_humaine_ne_leve_pas_le_mode_dry_run():
    """Un opérateur arbitre le commercial, pas les garde-fous d'exploitation."""
    decision = decide(action(), PolicySettings(mode=AgentMode.DRY_RUN), human_approved=True)

    assert decision.verdict is Verdict.BLOCK


def test_une_validation_humaine_ne_leve_pas_la_liste_de_destinataires():
    settings = autonomous(allowed_recipients=("demo@exemple.test",))

    decision = decide(action(recipient="vrai@client.com"), settings, human_approved=True)

    assert decision.verdict is Verdict.BLOCK
