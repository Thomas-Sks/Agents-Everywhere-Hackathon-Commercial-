"""Erreurs du domaine et des adapters.

Principe : un adapter **lève** quand il échoue, il ne renvoie pas `None` silencieusement. La
dégradation gracieuse est une décision applicative, prise dans les use cases, pas une
convention implicite disséminée dans chaque intégration.
"""

from __future__ import annotations


class RevenueAgentError(Exception):
    """Racine de toutes les erreurs applicatives."""


class AdapterError(RevenueAgentError):
    """Échec d'un système externe (CRM, email, voix, enrichissement)."""

    def __init__(self, adapter: str, message: str) -> None:
        super().__init__(f"[{adapter}] {message}")
        self.adapter = adapter
        self.message = message


class CrmError(AdapterError):
    pass


class MessagingError(AdapterError):
    pass


class VoiceError(AdapterError):
    pass


class EnrichmentError(AdapterError):
    pass


class OpportunityNotFound(RevenueAgentError):
    def __init__(self, opportunity_id: str) -> None:
        super().__init__(f"Opportunité introuvable : {opportunity_id}")
        self.opportunity_id = opportunity_id


class ChannelUnavailable(RevenueAgentError):
    """L'agent a choisi un canal dont la coordonnée n'existe pas pour ce prospect."""

    def __init__(self, opportunity_id: str, channel: str) -> None:
        super().__init__(
            f"Canal '{channel}' indisponible pour l'opportunité {opportunity_id} "
            "(coordonnée absente du CRM)"
        )
        self.opportunity_id = opportunity_id
        self.channel = channel


class AuthorizationRequired(RevenueAgentError):
    """L'action dépasse les limites d'autonomie de l'agent."""

    def __init__(self, action: str, reason: str) -> None:
        super().__init__(f"Action '{action}' refusée : {reason}")
        self.action = action
        self.reason = reason
