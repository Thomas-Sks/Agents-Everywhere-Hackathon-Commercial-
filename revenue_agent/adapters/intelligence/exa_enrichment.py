"""Enrichment adapter — Exa.

Answers the decision engine's "what don't I know?": the CRM tells us what happened between us
and the prospect, Exa tells us what happened at the prospect. A funding round or a hiring spree
changes how two months of silence should be read.

The time window is essential: without `startPublishedDate`, we surface articles from 2019 that
are not buying signals.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from revenue_agent.adapters.http import HttpClient
from revenue_agent.domain.errors import EnrichmentError
from revenue_agent.domain.models import ProspectInsight

logger = logging.getLogger(__name__)


class ExaEnrichmentAdapter:
    def __init__(self, api_key: str) -> None:
        self._http = HttpClient(
            base_url="https://api.exa.ai",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            error_factory=lambda message: EnrichmentError("exa", message),
        )

    def research_company(
        self, company_name: str, *, since_days: int = 90, limit: int = 5
    ) -> list[ProspectInsight]:
        since = datetime.now(UTC) - timedelta(days=since_days)
        payload = self._http.request(
            "POST",
            "/search",
            json={
                "query": (
                    f"News about {company_name}: funding rounds, hiring, "
                    "leadership appointments, new offices, supplier or strategy "
                    "changes."
                ),
                "category": "news",
                "startPublishedDate": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "numResults": limit,
                "contents": {
                    "summary": {
                        "query": (
                            "What actionable sales signal does this article contain "
                            "about this company?"
                        )
                    }
                },
            },
        )

        insights = [
            ProspectInsight(
                title=result.get("title") or "Sans titre",
                url=result.get("url", ""),
                summary=(result.get("summary") or "").strip(),
                published_at=_parse_published_date(result.get("publishedDate")),
            )
            for result in payload.get("results", [])
        ]
        logger.info(
            "Exa: %s result(s) for %s (cost $%s)",
            len(insights),
            company_name,
            payload.get("costDollars", "?"),
        )
        return insights


class NullEnrichmentAdapter:
    """Used when Exa is not configured: the agent then knows it lacks the capability, rather
    than receiving empty results it might read as "nothing to report"."""

    def research_company(
        self, company_name: str, *, since_days: int = 90, limit: int = 5
    ) -> list[ProspectInsight]:
        raise EnrichmentError("exa", "web enrichment not configured (EXA_API_KEY missing)")


def _parse_published_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
