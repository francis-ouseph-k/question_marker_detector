"""Data models shared by all components.

Everything that leaves a component (and everything we write to disk) is one of
these models. They are Pydantic models so they validate themselves and can be
serialised to JSON with ``model_dump_json()``.

Field names follow Requirements §5 and Technical Design §13 / §18.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Enumerations (Requirements §5.3, TD §13, §18)
# --------------------------------------------------------------------------- #


class MarkerStatus(str, Enum):
    """Final status of one marker candidate (Requirements §5.3)."""

    ACCEPTED = "ACCEPTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNRESOLVED = "UNRESOLVED"
    DISCARDED = "DISCARDED"
    REJECTED = "REJECTED"  # only set by a human reviewer


class ReviewReason(str, Enum):
    """Why a marker was sent to human review (Requirements §5.3)."""

    LOW_RECOGNITION_CONFIDENCE = "LOW_RECOGNITION_CONFIDENCE"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"
    INVALID_POSITION = "INVALID_POSITION"
    LANDMARKS_NOT_FOUND = "LANDMARKS_NOT_FOUND"
    START_UNCERTAIN = "START_UNCERTAIN"
    DUPLICATE_IN_SCRIPT = "DUPLICATE_IN_SCRIPT"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    ANSWER_COUNT_MISMATCH = "ANSWER_COUNT_MISMATCH"
    HUMAN_VS_REPROCESS_CONFLICT = "HUMAN_VS_REPROCESS_CONFLICT"
    # Implementation addition (see README "Assumptions", A-7): a prefix such as
    # "QNo" with no readable question number and no marker below it.
    PREFIX_WITHOUT_ID = "PREFIX_WITHOUT_ID"


class DiscardReason(str, Enum):
    """Why a candidate was automatically classified as non-marker content."""

    VALUER_INK = "VALUER_INK"
    INSIDE_ANSWER_AREA = "INSIDE_ANSWER_AREA"
    HEADER_TOKEN = "HEADER_TOKEN"
    NOT_A_MARKER_PATTERN = "NOT_A_MARKER_PATTERN"
    # Implementation addition (README A-9): one character read from a wide box.
    IMPLAUSIBLE_SHAPE = "IMPLAUSIBLE_SHAPE"


class ResolutionMethod(str, Enum):
    EXACT_UNIQUE = "EXACT_UNIQUE"
    CONSTRAINED_UNIQUE = "CONSTRAINED_UNIQUE"
    NONE = "NONE"


class QuestionStartMethod(str, Enum):
    LANDMARK_LINE = "LANDMARK_LINE"
    FALLBACK_OFFSET = "FALLBACK_OFFSET"
    MANUAL = "MANUAL"


class ResultSource(str, Enum):
    AUTOMATIC = "AUTOMATIC"
    REVIEWER_CORRECTED = "REVIEWER_CORRECTED"
    REVIEWER_ADDED = "REVIEWER_ADDED"


class PageRole(str, Enum):
    """Role of a page in the booklet template (Requirements FR-19)."""

    ANSWER = "ANSWER"
    COVER = "COVER"
    DO_NOT_WRITE = "DO_NOT_WRITE"
    INSTRUCTIONS = "INSTRUCTIONS"


class ProcessingStatus(str, Enum):
    PROCESSED = "PROCESSED"
    SKIPPED_BY_ROLE = "SKIPPED_BY_ROLE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"


class ContentClass(str, Enum):
    BLANK = "BLANK"
    SPARSE = "SPARSE"
    NORMAL = "NORMAL"
    TEMPLATE = "TEMPLATE"


class ParseFlag(str, Enum):
    """Adjustments the grammar parser made to the raw OCR text (TD §10.1)."""

    NOISE_STRIPPED = "NOISE_STRIPPED"      # leading digit(s) dropped as clipped-prefix residue
    LINE_TRIMMED = "LINE_TRIMMED"          # trailing text/segments (answer text) dropped
    PREFIX_RESIDUE = "PREFIX_RESIDUE"      # unknown letters before the ID (e.g. "s" of "Ans")


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #


class BBox(BaseModel):
    """Axis-aligned box in pixels. (x, y) is the top-left corner."""

    x: int
    y: int
    width: int
    height: int

    @property
    def x2(self) -> int:
        return self.x + self.width

    @property
    def y2(self) -> int:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0


class Point(BaseModel):
    x: int
    y: int


# --------------------------------------------------------------------------- #
# Page layout (TD §6.1)
# --------------------------------------------------------------------------- #


class PageLayout(BaseModel):
    """Landmarks found on a page. Coordinates are in CANONICAL page pixels."""

    found: bool = Field(description="True when margin rule and ruled lines were found")
    margin_rule_x: Optional[int] = None
    header_y: Optional[int] = None
    ruled_line_ys: list[int] = Field(default_factory=list)
    line_pitch_px: Optional[float] = None
    parity: Optional[str] = None  # "odd" | "even" (from 1-based page index)
    skew_deg: float = 0.0
    deskewed: bool = False


# --------------------------------------------------------------------------- #
# Marker result (Requirements §5.1, TD §13)
# --------------------------------------------------------------------------- #


class Candidate(BaseModel):
    """One question-marker candidate returned by the resolver, with its score."""

    question_id: str
    score: float


class MarkerResult(BaseModel):
    marker_result_id: str
    script_id: str
    page_id: str
    page_index: int
    processing_version: str
    line_index: Optional[int] = None

    raw_ocr_text: str
    parsed_marker: Optional[str] = None
    normalized_marker: Optional[str] = None
    canonical_question_id: Optional[str] = None
    candidate_list: list[Candidate] = Field(default_factory=list)
    resolution_method: ResolutionMethod = ResolutionMethod.NONE
    parse_flags: list[ParseFlag] = Field(default_factory=list)

    detection_confidence: Optional[float] = None
    recognition_confidence: Optional[float] = None
    recognition_confidence_raw: Optional[float] = None
    resolution_confidence: Optional[float] = None
    resolution_margin: Optional[float] = None

    coordinate_system: str = "CANONICAL_PAGE_PX"
    page_image_width: int
    page_image_height: int
    page_image_dpi: float
    marker_polygon: Optional[list[list[int]]] = None
    marker_x: int
    marker_y: int
    marker_width: int
    marker_height: int

    question_start_x: Optional[int] = None
    question_start_y: Optional[int] = None
    question_start_method: Optional[QuestionStartMethod] = None
    question_start_confidence: Optional[float] = None

    status: MarkerStatus
    review_reasons: list[ReviewReason] = Field(default_factory=list)
    discard_reason: Optional[DiscardReason] = None
    source: ResultSource = ResultSource.AUTOMATIC

    ocr_engine: str
    ocr_model: str
    ocr_model_version: str
    preprocessing_version: str
    grammar_version: str
    parser_version: str
    question_list_version: str
    config_version: str
    created_at: str

    # Relative path (inside the script output folder) of the crop that was fed
    # to the recognizer. Useful when diagnosing OCR errors (TD §25).
    debug_crop_path: Optional[str] = None


# --------------------------------------------------------------------------- #
# Page and script results (Requirements §5.2, TD §18, §23a)
# --------------------------------------------------------------------------- #


class PageResult(BaseModel):
    script_id: str
    page_id: str
    page_index: int  # 1-based
    page_role: PageRole
    processing_status: ProcessingStatus
    content_class: Optional[ContentClass] = None
    marker_count: int = 0
    page_image_width: Optional[int] = None
    page_image_height: Optional[int] = None
    page_image_dpi: Optional[float] = None
    canonical_image_source: Optional[str] = None  # "embedded" | "rendered"
    is_colour: Optional[bool] = None
    layout: Optional[PageLayout] = None
    flags: list[str] = Field(default_factory=list)
    markers: list[MarkerResult] = Field(default_factory=list)
    suppressed_valuer_ink: list[BBox] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_reason: Optional[str] = None
    processing_version: str
    processing_ms: Optional[int] = None


class ScriptFlag(str, Enum):
    DUPLICATE_QUESTION = "DUPLICATE_QUESTION"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    ANSWER_COUNT_MISMATCH = "ANSWER_COUNT_MISMATCH"
    POSSIBLE_MISSED_MARKER = "POSSIBLE_MISSED_MARKER"
    NO_MARKERS = "NO_MARKERS"
    INCOMPLETE = "INCOMPLETE"


class ScriptResult(BaseModel):
    script_id: str
    source_pdf: str
    source_sha256: str
    page_count: int
    processing_version: str
    question_list_version: str
    exam_id: Optional[str] = None
    flags: list[ScriptFlag] = Field(default_factory=list)
    flag_details: list[str] = Field(default_factory=list)
    pages: list[PageResult] = Field(default_factory=list)
    started_at: str
    completed_at: Optional[str] = None
    processing_ms: Optional[int] = None


class ReviewAction(str, Enum):
    CONFIRM = "CONFIRM"
    CORRECT = "CORRECT"
    REJECT = "REJECT"
    ADD = "ADD"


class ReviewRecord(BaseModel):
    """Human review record (TD §26).

    Not produced by the command-line tool; defined here so that a future review
    UI and database share the same contract.
    """

    marker_result_id: str
    action: ReviewAction
    original_value: Optional[dict] = None
    corrected_value: Optional[dict] = None
    reviewer: str
    timestamp: str
    reason: Optional[str] = None
