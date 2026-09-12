"""Application logging configuration.

A console-readable format for demos, switching to JSON for deployments where logs are
aggregated (Trigger.dev, a container). Logs are the product's only observability channel: when
the agent runs autonomously, nobody is watching the screen at the moment it decides.
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
