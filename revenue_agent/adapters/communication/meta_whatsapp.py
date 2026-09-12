"""Adapter WhatsApp — Meta Cloud API.

En mode test développeur (gratuit), Meta n'autorise l'envoi que vers un petit nombre de
numéros préalablement déclarés : un envoi vers un numéro non déclaré est rejeté par l'API,
pas silencieusement ignoré.
"""

from __future__ import annotations

import logging

from revenue_agent.adapters.http import HttpClient
from revenue_agent.domain.errors import MessagingError

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v20.0"


class MetaWhatsAppAdapter:
    def __init__(self, token: str, phone_number_id: str) -> None:
        self._phone_number_id = phone_number_id
        self._http = HttpClient(
            base_url=f"https://graph.facebook.com/{GRAPH_API_VERSION}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            error_factory=lambda message: MessagingError("whatsapp", message),
        )

    def send(self, *, to_phone_number: str, message: str) -> str:
        payload = self._http.request(
            "POST",
            f"/{self._phone_number_id}/messages",
            json={
                "messaging_product": "whatsapp",
                "to": to_phone_number,
                "type": "text",
                "text": {"body": message},
            },
        )
        messages = payload.get("messages", [])
        message_id = messages[0].get("id", "") if messages else ""
        logger.info("WhatsApp envoyé à %s (id=%s)", to_phone_number, message_id)
        return message_id
