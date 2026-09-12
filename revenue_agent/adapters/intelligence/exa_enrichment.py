"""Adapter d'enrichissement — Exa.

Répond au « qu'est-ce que je ne sais pas ? » du moteur de décision : le CRM dit ce qui s'est
passé entre nous et le prospect, Exa dit ce qui s'est passé chez le prospect. Une levée de
fonds ou un recrutement massif change l'interprétation d'un silence de deux mois.

La fenêtre temporelle est essentielle : sans `startPublishedDate`, on remonte des articles de
2019 qui ne sont pas des signaux d'achat.
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
                    f"Actualité de l'entreprise {company_name} : levée de fonds, "
                    "recrutements, nomination de dirigeants, ouverture de bureaux, "
                    "changement de fournisseur ou de stratégie."
                ),
                "category": "news",
                "startPublishedDate": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "numResults": limit,
                "contents": {
                    "summary": {
                        "query": (
                            "Quel signal commercial exploitable cet article contient-il "
                            "au sujet de cette entreprise ?"
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
            "Exa : %s résultat(s) pour %s (coût %s $)",
            len(insights),
            company_name,
            payload.get("costDollars", "?"),
        )
        return insights


class NullEnrichmentAdapter:
    """Utilisé quand Exa n'est pas configuré : l'agent sait alors qu'il n'a pas cette capacité,
    plutôt que de recevoir des résultats vides qu'il pourrait prendre pour « rien à signaler »."""

    def research_company(
        self, company_name: str, *, since_days: int = 90, limit: int = 5
    ) -> list[ProspectInsight]:
        raise EnrichmentError("exa", "enrichissement web non configuré (EXA_API_KEY absente)")


def _parse_published_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
