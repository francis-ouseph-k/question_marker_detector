"""Confidence / review gate (TD §19.1, Requirements FR-13).

A marker is ACCEPTED only if ALL conditions hold; otherwise it is sent to
review with one or more reason codes. Script-level checks (``consolidation.py``)
can later downgrade ACCEPTED markers to REVIEW_REQUIRED.

Principle (Problem Statement §9): a wrong automatic acceptance is worse than a
human review. When in doubt, review.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from qmd.config import GateConfig
from qmd.models import MarkerStatus, ParseFlag, QuestionStartMethod, ReviewReason
from qmd.question_start import QuestionStart
from qmd.resolver import Outcome, Resolution


@dataclass
class GateDecision:
    status: MarkerStatus
    reasons: list[ReviewReason] = field(default_factory=list)


class ReviewGate:
    def __init__(self, cfg: GateConfig) -> None:
        self.cfg = cfg

    def decide(
        self,
        resolution: Resolution,
        recognition_confidence: float,
        parse_flags: list[ParseFlag],
        position_valid: bool,
        start: QuestionStart,
        landmarks_found: bool,
        colour_scan: bool,
    ) -> GateDecision:
        reasons: list[ReviewReason] = []

        if resolution.outcome == Outcome.NO_MATCH:
            return GateDecision(MarkerStatus.UNRESOLVED, [ReviewReason.NO_MATCH])
        if resolution.outcome == Outcome.AMBIGUOUS:
            reasons.append(ReviewReason.AMBIGUOUS)

        if not position_valid:
            reasons.append(ReviewReason.INVALID_POSITION)

        threshold = self.cfg.min_recognition_confidence
        if ParseFlag.NOISE_STRIPPED in parse_flags or ParseFlag.LINE_TRIMMED in parse_flags:
            threshold = max(threshold, self.cfg.adjusted_parse_min_confidence)
        if not colour_scan:
            threshold = max(threshold, self.cfg.grayscale_min_confidence)
        if recognition_confidence < threshold:
            reasons.append(ReviewReason.LOW_RECOGNITION_CONFIDENCE)

        if resolution.outcome == Outcome.RESOLVED and resolution.method.value == "CONSTRAINED_UNIQUE" \
                and resolution.margin < self.cfg.min_resolution_margin:
            reasons.append(ReviewReason.AMBIGUOUS)
        if resolution.is_confusable:
            # e.g. "1(1)" vs "11": only a probability-based path could separate them (TD §12.3).
            reasons.append(ReviewReason.AMBIGUOUS)

        if not landmarks_found or start.method == QuestionStartMethod.FALLBACK_OFFSET:
            reasons.append(ReviewReason.LANDMARKS_NOT_FOUND)
        elif start.confidence < self.cfg.min_question_start_confidence:
            reasons.append(ReviewReason.START_UNCERTAIN)

        reasons = list(dict.fromkeys(reasons))  # de-duplicate, keep order
        return GateDecision(MarkerStatus.REVIEW_REQUIRED if reasons else MarkerStatus.ACCEPTED, reasons)
