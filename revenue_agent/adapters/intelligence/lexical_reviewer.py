"""Relecture lexicale — le socle déterministe.

Volontairement simple et sans appel réseau : c'est la garantie qui tient quand le classifieur
sémantique est indisponible, lent, ou se trompe. Elle n'attrape que ce qui se nomme
explicitement, mais elle l'attrape toujours, de façon reproductible et auditable.

Ne jamais retirer cette couche au motif que le classifieur fait mieux : une garantie
probabiliste ne remplace pas une garantie déterministe, elle s'y ajoute.
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
    """Implémente `MessageReviewPort` sans modèle ni réseau."""

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
    """Extrait la phrase contenant le passage repéré — un motif isolé ne se relit pas."""
    start = max(content.rfind(".", 0, position), content.rfind("\n", 0, position)) + 1
    end_candidates = [
        index
        for index in (content.find(".", position), content.find("\n", position))
        if index != -1
    ]
    end = min(end_candidates) + 1 if end_candidates else len(content)
    return content[start:end].strip()
