"""Script-level consolidation (TD §23a, Requirements FR-17).

Runs once all pages of a script are final. It may DOWNGRADE markers from
ACCEPTED to REVIEW_REQUIRED and set script flags; it never changes a question ID.
"""

from __future__ import annotations

import logging

from qmd.models import (
    ContentClass,
    MarkerResult,
    MarkerStatus,
    PageRole,
    ProcessingStatus,
    ReviewReason,
    ScriptFlag,
    ScriptResult,
)
from qmd.question_list import QuestionList

log = logging.getLogger(__name__)

_LIVE = (MarkerStatus.ACCEPTED, MarkerStatus.REVIEW_REQUIRED)


def _flag_marker(marker: MarkerResult, reason: ReviewReason) -> None:
    if reason not in marker.review_reasons:
        marker.review_reasons.append(reason)
    if marker.status == MarkerStatus.ACCEPTED:
        marker.status = MarkerStatus.REVIEW_REQUIRED


def consolidate(script: ScriptResult, questions: QuestionList) -> None:
    """Apply script-level checks in place."""
    flags: list[ScriptFlag] = []
    details: list[str] = []

    ordered = sorted(
        (m for p in script.pages for m in p.markers if m.status in _LIVE and m.canonical_question_id),
        key=lambda m: (m.page_index, m.marker_y),
    )

    # 1. Duplicate question IDs.
    by_id: dict[str, list[MarkerResult]] = {}
    for m in ordered:
        by_id.setdefault(m.canonical_question_id or "", []).append(m)
    for qid, group in by_id.items():
        if len(group) > 1:
            flags.append(ScriptFlag.DUPLICATE_QUESTION)
            pages = ", ".join(str(m.page_index) for m in group)
            details.append(f"Question {qid} appears {len(group)} times (pages {pages})")
            for m in group:
                _flag_marker(m, ReviewReason.DUPLICATE_IN_SCRIPT)

    # 2. Out of order: a marker whose question comes before one already seen.
    highest = -1
    highest_marker: MarkerResult | None = None
    for m in ordered:
        q = questions.get(m.canonical_question_id or "")
        if q is None:
            continue
        if q.order < highest and highest_marker is not None:
            flags.append(ScriptFlag.OUT_OF_ORDER)
            details.append(f"Question {m.canonical_question_id} (page {m.page_index}) after "
                           f"{highest_marker.canonical_question_id} (page {highest_marker.page_index})")
            _flag_marker(m, ReviewReason.OUT_OF_ORDER)
            _flag_marker(highest_marker, ReviewReason.OUT_OF_ORDER)
        if q.order > highest:
            highest, highest_marker = q.order, m

    # 3. Answer count vs answering rules.
    distinct = len(by_id)
    rules = questions.rules
    if (rules.min_answers is not None and distinct < rules.min_answers) or \
            (rules.max_answers is not None and distinct > rules.max_answers):
        flags.append(ScriptFlag.ANSWER_COUNT_MISMATCH)
        details.append(f"{distinct} distinct questions found; rules allow "
                       f"{rules.min_answers if rules.min_answers is not None else '-'}.."
                       f"{rules.max_answers if rules.max_answers is not None else '-'}")
        for m in ordered:
            _flag_marker(m, ReviewReason.ANSWER_COUNT_MISMATCH)

    # 4. Possible missed markers.
    answer_pages = [p for p in script.pages
                    if p.page_role == PageRole.ANSWER and p.processing_status == ProcessingStatus.PROCESSED]
    written = [p for p in answer_pages if p.content_class in (ContentClass.NORMAL, ContentClass.SPARSE)]
    if written and not ordered:
        flags.append(ScriptFlag.NO_MARKERS)
        details.append("Answer pages contain writing but no question markers were found")
    elif written:
        first = written[0]
        if not any(m.status in _LIVE or m.status == MarkerStatus.UNRESOLVED for m in first.markers):
            flags.append(ScriptFlag.POSSIBLE_MISSED_MARKER)
            details.append(f"First written answer page ({first.page_index}) has no marker")

    # 5. Failed pages.
    failed = [p.page_index for p in script.pages
              if p.processing_status in (ProcessingStatus.FAILED_PERMANENT, ProcessingStatus.FAILED_RETRYABLE)]
    if failed:
        flags.append(ScriptFlag.INCOMPLETE)
        details.append(f"Pages failed: {failed}")

    script.flags = list(dict.fromkeys(flags))
    script.flag_details = details
    for p in script.pages:
        p.marker_count = sum(1 for m in p.markers if m.status in _LIVE or m.status == MarkerStatus.UNRESOLVED)
    if script.flags:
        log.info("script %s flags: %s", script.script_id, [f.value for f in script.flags])
