"""Superposition des deux relectures.

Le socle lexical s'exécute toujours et en premier : il est gratuit, instantané et déterministe.
S'il escalade, inutile d'appeler un modèle pour confirmer ce qu'une règle certaine a déjà
tranché — on économise l'appel et on garde un motif auditable.

La relecture sémantique ne s'exécute donc que sur les messages que le socle a laissés passer.
C'est là qu'elle apporte quelque chose : les engagements formulés sans jamais se nommer.
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
