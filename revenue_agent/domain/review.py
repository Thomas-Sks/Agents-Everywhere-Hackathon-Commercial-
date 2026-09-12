"""Relecture d'un message sortant — le regard d'un directeur commercial avant envoi.

La détection lexicale attrape ce qui se nomme (« remise », « -20 % »). Elle ne voit pas ce qui
se formule : « je m'aligne sur leur tarif », « on trouvera un terrain d'entente sur le budget »,
« je vous garantis un retour sur investissement en six mois ». Ces phrases engagent l'entreprise
autant qu'une remise annoncée, et aucun dictionnaire ne les couvrira toutes.

D'où cette relecture sémantique. Elle ne remplace pas les règles déterministes : elle s'y
ajoute. Un jugement de modèle peut se tromper ou être indisponible ; une règle lexicale, non.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReviewCategory(StrEnum):
    """Motifs d'escalade, tels qu'un directeur commercial les formulerait."""

    NONE = "aucun"
    PRICE_COMMITMENT = "engagement_prix"
    CONTRACTUAL_COMMITMENT = "engagement_contractuel"
    UNBACKED_PROMISE = "promesse_intenable"
    PREMATURE_CONCESSION = "concession_prematuree"
    EXCESSIVE_PRESSURE = "pression_excessive"
    CLIENT_REFERENCE = "reference_client"
    STAGE_MISMATCH = "inadapte_au_stade"
    LEGAL_EXPOSURE = "exposition_juridique"


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """Verdict de la relecture.

    `quote` porte la phrase exacte qui pose problème : un directeur commercial ne dit pas
    « ce message m'ennuie », il pointe la ligne. C'est aussi ce qui rend la file de validation
    exploitable en quelques secondes.
    """

    requires_human: bool
    category: ReviewCategory = ReviewCategory.NONE
    rationale: str = ""
    quote: str = ""
    source: str = ""

    @classmethod
    def clear(cls, source: str = "") -> ReviewFinding:
        return cls(requires_human=False, source=source)

    @classmethod
    def escalate(
        cls, category: ReviewCategory, rationale: str, quote: str = "", source: str = ""
    ) -> ReviewFinding:
        return cls(
            requires_human=True,
            category=category,
            rationale=rationale,
            quote=quote,
            source=source,
        )

    def describe(self) -> str:
        parts = [self.rationale or self.category.value]
        if self.quote:
            parts.append(f"Passage en cause : « {self.quote.strip()} »")
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class MessageUnderReview:
    """Ce qu'on soumet au relecteur : le message, et le contexte qui permet d'en juger.

    Sans le contexte, la même phrase est anodine ou grave : « on peut s'arranger sur le prix »
    en fin de négociation avec un décideur engagé n'est pas la même chose qu'au premier contact.
    """

    channel: str
    company: str
    stage: str
    content: str
    amount: float | None = None
    open_objections: tuple[str, ...] = ()
    interactions_count: int = 0
