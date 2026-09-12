"""Ports de communication — les moyens d'agir sur le monde extérieur.

Le moteur de décision choisit un canal ; il ignore que l'email part par Resend, que WhatsApp
passe par Meta ou que l'appel est passé par Retell. Remplacer un fournisseur, c'est écrire un
adapter, pas toucher au domaine.
"""

from __future__ import annotations

from typing import Protocol

from revenue_agent.domain.models import Opportunity


class EmailPort(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> str:
        """Retourne un identifiant de message. Lève `MessagingError` en cas d'échec."""
        ...


class WhatsAppPort(Protocol):
    def send(self, *, to_phone_number: str, message: str) -> str: ...


class VoicePort(Protocol):
    def place_call(
        self, *, to_phone_number: str, opportunity: Opportunity, objective: str
    ) -> str:
        """Déclenche un appel sortant et retourne son identifiant.

        L'implémentation est responsable de transmettre le contexte de l'opportunité à la
        plateforme vocale, dans le format qu'elle impose.
        """
        ...


class HandoffPort(Protocol):
    """Passage de relais à un commercial humain, avec l'intégralité du contexte.

    Volontairement un port distinct : le handoff n'est pas « un message de plus », c'est un
    moment produit à part entière (chapitre 10 du concept) dont la destination changera
    (console aujourd'hui, Slack ou un espace de collaboration demain).
    """

    def escalate(
        self, *, opportunity: Opportunity, reason: str, urgency: str, context_brief: str
    ) -> None: ...
