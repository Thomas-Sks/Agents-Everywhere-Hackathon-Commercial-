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
    finding = LexicalMessageReviewer().review(message("I can offer you a 15% discount."))

    assert finding.requires_human is True
    assert finding.category is ReviewCategory.PRICE_COMMITMENT


def test_the_baseline_quotes_the_offending_sentence():
    """A reviewer points at the line, they do not merely say "this message bothers me"."""
    content = "Hello Julie. I can give you a discount. Talk soon."

    finding = LexicalMessageReviewer().review(message(content))

    assert "discount" in finding.quote
    assert finding.quote != content, "we quote the sentence, not the whole message"


def test_the_baseline_lets_an_innocuous_message_through():
    finding = LexicalMessageReviewer().review(message("Would you be available on Tuesday at 2pm?"))

    assert finding.requires_human is False


# -- Layering -----------------------------------------------------------------------


def test_the_semantic_layer_is_not_called_when_the_baseline_already_ruled():
    """Saves a model call: a certain rule does not need confirmation."""
    semantic = StubReviewer(ReviewFinding.clear())
    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=semantic)

    finding = reviewer.review(message("A discount is possible."))

    assert finding.requires_human is True
    assert semantic.calls == []


def test_the_semantic_layer_catches_what_the_baseline_cannot_see():
    """"I'll match their price" is not in the dictionary, and yet it commits the company."""
    semantic = StubReviewer(
        ReviewFinding.escalate(
            ReviewCategory.PRICE_COMMITMENT,
            "The message implies the price is negotiable.",
            quote="I will match their price",
        )
    )
    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=semantic)

    finding = reviewer.review(message("On budget, I will match their price."))

    assert finding.requires_human is True
    assert finding.category is ReviewCategory.PRICE_COMMITMENT
    assert semantic.calls, "the semantic layer must be consulted when the baseline lets it pass"


def test_without_a_semantic_reviewer_the_baseline_has_the_final_say():
    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=None)

    assert reviewer.review(message("Hello, are you free on Thursday?")).requires_human is False


def test_a_message_cleared_by_both_layers_goes_through():
    reviewer = LayeredMessageReviewer(
        lexical=LexicalMessageReviewer(), semantic=StubReviewer(ReviewFinding.clear())
    )

    assert reviewer.review(message("I am sending you the case study.")).requires_human is False


# -- Fail closed --------------------------------------------------------------------


def test_a_reviewer_that_is_down_holds_the_message():
    """The most important point: an absent reviewer does not mean an approved message."""

    class BrokenReviewer:
        def review(self, msg):
            return ReviewFinding.escalate(
                ReviewCategory.NONE, "The review could not run.", source="llm"
            )

    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=BrokenReviewer())

    assert reviewer.review(message("Bonjour Julie.")).requires_human is True


def test_the_context_is_passed_to_the_reviewer():
    """The same sentence is not judged the same way on first contact and late in a negotiation."""
    semantic = StubReviewer(ReviewFinding.clear())
    reviewer = LayeredMessageReviewer(lexical=LexicalMessageReviewer(), semantic=semantic)

    reviewer.review(message("Hello."))

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
    assert _parse_category("price_commitment") is ReviewCategory.PRICE_COMMITMENT
