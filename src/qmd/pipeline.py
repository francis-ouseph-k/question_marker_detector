"""Page processing pipeline (TD §34, steps 1-14).

``PageProcessor.process_page`` is a pure "page job": given a PDF path and page
index it returns a ``PageResult`` and writes that page's diagnostic artefacts.
It holds no per-script state, so the same code can later run inside a
queue-driven worker (TD §2, §23) without changes.

Coordinate spaces used here (see ``geometry.py``):

* canonical - the page's native scan image; everything we SAVE is in this space
* work      - canonical, possibly deskewed (``canonical_to_work`` affine)
* region    - the margin crop of the work image (``region.offset_x/y``)
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from qmd import __version__, geometry
from qmd.artefacts import draw_overlay, write_bytes, write_image
from qmd.config import Settings, mm_to_px
from qmd.detection import RegionCandidate, create_detector
from qmd.gate import ReviewGate
from qmd.grammar import MarkerGrammar, ParsedMarker, ParseKind
from qmd.ink import is_colour_scan, to_student_gray
from qmd.models import (
    BBox,
    ContentClass,
    DiscardReason,
    MarkerResult,
    MarkerStatus,
    PageLayout,
    PageResult,
    PageRole,
    ProcessingStatus,
    ResolutionMethod,
    ReviewReason,
)
from qmd.ocr import OcrEngine, create_engine
from qmd.ocr.base import EngineInfo
from qmd.ocr.calibration import Calibrator
from qmd.page_layout import WorkLayout, detect_layout, prepare_work_image, to_canonical_layout
from qmd.pdf_loader import CanonicalPage, load_page
from qmd.question_list import QuestionList
from qmd.question_start import QuestionStart, QuestionStartFinder, answer_ink_mask
from qmd.region import MarginRegion, build_margin_region
from qmd.resolver import Outcome, QuestionResolver, Resolution
from qmd.triage import classify_content

log = logging.getLogger(__name__)

PREPROCESSING_VERSION = f"qmd-{__version__}/layout-1/ink-1"
PARSER_VERSION = f"qmd-{__version__}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def page_id_for(script_id: str, page_index: int) -> str:
    return f"{script_id}-P{page_index:03d}"


@dataclass
class _Item:
    """Working record for one candidate while a page is processed."""

    candidate: RegionCandidate
    box_work: BBox
    band: Optional[int]
    text: str = ""
    raw_conf: float = 0.0
    conf: float = 0.0
    parsed: Optional[ParsedMarker] = None
    resolution: Optional[Resolution] = None
    position_valid: bool = True
    discard: Optional[DiscardReason] = None
    status: Optional[MarkerStatus] = None
    reasons: list[ReviewReason] = field(default_factory=list)
    start: Optional[QuestionStart] = None
    crop_path: Optional[str] = None


@dataclass
class _PageContext:
    """Everything derived from one page image that the steps share."""

    canonical: CanonicalPage
    colour: bool
    work: np.ndarray
    work_to_canonical: geometry.Affine
    layout: WorkLayout
    layout_report: PageLayout
    student_gray: np.ndarray
    region: MarginRegion
    answer_ink: np.ndarray


class PageProcessor:
    """Processes single pages. Create once per worker process (loads the OCR models)."""

    def __init__(self, settings: Settings, questions: QuestionList, engine: Optional[OcrEngine] = None) -> None:
        self.s = settings
        self.questions = questions
        need_detector = settings.detection.method == "ocr_model"
        self.engine = engine or create_engine(settings.ocr, need_detector=need_detector)
        self.calibrator = Calibrator.from_file(settings.ocr.calibration_file)
        self.grammar = MarkerGrammar(settings.grammar)
        self.resolver = QuestionResolver(questions, settings.resolution)
        self.detector = create_detector(settings.detection, self.engine.detector)
        self.gate = ReviewGate(settings.gate)
        self.start_finder = QuestionStartFinder(settings.question_start)
        self.processing_version = settings.processing_version()

    @property
    def engine_info(self) -> EngineInfo:
        return self.engine.info

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def process_page(self, pdf_path: Path, script_id: str, page_index: int, out_dir: Path) -> PageResult:
        started = time.perf_counter()
        role = self.s.booklet.page_roles.get(page_index, self.s.booklet.default_role)
        result = PageResult(
            script_id=script_id,
            page_id=page_id_for(script_id, page_index),
            page_index=page_index,
            page_role=role,
            processing_status=ProcessingStatus.PROCESSED,
            processing_version=self.processing_version,
        )
        if role != PageRole.ANSWER:
            # NFR-09: excluded pages (e.g. the cover with personal data) are not decoded or OCR'd.
            result.processing_status = ProcessingStatus.SKIPPED_BY_ROLE
            result.content_class = ContentClass.TEMPLATE
            log.info("page %d: skipped (role %s)", page_index, role.value)
            return result

        page = load_page(pdf_path, page_index, self.s.pdf.canonical_dpi, self.s.pdf.full_page_image_min_coverage)
        result.page_image_width, result.page_image_height = page.width, page.height
        result.page_image_dpi = page.dpi
        result.canonical_image_source = page.source
        self._save_page_image(page, out_dir)

        ctx = self._prepare(page)
        result.is_colour = ctx.colour
        result.layout = ctx.layout_report
        if not ctx.colour:
            result.flags.append("COLOUR_SEPARATION_UNAVAILABLE")
        if not ctx.layout.found:
            result.flags.append("LANDMARKS_NOT_FOUND")
        result.suppressed_valuer_ink = [self._box_to_canonical(b, ctx) for b in ctx.region.valuer_boxes_work]

        top = ctx.layout.header_y or 0
        result.content_class, n_components = classify_content(
            ctx.student_gray, page.dpi, self.s.triage, self.s.ink.dark_ink_threshold, top_y=top)

        items = self._detect_and_recognise(ctx, script_id, page_index, out_dir)
        self._classify_items(items, ctx)
        result.markers = [self._to_marker_result(it, ctx, script_id, result.page_id, page_index) for it in items]
        result.marker_count = sum(1 for m in result.markers if m.status != MarkerStatus.DISCARDED)

        if self.s.app.save_debug_images:
            write_image(out_dir / "debug" / f"page_{page_index:03d}_margin.png", ctx.region.gray)
            write_image(out_dir / "debug" / f"page_{page_index:03d}_overlay.jpg", draw_overlay(page.image, result))

        result.processing_ms = int((time.perf_counter() - started) * 1000)
        log.info("page %d: %s, %d ink components, layout=%s, %d candidates -> %d markers (%d ms)",
                 page_index, result.content_class.value if result.content_class else "-", n_components,
                 "ok" if ctx.layout.found else "NOT FOUND", len(items), result.marker_count, result.processing_ms)
        return result

    # ------------------------------------------------------------------ #
    # Steps
    # ------------------------------------------------------------------ #

    def _prepare(self, page: CanonicalPage) -> _PageContext:
        """Steps 3-6: colour check, deskew, landmarks, student-ink image, margin region."""
        colour = is_colour_scan(page.image, self.s.ink)
        deskew = prepare_work_image(page.image, page.dpi, self.s.layout)
        work_to_canonical = geometry.invert(deskew.canonical_to_work)
        layout = detect_layout(deskew.image, page.dpi, self.s.layout)
        parity = "odd" if page.page_index % 2 else "even"
        report = to_canonical_layout(layout, work_to_canonical, parity, deskew.skew_deg, deskew.deskewed)
        student_gray, valuer_mask = to_student_gray(deskew.image, self.s.ink, colour)
        region = build_margin_region(student_gray, valuer_mask, layout, page.dpi, self.s.region, self.s.ink)
        answer_ink = answer_ink_mask(student_gray, self.s.ink.dark_ink_threshold, page.dpi)
        return _PageContext(page, colour, deskew.image, work_to_canonical, layout, report, student_gray, region,
                            answer_ink)

    def _detect_and_recognise(self, ctx: _PageContext, script_id: str, page_index: int, out_dir: Path) -> list[_Item]:
        """Steps 7-8: find candidates, crop them, run the recognizer (batched)."""
        region = ctx.region
        candidates = self.detector.detect(region)
        pad = max(2, mm_to_px(self.s.detection.crop_padding_mm, region.dpi))
        items: list[_Item] = []
        crops: list[np.ndarray] = []
        for number, cand in enumerate(candidates, start=1):
            b = cand.box
            y0, y1 = max(0, b.y - pad), min(region.gray.shape[0], b.y2 + pad)
            x0, x1 = max(0, b.x - pad), min(region.gray.shape[1], b.x2 + pad)
            crop = region.gray[y0:y1, x0:x1]
            # White border: recognizers expect some background around the text.
            border = max(4, crop.shape[0] // 4)
            crop = cv2.copyMakeBorder(crop, border // 2, border // 2, border, border, cv2.BORDER_CONSTANT, value=255)
            crop_bgr = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
            crops.append(crop_bgr)
            box_work = BBox(x=b.x + region.offset_x, y=b.y + region.offset_y, width=b.width, height=b.height)
            item = _Item(candidate=cand, box_work=box_work, band=ctx.layout.band_index(box_work.center_y))
            if self.s.app.save_marker_crops:
                rel = f"crops/page_{page_index:03d}_c{number:02d}.png"
                write_image(out_dir / rel, crop_bgr)
                item.crop_path = rel
            items.append(item)

        texts = self.engine.recognizer.recognize(crops) if crops else []
        for item, res in zip(items, texts):
            item.text = res.text
            item.raw_conf = res.recognition_confidence
            item.conf = self.calibrator(res.recognition_confidence)
            log.debug("page %d candidate at %s: %r (%.3f)", page_index, item.box_work, item.text, item.raw_conf)
        return items

    def _classify_items(self, items: list[_Item], ctx: _PageContext) -> None:
        """Steps 9-13: position check, grammar, resolution, question start, gate."""
        layout, dpi = ctx.layout, ctx.region.dpi
        spill = mm_to_px(self.s.region.spill_mm, dpi)
        edge_px = max(2, mm_to_px(0.5, dpi))

        for it in items:
            b = it.box_work
            if layout.margin_rule_x is not None:
                crosses = b.x < layout.margin_rule_x <= b.x2
                it.position_valid = b.center_x < layout.margin_rule_x + spill / 2 or crosses
                if b.x >= layout.margin_rule_x:
                    it.discard = DiscardReason.INSIDE_ANSWER_AREA
                    continue
            it.parsed = self.grammar.parse(it.text, allow_noise_strip=b.x <= edge_px)
            if it.parsed.kind == ParseKind.NOT_A_MARKER:
                it.discard = DiscardReason.NOT_A_MARKER_PATTERN
            elif _implausible_single_char(it.text, b, self.s.detection.single_char_max_aspect):
                it.discard = DiscardReason.IMPLAUSIBLE_SHAPE
            elif it.parsed.kind == ParseKind.MARKER:
                it.resolution = self.resolver.resolve(it.parsed)

        live = sorted((it for it in items if it.discard is None), key=lambda i: i.box_work.y)

        # Prefix-only tokens ("Q.No.", or a marker whose number OCR lost): header if a
        # real marker follows within a few lines, otherwise send to review (README A-7).
        lookahead = self.s.grammar.header_lookahead_lines
        for it in live:
            if it.parsed and it.parsed.kind == ParseKind.PREFIX_ONLY:
                below = [o for o in live if o is not it and o.parsed and o.parsed.kind == ParseKind.MARKER
                         and o.box_work.y >= it.box_work.y and _band_distance(it, o) <= lookahead]
                if below:
                    it.discard = DiscardReason.HEADER_TOKEN
        live = [it for it in live if it.discard is None]

        for idx, it in enumerate(live):
            next_band = live[idx + 1].band if idx + 1 < len(live) else None
            it.start = self.start_finder.find(ctx.answer_ink, layout, it.box_work, next_band, dpi)
            if it.parsed and it.parsed.kind == ParseKind.PREFIX_ONLY:
                it.status, it.reasons = MarkerStatus.UNRESOLVED, [ReviewReason.PREFIX_WITHOUT_ID]
                continue
            assert it.resolution is not None and it.parsed is not None
            flags = list(dict.fromkeys(it.parsed.flags + it.resolution.flags))
            decision = self.gate.decide(it.resolution, it.conf, flags, it.position_valid, it.start,
                                        layout.found, ctx.colour)
            it.status, it.reasons = decision.status, decision.reasons

    # ------------------------------------------------------------------ #
    # Output conversion
    # ------------------------------------------------------------------ #

    def _box_to_canonical(self, box: BBox, ctx: _PageContext) -> BBox:
        poly = geometry.polygon_to_canonical(ctx.work_to_canonical, geometry.box_polygon(box.x, box.y, box.width, box.height))
        bb = geometry.bbox_of_polygon(poly)
        return geometry.clamp_bbox(bb, ctx.canonical.width, ctx.canonical.height)

    @staticmethod
    def _work_polygon(it: _Item, ctx: _PageContext) -> list[list[float]]:
        """Detector outline if we have one (TD §8), otherwise the box corners (work coords)."""
        if it.candidate.polygon:
            return [[x + ctx.region.offset_x, y + ctx.region.offset_y] for x, y in it.candidate.polygon]
        b = it.box_work
        return geometry.box_polygon(b.x, b.y, b.width, b.height)

    def _to_marker_result(self, it: _Item, ctx: _PageContext, script_id: str, page_id: str,
                          page_index: int) -> MarkerResult:
        box = self._box_to_canonical(it.box_work, ctx)
        polygon = geometry.polygon_to_canonical(ctx.work_to_canonical, self._work_polygon(it, ctx))
        start_x = start_y = None
        if it.start is not None:
            pt = geometry.point_to_canonical(ctx.work_to_canonical, it.start.x, it.start.y)
            start_x, start_y = pt.x, pt.y
        parsed, res = it.parsed, it.resolution
        flags = list(dict.fromkeys((parsed.flags if parsed else []) + (res.flags if res else [])))
        status = MarkerStatus.DISCARDED if it.discard else (it.status or MarkerStatus.UNRESOLVED)
        info = self.engine.info
        x_bucket = box.x // max(1, mm_to_px(5, ctx.canonical.dpi))
        ident = f"{script_id}|{page_id}|{self.processing_version}|{it.band}|{x_bucket}|{box.y}"
        return MarkerResult(
            marker_result_id=hashlib.sha1(ident.encode()).hexdigest()[:16],
            script_id=script_id,
            page_id=page_id,
            page_index=page_index,
            processing_version=self.processing_version,
            line_index=it.band,
            raw_ocr_text=it.text,
            parsed_marker=parsed.parsed_text if parsed else None,
            normalized_marker=(res.variant.key if res and res.variant else (parsed.normalized if parsed else None)),
            canonical_question_id=res.question.canonical if res and res.question and res.outcome == Outcome.RESOLVED else None,
            candidate_list=res.candidates if res else [],
            resolution_method=res.method if res else ResolutionMethod.NONE,
            parse_flags=flags,
            detection_confidence=round(it.candidate.detection_confidence, 4),
            recognition_confidence=round(it.conf, 4),
            recognition_confidence_raw=round(it.raw_conf, 4),
            resolution_confidence=round(res.confidence, 4) if res else None,
            resolution_margin=round(res.margin, 4) if res else None,
            page_image_width=ctx.canonical.width,
            page_image_height=ctx.canonical.height,
            page_image_dpi=ctx.canonical.dpi,
            marker_polygon=polygon,
            marker_x=box.x,
            marker_y=box.y,
            marker_width=box.width,
            marker_height=box.height,
            question_start_x=start_x,
            question_start_y=start_y,
            question_start_method=it.start.method if it.start else None,
            question_start_confidence=round(it.start.confidence, 3) if it.start else None,
            status=status,
            review_reasons=[] if it.discard else it.reasons,
            discard_reason=it.discard,
            ocr_engine=info.ocr_engine,
            ocr_model=info.ocr_model,
            ocr_model_version=info.ocr_model_version,
            preprocessing_version=PREPROCESSING_VERSION,
            grammar_version=self.s.grammar.version,
            parser_version=PARSER_VERSION,
            question_list_version=self.questions.version_label,
            config_version=self.processing_version,
            created_at=utc_now(),
            debug_crop_path=it.crop_path,
        )

    def _save_page_image(self, page: CanonicalPage, out_dir: Path) -> None:
        if not self.s.app.save_page_images:
            return
        if page.original_bytes:
            write_bytes(out_dir / "pages" / f"page_{page.page_index:03d}{page.original_ext}", page.original_bytes)
        else:
            write_image(out_dir / "pages" / f"page_{page.page_index:03d}.png", page.image)


def _implausible_single_char(text: str, box: BBox, max_aspect: float) -> bool:
    """One alphanumeric character read from a box much wider than tall (README A-9)."""
    alnum = [ch for ch in text if ch.isalnum()]
    return len(alnum) == 1 and box.width > max_aspect * box.height


def _band_distance(a: _Item, b: _Item) -> int:
    if a.band is None or b.band is None:
        return 0 if abs(a.box_work.y - b.box_work.y) < 3 * max(a.box_work.height, 1) else 99
    return abs(b.band - a.band)
