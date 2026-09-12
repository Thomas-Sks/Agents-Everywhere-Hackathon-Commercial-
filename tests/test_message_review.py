"""Review of outbound messages — lexical baseline, semantic judgement, layering.

The model's judgement is not tested here (that would be testing OpenAI): what is tested is that
our code behaves correctly **around** it — that an absent, slow or incoherent model never
results in a message going out unreviewed.
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


# -- Lexical baseline ---------------------------------------------------------------


def test_the_baseline_catches_what_names_itself():
    finding = LexicalMessageReviewer().review(message("Je vous propose une remise de 15 %."))

    assert finding.requires_human is True
    assert finding.category is ReviewCategory.PRICE_COMMITMENT


def test_the_baseline_quotes_the_offending_sentence():
    """A reviewer points at the line, they do not merely say "this message bothers me"."""
    content = "Bonjour Julie. Je peux vous faire une remise. À bientôt."

    finding = LexicalMessageReviewer().review(message(content))

    assert "remise" in finding.quote
    assert finding.quote != content, "we quote the sentence, not the whole message"


def test_the_baseline_lets_an_innocuous_message_through():
    finding = LexicalMessageReviewer().review(message("Seriez-vous disponible mardi à 14 h ?"))

    assert finding.requires_human is False


# -- Layering -----------------------------------------------------------------------


def test_the_semantic_layer_is_not_called_when_the_baseline_already_ruled():
    """Saves a model call: a certain rule does not need confirmation."""
    semantic = StubReviewer(ReviewFinding.clear())
    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=semantic)

    finding = reviewer.review(message("Une remise est envisageable."))

    assert finding.requires_human is True
    assert semantic.calls == []


def test_the_semantic_layer_catches_what_the_baseline_cannot_see():
    """"I'll match their price" is not in the dictionary, and yet it commits the company."""
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
    assert semantic.calls, "the semantic layer must be consulted when the baseline lets it pass"


def test_without_a_semantic_reviewer_the_baseline_has_the_final_say():
    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=None)

    assert reviewer.review(message("Bonjour, disponible jeudi ?")).requires_human is False


def test_a_message_cleared_by_both_layers_goes_through():
    reviewer = LayeredMessageReviewer(
        lexical=LexicalMessageReviewer(), semantic=StubReviewer(ReviewFinding.clear())
    )

    assert reviewer.review(message("Je vous envoie l'étude de cas.")).requires_human is False


# -- Fail closed --------------------------------------------------------------------


def test_a_reviewer_that_is_down_holds_the_message():
    """The most important point: an absent reviewer does not mean an approved message."""

    class BrokenReviewer:
        def review(self, msg):
            return ReviewFinding.escalate(
                ReviewCategory.NONE, "La relecture n'a pas pu s'exécuter.", source="llm"
            )

    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=BrokenReviewer())

    assert reviewer.review(message("Bonjour Julie.")).requires_human is True


def test_the_context_is_passed_to_the_reviewer():
    """The same sentence is not judged the same way on first contact and late in a negotiation."""
    semantic = StubReviewer(ReviewFinding.clear())
    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=semantic)

    reviewer.review(message("Bonjour."))

    transmitted = semantic.calls[0]
    assert transmitted.stage == "qualification"
    assert transmitted.amount == 40_000.0


# -- Reading the model's response ---------------------------------------------------
#
# A model does not always return bare JSON: it wraps it, comments on it, or goes off the rails.
# These cases are the main source of silent failure for a classifier in production.


def test_bare_json():
    from revenue_agent.adapters.intelligence.llm_reviewer import _extract_json

    assert _extract_json('{"requires_human": false}') == {"requires_human": False}


def test_json_inside_a_code_block():
    from revenue_agent.adapters.intelligence.llm_reviewer import _extract_json

    payload = _extract_json('Voici mon verdict :\n```json\n{"requires_human": true}\n```')

    assert payload == {"requires_human": True}


def test_json_buried_in_prose():
    from revenue_agent.adapters.intelligence.llm_reviewer import _extract_json

    payload = _extract_json('Après relecture, {"requires_human": true} me semble juste.')

    assert payload == {"requires_human": True}


def test_an_unreadable_response_does_not_guess_a_verdict():
    from revenue_agent.adapters.intelligence.llm_reviewer import _extract_json

    assert _extract_json("Je ne peux pas répondre.") is None


def test_an_unknown_category_does_not_crash():
    from revenue_agent.adapters.intelligence.llm_reviewer import _parse_category

    assert _parse_category("catégorie_inventée_par_le_modèle") is ReviewCategory.NONE
    assert _parse_category(None) is ReviewCategory.NONE
    assert _parse_category("engagement_prix") is ReviewCategory.PRICE_COMMITMENT
