"""Relecture des messages sortants — socle lexical, jugement sémantique, superposition.

Le jugement du modèle n'est pas testé ici (ce serait tester OpenAI) : ce qui est testé, c'est
que notre code se comporte correctement **autour** de lui — qu'un modèle absent, lent ou
incohérent ne se traduise jamais par un message qui part sans relecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from revenue_agent.adapters.intelligence.composite_reviewer import LayeredMessageReviewer
from revenue_agent.adapters.intelligence.lexical_reviewer import LexicalMessageReviewer
from revenue_agent.domain.review import MessageUnderReview, ReviewCategory, ReviewFinding


def message(content: str) -> MessageUnderReview:
    return MessageUnderReview(
        channel="email",
        company="Acme Co",
        stage="qualification",
        content=content,
        amount=40_000.0,
    )


@dataclass
class StubReviewer:
    finding: ReviewFinding
    calls: list = field(default_factory=list)

    def review(self, msg: MessageUnderReview) -> ReviewFinding:
        self.calls.append(msg)
        return self.finding


# -- Socle lexical ------------------------------------------------------------------


def test_le_socle_attrape_ce_qui_se_nomme():
    finding = LexicalMessageReviewer().review(message("Je vous propose une remise de 15 %."))

    assert finding.requires_human is True
    assert finding.category is ReviewCategory.PRICE_COMMITMENT


def test_le_socle_cite_la_phrase_en_cause():
    """Un relecteur pointe la ligne, il ne dit pas seulement « ce message m'ennuie »."""
    content = "Bonjour Julie. Je peux vous faire une remise. À bientôt."

    finding = LexicalMessageReviewer().review(message(content))

    assert "remise" in finding.quote
    assert finding.quote != content, "on cite la phrase, pas tout le message"


def test_le_socle_laisse_passer_un_message_anodin():
    finding = LexicalMessageReviewer().review(message("Seriez-vous disponible mardi à 14 h ?"))

    assert finding.requires_human is False


# -- Superposition ------------------------------------------------------------------


def test_le_semantique_nest_pas_appele_si_le_socle_a_deja_tranche():
    """Économie d'un appel de modèle : une règle certaine n'a pas besoin de confirmation."""
    semantic = StubReviewer(ReviewFinding.clear())
    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=semantic)

    finding = reviewer.review(message("Une remise est envisageable."))

    assert finding.requires_human is True
    assert semantic.calls == []


def test_le_semantique_rattrape_ce_que_le_socle_ne_voit_pas():
    """« Je m'aligne sur leur tarif » n'est pas dans le dictionnaire, et engage pourtant."""
    semantic = StubReviewer(
        ReviewFinding.escalate(
            ReviewCategory.PRICE_COMMITMENT,
            "Le message laisse entendre que le prix est négociable.",
            quote="je m'aligne sur leur tarif",
        )
    )
    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=semantic)

    finding = reviewer.review(message("Sur le budget, je m'aligne sur leur tarif."))

    assert finding.requires_human is True
    assert finding.category is ReviewCategory.PRICE_COMMITMENT
    assert semantic.calls, "le sémantique doit être consulté quand le socle laisse passer"


def test_sans_relecteur_semantique_le_socle_fait_foi():
    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=None)

    assert reviewer.review(message("Bonjour, disponible jeudi ?")).requires_human is False


def test_un_message_valide_par_les_deux_couches_passe():
    reviewer = LayeredMessageReviewer(
        lexical=LexicalMessageReviewer(), semantic=StubReviewer(ReviewFinding.clear())
    )

    assert reviewer.review(message("Je vous envoie l'étude de cas.")).requires_human is False


# -- Échec fermé --------------------------------------------------------------------


def test_un_relecteur_en_panne_retient_le_message():
    """Le point le plus important : un relecteur absent ne veut pas dire un message validé."""

    class BrokenReviewer:
        def review(self, msg):
            return ReviewFinding.escalate(
                ReviewCategory.NONE, "La relecture n'a pas pu s'exécuter.", source="llm"
            )

    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=BrokenReviewer())

    assert reviewer.review(message("Bonjour Julie.")).requires_human is True


def test_le_contexte_est_transmis_au_relecteur():
    """La même phrase ne se juge pas pareil au premier contact et en fin de négociation."""
    semantic = StubReviewer(ReviewFinding.clear())
    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=semantic)

    reviewer.review(message("Bonjour."))

    transmitted = semantic.calls[0]
    assert transmitted.stage == "qualification"
    assert transmitted.amount == 40_000.0


# -- Lecture de la réponse du modèle ------------------------------------------------
#
# Un modèle ne répond pas toujours du JSON nu : il l'encadre, le commente, ou déraille.
# Ces cas sont la principale source de panne silencieuse d'un classifieur en production.


def test_json_nu():
    from revenue_agent.adapters.intelligence.llm_reviewer import _extract_json

    assert _extract_json('{"requires_human": false}') == {"requires_human": False}


def test_json_dans_un_bloc_de_code():
    from revenue_agent.adapters.intelligence.llm_reviewer import _extract_json

    payload = _extract_json('Voici mon verdict :\n```json\n{"requires_human": true}\n```')

    assert payload == {"requires_human": True}


def test_json_noye_dans_du_texte():
    from revenue_agent.adapters.intelligence.llm_reviewer import _extract_json

    payload = _extract_json('Après relecture, {"requires_human": true} me semble juste.')

    assert payload == {"requires_human": True}


def test_reponse_illisible_ne_devine_pas_un_verdict():
    from revenue_agent.adapters.intelligence.llm_reviewer import _extract_json

    assert _extract_json("Je ne peux pas répondre.") is None


def test_categorie_inconnue_ne_fait_pas_planter():
    from revenue_agent.adapters.intelligence.llm_reviewer import _parse_category

    assert _parse_category("catégorie_inventée_par_le_modèle") is ReviewCategory.NONE
    assert _parse_category(None) is ReviewCategory.NONE
    assert _parse_category("engagement_prix") is ReviewCategory.PRICE_COMMITMENT
