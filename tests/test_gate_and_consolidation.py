"""Review gate (TD §19) and script-level consolidation (TD §23a, FR-17)."""

from qmd.config import GateConfig
from qmd.consolidation import consolidate
from qmd.gate import ReviewGate
from qmd.models import (
    ContentClass,
    MarkerResult,
    MarkerStatus,
    PageResult,
    PageRole,
    ParseFlag,
    ProcessingStatus,
    QuestionStartMethod,
    ResolutionMethod,
    ReviewReason,
    ScriptFlag,
    ScriptResult,
)
from qmd.question_list import build_question_list
from qmd.question_start import QuestionStart
from qmd.resolver import Outcome, Resolution

GOOD_START = QuestionStart(100, 400, QuestionStartMethod.LANDMARK_LINE, 0.95, 0)


def _res(outcome=Outcome.RESOLVED, method=ResolutionMethod.EXACT_UNIQUE, margin=1.0, question=None):
    return Resolution(outcome, method, question, 1.0, margin)


def _decide(**kw):
    args = dict(resolution=_res(), recognition_confidence=0.95, parse_flags=[], position_valid=True,
                start=GOOD_START, landmarks_found=True, colour_scan=True)
    args.update(kw)
    return ReviewGate(GateConfig()).decide(**args)


def test_accept_when_everything_ok():
    assert _decide().status == MarkerStatus.ACCEPTED


def test_low_confidence_reviews():
    d = _decide(recognition_confidence=0.5)
    assert d.status == MarkerStatus.REVIEW_REQUIRED and ReviewReason.LOW_RECOGNITION_CONFIDENCE in d.reasons


def test_adjusted_parse_needs_higher_confidence():
    assert _decide(recognition_confidence=0.85).status == MarkerStatus.ACCEPTED
    d = _decide(recognition_confidence=0.85, parse_flags=[ParseFlag.NOISE_STRIPPED])
    assert d.status == MarkerStatus.REVIEW_REQUIRED


def test_grayscale_needs_higher_confidence():
    assert _decide(recognition_confidence=0.85, colour_scan=False).status == MarkerStatus.REVIEW_REQUIRED


def test_no_match_is_unresolved():
    d = _decide(resolution=_res(Outcome.NO_MATCH, ResolutionMethod.NONE))
    assert d.status == MarkerStatus.UNRESOLVED and d.reasons == [ReviewReason.NO_MATCH]


def test_ambiguous_reviews():
    d = _decide(resolution=_res(Outcome.AMBIGUOUS, ResolutionMethod.NONE))
    assert ReviewReason.AMBIGUOUS in d.reasons


def test_landmarks_missing_reviews():
    fallback = QuestionStart(100, 400, QuestionStartMethod.FALLBACK_OFFSET, 0.2)
    d = _decide(start=fallback, landmarks_found=False)
    assert ReviewReason.LANDMARKS_NOT_FOUND in d.reasons


def test_confusable_reviews():
    ql = build_question_list(["1(1)", "11"])
    d = _decide(resolution=_res(question=ql.questions["11"]))
    assert ReviewReason.AMBIGUOUS in d.reasons


# --------------------------------------------------------------------------- #


def _marker(page, qid, y=500, status=MarkerStatus.ACCEPTED):
    return MarkerResult(
        marker_result_id=f"{page}-{qid}-{y}", script_id="S", page_id=f"S-P{page:03d}", page_index=page,
        processing_version="t", raw_ocr_text=qid, canonical_question_id=qid, status=status,
        page_image_width=1000, page_image_height=2000, page_image_dpi=200, marker_x=0, marker_y=y,
        marker_width=10, marker_height=10, ocr_engine="f", ocr_model="f", ocr_model_version="f",
        preprocessing_version="f", grammar_version="f", parser_version="f", question_list_version="f",
        config_version="f", created_at="now",
    )


def _page(idx, markers, content=ContentClass.NORMAL, status=ProcessingStatus.PROCESSED):
    return PageResult(script_id="S", page_id=f"S-P{idx:03d}", page_index=idx, page_role=PageRole.ANSWER,
                      processing_status=status, content_class=content, markers=markers, processing_version="t")


def _script(pages):
    return ScriptResult(script_id="S", source_pdf="x", source_sha256="0", page_count=len(pages),
                        processing_version="t", question_list_version="q", pages=pages, started_at="now")


def test_clean_script_has_no_flags(eight_questions):
    s = _script([_page(3, [_marker(3, "1")]), _page(4, [_marker(4, "2"), _marker(4, "3", y=900)])])
    consolidate(s, eight_questions)
    assert s.flags == []
    assert all(m.status == MarkerStatus.ACCEPTED for p in s.pages for m in p.markers)


def test_duplicate_and_order(eight_questions):
    s = _script([_page(3, [_marker(3, "1")]), _page(4, [_marker(4, "3")]), _page(5, [_marker(5, "1")])])
    consolidate(s, eight_questions)
    assert ScriptFlag.DUPLICATE_QUESTION in s.flags and ScriptFlag.OUT_OF_ORDER in s.flags
    first = s.pages[0].markers[0]
    assert first.status == MarkerStatus.REVIEW_REQUIRED
    assert ReviewReason.DUPLICATE_IN_SCRIPT in first.review_reasons
    # IDs are never changed by consolidation
    assert [m.canonical_question_id for p in s.pages for m in p.markers] == ["1", "3", "1"]


def test_answer_count_rules():
    ql = build_question_list(["1", "2", "3"], rules={"min_answers": 3})
    s = _script([_page(3, [_marker(3, "1")])])
    consolidate(s, ql)
    assert ScriptFlag.ANSWER_COUNT_MISMATCH in s.flags


def test_missed_marker_and_failed_pages(eight_questions):
    s = _script([_page(3, []), _page(4, [_marker(4, "2")]),
                 _page(5, [], status=ProcessingStatus.FAILED_PERMANENT, content=None)])
    consolidate(s, eight_questions)
    assert ScriptFlag.POSSIBLE_MISSED_MARKER in s.flags
    assert ScriptFlag.INCOMPLETE in s.flags


def test_no_markers(eight_questions):
    s = _script([_page(3, []), _page(4, [], content=ContentClass.BLANK)])
    consolidate(s, eight_questions)
    assert ScriptFlag.NO_MARKERS in s.flags
