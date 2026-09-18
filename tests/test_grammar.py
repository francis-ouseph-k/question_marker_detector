"""Marker grammar (TD §10, FR-04). Cases taken from the sample scripts."""

import pytest

from qmd.config import GrammarConfig
from qmd.grammar import MarkerGrammar, ParseKind
from qmd.models import ParseFlag


@pytest.fixture
def grammar() -> MarkerGrammar:
    return MarkerGrammar(GrammarConfig())


@pytest.mark.parametrize(
    "raw, segments",
    [
        ("10b1", ["10", "b", "1"]),
        ("10.b.1", ["10", "b", "1"]),
        ("10 b 1", ["10", "b", "1"]),
        ("10-b-1", ["10", "b", "1"]),
        ("10b1)", ["10", "b", "1"]),
        ("(10b1)", ["10", "b", "1"]),
        ("10 (b) (1)", ["10", "b", "1"]),
        ("Ans 10b1", ["10", "b", "1"]),
        ("Ans. 10(b)(1)", ["10", "b", "1"]),
        ("Q10b1", ["10", "b", "1"]),
        ("Q.No 10 b 1", ["10", "b", "1"]),
        ("QNo10b1)", ["10", "b", "1"]),
        ("5(a)(ii)", ["5", "a", "ii"]),
        ("Ans 5.", ["5"]),
        ("3).", ["3"]),
        ("QN.7)", ["7"]),
    ],
)
def test_requirement_formats(grammar, raw, segments):
    parsed = grammar.parse(raw)
    assert parsed.kind == ParseKind.MARKER
    assert parsed.segments == segments


def test_prefix_detected(grammar):
    assert grammar.parse("QNo6)").prefix == "QNO"
    assert grammar.parse("Ans 5").prefix == "ANS"


def test_clipped_prefix_residue_is_ignored(grammar):
    parsed = grammar.parse("s 1.")          # "Ans 1." with "An" cut off by the scan edge
    assert parsed.segments == ["1"]
    assert ParseFlag.PREFIX_RESIDUE in parsed.flags
    assert grammar.parse("(N.7)").segments == ["7"]


def test_noise_digit_variants_only_at_page_edge(grammar):
    at_edge = grammar.parse("63.", allow_noise_strip=True)     # "s 3." read as "63."
    assert ("3",) in [v.segments for v in at_edge.variants]
    not_edge = grammar.parse("63.", allow_noise_strip=False)
    assert [v.segments for v in not_edge.variants] == [("63",)]


def test_trailing_answer_text_is_trimmed(grammar):
    parsed = grammar.parse("Ans 8. From")
    assert parsed.segments == ["8"]
    assert ParseFlag.LINE_TRIMMED in parsed.flags


def test_trailing_single_letter_produces_trim_variant(grammar):
    parsed = grammar.parse("Ans 8. f")
    assert parsed.segments == ["8", "f"]
    assert ("8",) in [v.segments for v in parsed.variants]


@pytest.mark.parametrize("raw", ["Q.No.", "No", "ANo", "LNo."])
def test_prefix_only(grammar, raw):
    assert grammar.parse(raw).kind == ParseKind.PREFIX_ONLY


@pytest.mark.parametrize("raw", ["Section B", "", "り", "一", "Section 2", "9880314362"])
def test_not_a_marker(grammar, raw):
    assert grammar.parse(raw).kind == ParseKind.NOT_A_MARKER


def test_circled_digit_is_normalised(grammar):
    # NFKC turns the circled "②" into "2"; the position check (not the grammar) rejects it.
    assert grammar.parse("②").segments == ["2"]


# --- ASCII-only question markers (README A-16) ------------------------------


@pytest.mark.parametrize("raw", ["१२", "३)", "೫"])      # Devanagari / Kannada digits
def test_non_ascii_digits_are_not_markers(grammar, raw):
    assert grammar.parse(raw).kind == ParseKind.NOT_A_MARKER


def test_prefix_with_non_ascii_digits_goes_to_review_path(grammar):
    # The prefix is still recognised, so the pipeline reports PREFIX_WITHOUT_ID
    # (human review) instead of guessing or silently dropping it.
    assert grammar.parse("Ans १२").kind == ParseKind.PREFIX_ONLY


def test_non_ascii_punctuation_is_tolerated(grammar):
    assert grammar.parse("Ans 5。").segments == ["5"]      # CJK full stop from the recognizer
    assert grammar.parse("Ans ５").segments == ["5"]       # full-width digit -> NFKC -> ASCII
