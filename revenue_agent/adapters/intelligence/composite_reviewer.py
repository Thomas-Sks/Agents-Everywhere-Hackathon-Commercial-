"""Layering the two reviews.

The lexical baseline always runs, and runs first: it is free, instantaneous and deterministic.
If it escalates, there is no point calling a model to confirm what a certain rule has already
settled — we save the call and keep an auditable rationale.

The semantic review therefore only runs on messages the baseline let through. That is where it
adds something: commitments phrased without ever naming themselves.
"""

from __future__ import annotations

import logging

from revenue_agent.domain.review import MessageUnderReview, ReviewFinding
from revenue_agent.ports.intelligence import MessageReviewPort

logger = logging.getLogger(__name__)


class LayeredMessageReviewer:
    def __init__(self, *, lexical: MessageReviewPort, semantic: MessageReviewPort | None) -> None:
        self._lexical = lexical
        self._semantic = semantic

    def review(self, message: MessageUnderReview) -> ReviewFinding:
        lexical_finding = self._lexical.review(message)
        if lexical_finding.requires_human:
            return lexical_finding

        if self._semantic is None:
            return lexical_finding

        semantic_finding = self._semantic.review(message)
        if semantic_finding.requires_human:
            logger.info(
                "Relecture sémantique : escalade %s — %s",
                semantic_finding.category.value,
                semantic_finding.rationale,
            )
        return semantic_finding
