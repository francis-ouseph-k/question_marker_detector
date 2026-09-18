"""Constrained resolution (TD §12, FR-05/06/07)."""

import pytest

from qmd.config import GrammarConfig, ResolutionConfig
from qmd.grammar import MarkerGrammar
from qmd.models import ParseFlag, ResolutionMethod
from qmd.question_list import build_question_list
from qmd.resolver import Outcome, QuestionResolver


@pytest.fixture
def grammar():
    return MarkerGrammar(GrammarConfig())


def resolve(ql, grammar, text, edge=False):
    return QuestionResolver(ql, ResolutionConfig()).resolve(grammar.parse(text, allow_noise_strip=edge))


def test_exact_multilevel(multilevel, grammar):
    r = resolve(multilevel, grammar, "Ans 10 b1)")
    assert r.outcome == Outcome.RESOLVED
    assert r.method == ResolutionMethod.EXACT_UNIQUE
    assert r.question.canonical == "10(b)(1)"


def test_parent_target(multilevel, grammar):
    r = resolve(multilevel, grammar, "10(b)")
    assert r.question.canonical == "10(b)"


def test_never_invents_ids(multilevel, grammar):
    r = resolve(multilevel, grammar, "77")
    assert r.outcome == Outcome.NO_MATCH
    assert r.question is None
    assert all(c.question_id in multilevel.questions for c in r.candidates)


def test_closing_bracket_misread_as_one(eight_questions, grammar):
    # "5)" is often read as "51"; 51 is not a question, 5 is the cheap explanation.
    r = resolve(eight_questions, grammar, "51")
    assert r.outcome == Outcome.RESOLVED
    assert r.method == ResolutionMethod.CONSTRAINED_UNIQUE
    assert r.question.canonical == "5"


def test_letter_digit_confusion_in_letter_position(grammar):
    ql = build_question_list(["9", "10(b)(1)", "12(c)(2)"])
    r = resolve(ql, grammar, "10 6 1")  # 'b' read as '6'
    assert r.question.canonical == "10(b)(1)"
    assert r.method == ResolutionMethod.CONSTRAINED_UNIQUE


def test_letter_digit_confusion_is_ambiguous_when_parent_is_target(multilevel, grammar):
    # With "10(b)" also a target, "1061" could be "10(b)" + a stray ')' -> review, not guess.
    r = resolve(multilevel, grammar, "10 6 1")
    assert r.outcome == Outcome.AMBIGUOUS


def test_noise_stripped_exact(eight_questions, grammar):
    r = resolve(eight_questions, grammar, "63.", edge=True)
    assert r.question.canonical == "3"
    assert ParseFlag.NOISE_STRIPPED in r.flags
    assert r.confidence < 1.0


def test_literal_reading_beats_noise_strip():
    ql = build_question_list([str(i) for i in range(1, 13)])
    g = MarkerGrammar(GrammarConfig())
    r = QuestionResolver(ql, ResolutionConfig()).resolve(g.parse("12", allow_noise_strip=True))
    assert r.question.canonical == "12" and r.method == ResolutionMethod.EXACT_UNIQUE


def test_ambiguous_when_margin_small(eight_questions, grammar):
    r = resolve(eight_questions, grammar, "821", edge=True)   # clipped "Ans2" misread
    assert r.outcome in (Outcome.AMBIGUOUS, Outcome.NO_MATCH)
    assert r.question is None


def test_confusable_flag():
    ql = build_question_list(["1(1)", "11", "2"])
    g = MarkerGrammar(GrammarConfig())
    r = QuestionResolver(ql, ResolutionConfig()).resolve(g.parse("11"))
    assert r.question.canonical == "11"
    assert r.is_confusable


def test_weighted_distance_uses_confusions(eight_questions):
    res = QuestionResolver(eight_questions, ResolutionConfig())
    assert res.weighted_distance("5", "5") == 0
    assert res.weighted_distance("s", "5") == pytest.approx(0.3)
    assert res.weighted_distance("51", "5") == pytest.approx(0.4)
    assert res.weighted_distance("x", "5") == pytest.approx(1.0)
