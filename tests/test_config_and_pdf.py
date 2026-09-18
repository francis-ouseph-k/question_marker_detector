"""Configuration loading (NFR-07) and PDF canonical image (TD §5.1)."""

from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest

from qmd.config import load_settings, mm_to_px
from qmd.errors import ConfigurationError, PdfReadError
from qmd.pdf_loader import count_pages, load_page
from tests.conftest import make_ruled_page, write_pdf


def test_default_yaml_loads():
    s = load_settings(Path("config/default.yaml"))
    assert s.ocr.backend == "paddle"
    assert s.ocr.rec_model_name == "PP-OCRv5_mobile_rec"
    assert s.booklet.page_roles[1].value == "COVER"


def test_env_overrides_yaml(monkeypatch):
    monkeypatch.setenv("QMD_OCR__BACKEND", "onnx")
    monkeypatch.setenv("QMD_PROCESSING__WORKERS", "3")
    s = load_settings(Path("config/default.yaml"))
    assert s.ocr.backend == "onnx" and s.processing.workers == 3


def test_unknown_section_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("ocrr: {backend: onnx}\n")
    with pytest.raises(ConfigurationError, match="Unknown configuration"):
        load_settings(p)


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("ocr: {backnd: onnx}\n")
    with pytest.raises(ConfigurationError):
        load_settings(p)


def test_invalid_value_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("layout: {line_pitch_min_mm: 20, line_pitch_max_mm: 10}\n")
    with pytest.raises(ConfigurationError):
        load_settings(p)


def test_line_snap_tolerance_is_validated(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("layout: {line_snap_tolerance_fraction: 0.6}\n")
    with pytest.raises(ConfigurationError):
        load_settings(p)
    assert load_settings(Path("config/default.yaml")).layout.line_snap_tolerance_fraction == 0.25


def test_processing_version_changes_with_config():
    a = load_settings(Path("config/default.yaml"))
    b = load_settings(Path("config/default.yaml"), gate={"min_recognition_confidence": 0.9})
    assert a.processing_version() != b.processing_version()
    assert a.processing_version() == load_settings(Path("config/default.yaml")).processing_version()


def test_mm_to_px():
    assert mm_to_px(25.4, 200) == 200


# --------------------------------------------------------------------------- #


def test_embedded_page_native_resolution(tmp_path):
    page = make_ruled_page(markers=[(3, "Ans 2")])
    pdf = write_pdf([page.image, page.image], tmp_path / "s.pdf", dpi=200)
    assert count_pages(pdf) == 2
    cp = load_page(pdf, 1, render_dpi=150)
    assert cp.source == "embedded"
    assert (cp.width, cp.height) == (page.image.shape[1], page.image.shape[0])
    assert cp.dpi == pytest.approx(200, abs=1)
    assert cp.original_bytes and cp.original_bytes[:2] == b"\xff\xd8"   # original JPEG kept


def test_rotated_page_is_made_upright(tmp_path):
    page = make_ruled_page()
    pdf = write_pdf([page.image], tmp_path / "r.pdf")
    doc = pdfium.PdfDocument(str(pdf))
    doc[0].set_rotation(90)
    rotated = tmp_path / "rot.pdf"
    doc.save(str(rotated))
    doc.close()
    cp = load_page(rotated, 1, render_dpi=200)
    assert (cp.width, cp.height) == (page.image.shape[0], page.image.shape[1])   # swapped
    assert cp.original_bytes is None


def test_page_out_of_range_and_bad_file(tmp_path):
    pdf = write_pdf([make_ruled_page().image], tmp_path / "s.pdf")
    with pytest.raises(PdfReadError):
        load_page(pdf, 5, 200)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(PdfReadError):
        count_pages(bad)
    assert isinstance(np.zeros(1), np.ndarray)
