"""Ports d'intelligence — enrichissement du contexte et prise de décision.

`DecisionAgentPort` est ce qui rend le produit testable : le domaine peut être exercé avec un
agent bouchonné, sans appeler ni OpenRouter ni aucun modèle.
"""

from __future__ import annotations

from typing import Protocol

from revenue_agent.domain.models import Opportunity, ProspectInsight
from revenue_agent.domain.review import MessageUnderReview, ReviewFinding


class EnrichmentPort(Protocol):
    """Recherche web qualifiée — répond au « qu'est-ce que je ne sais pas ? » du concept."""

    def research_company(
        self, company_name: str, *, since_days: int = 90, limit: int = 5
    ) -> list[ProspectInsight]:
        """Actualité récente : levée de fonds, recrutements, changements de direction."""
        ...


class MessageReviewPort(Protocol):
    """Relecture d'un message avant envoi, comme le ferait un directeur commercial.

    Contrat impératif : **une relecture impossible n'est pas une relecture favorable**. Toute
    implémentation qui ne peut pas conclure doit escalader, jamais laisser passer.
    """

    def review(self, message: MessageUnderReview) -> ReviewFinding: ...


class DecisionAgentPort(Protocol):
    def decide(self, *, opportunity: Opportunity, event: str, strategic: bool = False) -> str:
        """Fait raisonner l'agent sur un événement et exécute les actions qu'il choisit.

        `strategic=True` route vers un modèle plus capable : le coût du raisonnement suit
        l'enjeu commercial, pas l'inverse.

        Retourne la synthèse textuelle de ce que l'agent a décidé.
        """
        ...
