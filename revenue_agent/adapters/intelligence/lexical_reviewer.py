"""Lexical review — the deterministic baseline.

Deliberately simple and free of network calls: this is the guarantee that holds when the
semantic classifier is unavailable, slow, or wrong. It only catches what names itself
explicitly, but it always catches it, reproducibly and auditably.

Never remove this layer on the grounds that the classifier does better: a probabilistic
guarantee does not replace a deterministic one, it is added on top of it.
"""

from __future__ import annotations

import re

from revenue_agent.domain.review import MessageUnderReview, ReviewCategory, ReviewFinding

SOURCE = "lexical"

_PATTERNS: tuple[tuple[str, ReviewCategory, str], ...] = (
    (r"\bremise\b", ReviewCategory.PRICE_COMMITMENT, "Le message annonce une remise."),
    (r"\brabais\b", ReviewCategory.PRICE_COMMITMENT, "Le message annonce un rabais."),
    (r"\bréduction\b", ReviewCategory.PRICE_COMMITMENT, "Le message annonce une réduction."),
    (r"\bdiscount\b", ReviewCategory.PRICE_COMMITMENT, "Le message annonce une réduction."),
    (
        r"\bgeste commercial\b",
        ReviewCategory.PRICE_COMMITMENT,
        "Le message promet un geste commercial.",
    ),
    (
        r"\bprix préférentiel\b",
        ReviewCategory.PRICE_COMMITMENT,
        "Le message annonce un prix préférentiel.",
    ),
    (
        r"\boffre spéciale\b",
        ReviewCategory.PRICE_COMMITMENT,
        "Le message annonce une offre spéciale.",
    ),
    (r"\bgratuit(e|s)?\b", ReviewCategory.PRICE_COMMITMENT, "Le message promet une gratuité."),
    (
        r"\bofferte?s?\b",
        ReviewCategory.PRICE_COMMITMENT,
        "Le message promet une prestation offerte.",
    ),
    (r"\d+\s*%", ReviewCategory.PRICE_COMMITMENT, "Le message contient un pourcentage."),
)


class LexicalMessageReviewer:
    """Implements `MessageReviewPort` with no model and no network."""

    def review(self, message: MessageUnderReview) -> ReviewFinding:
        for pattern, category, rationale in _PATTERNS:
            match = re.search(pattern, message.content, re.IGNORECASE)
            if match:
                return ReviewFinding.escalate(
                    category=category,
                    rationale=rationale,
                    quote=_surrounding_sentence(message.content, match.start()),
                    source=SOURCE,
                )
        return ReviewFinding.clear(source=SOURCE)


def _surrounding_sentence(content: str, position: int) -> str:
    """Extracts the sentence containing the match — an isolated pattern cannot be reviewed."""
    start = max(content.rfind(".", 0, position), content.rfind("\n", 0, position)) + 1
    end_candidates = [
        index
        for index in (content.find(".", position), content.find("\n", position))
        if index != -1
    ]
    end = min(end_candidates) + 1 if end_candidates else len(content)
    return content[start:end].strip()
