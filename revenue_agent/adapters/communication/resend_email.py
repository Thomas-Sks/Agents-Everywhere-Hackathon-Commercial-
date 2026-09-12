"""Email adapter — Resend."""

from __future__ import annotations

import logging

from revenue_agent.adapters.http import HttpClient
from revenue_agent.domain.errors import MessagingError

logger = logging.getLogger(__name__)


class ResendEmailAdapter:
    def __init__(self, api_key: str, from_address: str) -> None:
        self._from_address = from_address
        self._http = HttpClient(
            base_url="https://api.resend.com",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            error_factory=lambda message: MessagingError("resend", message),
        )

    def send(self, *, to: str, subject: str, body: str) -> str:
        payload = self._http.request(
            "POST",
            "/emails",
            json={"from": self._from_address, "to": [to], "subject": subject, "text": body},
        )
        message_id = payload.get("id", "")
        logger.info("Email envoyé à %s (id=%s)", to, message_id)
        return message_id
