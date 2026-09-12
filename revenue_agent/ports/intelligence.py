"""Intelligence ports — context enrichment and decision making.

`DecisionAgentPort` is what makes the product testable: the domain can be exercised with a
stubbed agent, without calling OpenRouter or any model at all.
"""

from __future__ import annotations

from typing import Protocol

from revenue_agent.domain.models import Opportunity, ProspectInsight
from revenue_agent.domain.review import MessageUnderReview, ReviewFinding


class EnrichmentPort(Protocol):
    """Qualified web search — answers the concept's "what don't I know?" question."""

    def research_company(
        self, company_name: str, *, since_days: int = 90, limit: int = 5
    ) -> list[ProspectInsight]:
        """Recent news: funding rounds, hiring, changes in leadership."""
        ...


class MessageReviewPort(Protocol):
    """Reviewing a message before it goes out, the way a sales director would.

    Mandatory contract: **a review that could not happen is not a favourable review**. Any
    implementation unable to reach a conclusion must escalate, never let the message through.
    """

    def review(self, message: MessageUnderReview) -> ReviewFinding: ...


class DecisionAgentPort(Protocol):
    def decide(self, *, opportunity: Opportunity, event: str, strategic: bool = False) -> str:
        """Has the agent reason about an event and carries out the actions it chooses.

        `strategic=True` routes to a more capable model: the cost of reasoning follows the
        sales stakes, not the other way round.

        Returns a textual summary of what the agent decided.
        """
        ...
