# Changelog

## 1.0.1 (after external review)

Every point in the external review was checked against the code and the v1.1 specs before any change was made. The "syntax blockers" it reported were artefacts of the review copy, not problems in the source files.

### Changed

- **Detector polygon kept (TD §8).** With `detection.method: ocr_model`, the text detector's own outline, which may be rotated, is now carried through to `marker_polygon`. Merged fragments and component-detector candidates still report the box's corners.
- **ASCII-only question markers (README A-16).** Digits and letters must be ASCII; before this change, Devanagari or Kannada digits could match Python's `\d`. CJK punctuation and full-width forms are still tolerated.
- **Bundled ONNX models are identified by SHA-256 (NFR-06),** not only by the rapidocr version.
- **Calibration files with fewer than 2 points are rejected** at start-up instead of silently meaning "uncalibrated".
- **New setting `layout.line_snap_tolerance_fraction`** (default 0.25; previously hard-coded).

### Not changed (deliberately)

- **BLANK pages still go through marker detection.** Skipping them would violate FR-09 and TD §17.2.
- **Terminology is unchanged.** "Question marker" (the handwriting) and "question identifier" (the valid ID from the exam's list) are different concepts.
- **Question-start documentation is unchanged.** The v1.1 specs already define `LANDMARK_LINE` and `FALLBACK_OFFSET`.

Sample-script results are unchanged. There are 123 tests.

## 1.0.2

- **Added requirements files.** `requirements.txt` is the default PaddleOCR PP-OCRv5 backend. The `requirements/` folder holds `base.txt`, `paddle.txt`, `onnx.txt`, `dev.txt` and the existing lock file.
- **OpenCV moved out of the core dependencies.** Each backend now brings exactly one OpenCV package: `opencv-contrib-python` for paddle, `opencv-python` for onnx. Before this, installing the paddle backend put two OpenCV packages into the environment.
- **`PyYAML` is now a version range,** because paddlex pins its own exact version.
- **`pillow` added to the dev dependencies.** The tests use it, and it was previously only present by accident, pulled in by rapidocr.
- **Verified:** fresh environments built from `requirements.txt` (paddle) and from `requirements/onnx.txt` both install cleanly with exactly one OpenCV package. All tests pass in both; in the paddle environment the ONNX-only test is skipped.
