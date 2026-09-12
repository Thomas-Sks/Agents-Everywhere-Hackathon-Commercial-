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
    """Atteindre un humain au sujet d'une opportunité.

    Volontairement un port distinct des canaux prospect : ici le destinataire est un collègue,
    pas un client, et l'exigence n'est pas la même. Un message au prospect qui échoue peut être
    réessayé plus tard ; **un handoff perdu est un deal abandonné sans que personne ne le
    sache**. Toute implémentation doit donc garantir la remise, ou échouer bruyamment.

    Deux moments distincts, tous deux destinés à un humain :
    """

    def escalate(
        self, *, opportunity: Opportunity, reason: str, urgency: str, context_brief: str
    ) -> None:
        """Passage de relais complet : l'agent se retire, un commercial reprend le dossier."""
        ...

    def notify_pending_approval(
        self, *, opportunity: Opportunity, approval_id: str, channel: str, reason: str, preview: str
    ) -> None:
        """Une action attend un arbitrage.

        Sans cette notification, la file de validation ne serait consultée que par quelqu'un
        qui pense à la consulter — et une action retenue resterait indéfiniment en attente.
        """
        ...
