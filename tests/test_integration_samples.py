"""Optional integration test with a REAL OCR backend on real scanned PDFs.

Skipped unless QMD_SAMPLES_DIR points to a folder of PDFs, e.g.:

    QMD_SAMPLES_DIR=./samples QMD_OCR__BACKEND=onnx pytest -m ocr

It checks invariants (not accuracy - accuracy needs a labelled dataset, see
Requirements §6): every page gets a result, coordinates lie inside the page,
question IDs come only from the question list, question starts sit on the
margin rule.
"""

import math
import os
from pathlib import Path

import pytest

from qmd.config import load_settings
from qmd.models import MarkerStatus, ProcessingStatus, QuestionStartMethod
from qmd.question_list import load_question_list
from qmd.runner import Runner

SAMPLES = os.environ.get("QMD_SAMPLES_DIR")

pytestmark = [
    pytest.mark.ocr,
    pytest.mark.skipif(not SAMPLES, reason="set QMD_SAMPLES_DIR to run the real-OCR integration test"),
]


def test_real_scripts(tmp_path):
    settings = load_settings(Path("config/default.yaml"), app={"output_root": str(tmp_path)})
    questions = load_question_list(Path(os.environ.get("QMD_SAMPLES_QUESTIONS",
                                                      "examples/questions/eng221_ese_2023.yaml")))
    pdfs = sorted(Path(SAMPLES).glob("*.pdf"))
    assert pdfs, "no PDFs in QMD_SAMPLES_DIR"
    with Runner(settings, questions) as runner:
        for pdf in pdfs:
            result = runner.process_script(pdf)
            assert len(result.pages) == result.page_count
            for page in result.pages:
                assert page.processing_status != ProcessingStatus.FAILED_PERMANENT, page.error_reason
                for m in page.markers:
                    assert 0 <= m.marker_x < m.page_image_width and 0 <= m.marker_y < m.page_image_height
                    if m.canonical_question_id:
                        assert m.canonical_question_id in questions.questions
                    if m.status != MarkerStatus.DISCARDED and m.question_start_method == QuestionStartMethod.LANDMARK_LINE:
                        # On a skewed page the rule is slanted in canonical coordinates.
                        slack = 3 + abs(math.tan(math.radians(page.layout.skew_deg))) * m.page_image_height
                        assert abs(m.question_start_x - page.layout.margin_rule_x) <= slack
