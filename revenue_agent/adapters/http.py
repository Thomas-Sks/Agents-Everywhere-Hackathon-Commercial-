"""HTTP client shared by all outbound adapters.

Centralises what we don't want to rewrite (or forget) in every integration: an explicit
timeout, retry with backoff on transient errors only, and translation of failures into typed
application errors. A business-level 4xx must not be retried; a 429 or a 502 must be.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from revenue_agent.domain.errors import AdapterError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class HttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        error_factory: Callable[[str], AdapterError],
        timeout: float = 20.0,
        max_retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = headers
        self._error_factory = error_factory
        self._timeout = timeout
        self._max_retries = max_retries

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict:
        url = f"{self._base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = httpx.request(
                    method,
                    url,
                    headers=self._headers,
                    json=json,
                    params=params,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    self._sleep(attempt)
                    continue
                raise self._error_factory(f"{method} {path} : échec réseau ({exc})") from exc

            if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                logger.warning(
                    "Réponse %s sur %s %s — nouvelle tentative (%s/%s)",
                    response.status_code,
                    method,
                    path,
                    attempt + 1,
                    self._max_retries,
                )
                self._sleep(attempt, response)
                continue

            if response.is_error:
                raise self._error_factory(
                    f"{method} {path} → {response.status_code} : {response.text[:300]}"
                )

            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise self._error_factory(f"{method} {path} : réponse non-JSON") from exc

        raise self._error_factory(f"{method} {path} : échec après retries ({last_error})")

    @staticmethod
    def _sleep(attempt: int, response: httpx.Response | None = None) -> None:
        if response is not None:
            retry_after = response.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                time.sleep(min(int(retry_after), 10))
                return
        time.sleep(min(2**attempt * 0.5, 5.0))
