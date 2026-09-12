"""Configuration du logging applicatif.

Format lisible en console pour la démo, et bascule JSON pour un déploiement où les logs sont
agrégés (Trigger.dev, conteneur). Les logs sont le seul canal d'observabilité du produit :
quand l'agent tourne en autonomie, personne ne regarde l'écran au moment où il décide.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

_NOISY_LOGGERS = ("httpx", "httpcore", "openai", "urllib3")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)

    if os.environ.get("LOG_FORMAT", "").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-7s %(name)s — %(message)s", "%H:%M:%S")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)
