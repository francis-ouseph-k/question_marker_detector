# Question Marker Detector (QMD)

QMD finds the handwritten **question markers** (`Ans 5.`, `Q.No 10(b)(1)`, `3)`, …) in the left margin of scanned PDF answer scripts. It resolves each marker against the examination's list of valid questions and estimates the **X/Y point where that question's answer starts**.

It is a command-line subsystem of the Digital Evaluation platform. Three documents, kept in [`docs/specs/`](docs/specs), are the source of truth:

| Document | File |
|---|---|
| Problem Statement v1.1 | `docs/specs/problem_statement.md` |
| Requirements v1.1 | `docs/specs/requirements.md` |
| Technical Design v1.1 | `docs/specs/technical_design.md` |

Code comments cite these documents like this: `TD §12` means Technical Design section 12, and `FR-11` means functional requirement 11. [Traceability](#9-traceability) maps each requirement to the module that implements it.

---

## Contents

1. [Quick start](#1-quick-start)
2. [Setup](#2-setup)
3. [Configuration](#3-configuration)
4. [Usage](#4-usage)
5. [Outputs](#5-outputs)
6. [Architecture](#6-architecture)
7. [Testing](#7-testing)
8. [Troubleshooting](#8-troubleshooting)
9. [Traceability](#9-traceability)
10. [Assumptions and deviations](#10-assumptions-and-deviations)
11. [Known limitations](#11-known-limitations)

---

## 1. Quick start

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements/onnx.txt -r requirements/dev.txt   # ONNX backend: works offline
pip install -e . --no-deps                                        # the qmd package + `qmd` command
cp .env.example .env                                              # optional

qmd run --input ./scans --questions examples/questions/eng221_ese_2023.yaml --backend onnx
```

To use the default backend from the Technical Design (**PaddleOCR PP-OCRv5 Mobile**), install `requirements.txt` instead, in a separate environment:

```bash
pip install -r requirements.txt -r requirements/dev.txt && pip install -e . --no-deps
qmd run --input ./scans --questions examples/questions/eng221_ese_2023.yaml
```

---

## 2. Setup

### Requirements

* Python 3.10–3.12 (developed and tested on 3.11).
* CPU only. No GPU is required (NFR-01).
* Linux, macOS or Windows.

### Install

Pick **one** OCR backend. Each backend brings its own OpenCV package, and two OpenCV packages in one environment break `import cv2`.

**Option A: requirements files (pinned, tested versions)**

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install --upgrade pip

pip install -r requirements.txt        # default: PaddleOCR 3.x + PP-OCRv5 Mobile (= requirements/paddle.txt)
# or
pip install -r requirements/onnx.txt   # ONNX Runtime backend (offline, no model download)

pip install -r requirements/dev.txt    # optional: tests, ruff, mypy
pip install -e . --no-deps             # install the qmd package itself (adds the `qmd` command)
```

| File | Contents |
|---|---|
| `requirements.txt` | Default install; same as `requirements/paddle.txt` |
| `requirements/base.txt` | Core libraries shared by both backends (not installable alone, because it has no OpenCV) |
| `requirements/paddle.txt` | base + `paddlepaddle`, `paddleocr`, `opencv-contrib-python` |
| `requirements/onnx.txt` | base + `rapidocr`, `onnxruntime`, `opencv-python` |
| `requirements/dev.txt` | `pytest`, `pytest-cov`, `pillow` (used by the tests), `ruff`, `mypy` |
| `requirements/lock-onnx-py311.txt` | Exact full environment (`pip freeze`) of the tested ONNX setup |

**Option B: pyproject extras (version ranges)**

```bash
pip install -e ".[paddle]"             # or ".[onnx]"
pip install -e ".[onnx,dev]"           # backend + dev tools
```

For a byte-for-byte reproducible install of the ONNX setup that was tested:

```bash
pip install -r requirements/lock-onnx-py311.txt && pip install -e . --no-deps
```

### OCR models

| Backend | Models | How they arrive |
|---|---|---|
| `paddle` (default, TD §3.1) | `PP-OCRv5_mobile_rec`, plus `PP-OCRv5_mobile_det` if `detection.method=ocr_model` | PaddleOCR downloads them on first use from HuggingFace, BOS or ModelScope. **For production, download them once, store them as a versioned model bundle (TD §28) and set `ocr.rec_model_dir` / `ocr.det_model_dir`.** |
| `onnx` | Any PP-OCR model exported to ONNX (`ocr.onnx_rec_model_path`, `ocr.onnx_det_model_path`, `ocr.onnx_rec_keys_path`) | If you set no paths, the models **bundled in the `rapidocr` wheel** are used. For rapidocr 3.9.2 these are "PP-OCRv6 small", **not** PP-OCRv5 Mobile. A warning is logged, and the model file name plus a SHA-256 of its content is written into every result, so a rapidocr upgrade that ships a different model is always visible. |

The model name and version are recorded with every result (NFR-06), so results produced by different models are never confused.

---

## 3. Configuration

Configuration is layered. Each layer overrides the ones before it:

1. Defaults in `src/qmd/config.py`
2. The YAML file `config/default.yaml`, or `--config FILE`, or `QMD_CONFIG_FILE`
3. Environment variables or `.env`: `QMD_<SECTION>__<KEY>`, for example `QMD_OCR__BACKEND=onnx`
4. Command-line options such as `--backend`, `--workers`, `--output`, `--detector`, `--log-level`

Validation behaves as follows:

* Unknown sections and keys are errors, which catches typos.
* Inconsistent values (for example `line_pitch_min_mm >= line_pitch_max_mm`) are rejected at start-up with exit code 2.
* `qmd show-config` prints the effective configuration.

`config/default.yaml` is found relative to the current folder. Run `qmd` from the project folder, or set `QMD_CONFIG_FILE` or `--config`. If no file is found, a warning is logged and the built-in defaults are used; those have **no** page roles, so page 1 would also be processed.

**Physical sizes are in millimetres.** Page pixel sizes differ between pages, so `*_mm` values are converted using each page's own dpi (Requirements §2.4).

### Key settings

| Section | Key | Default | Meaning |
|---|---|---|---|
| `booklet` | `page_roles` | `{1: COVER, 2: DO_NOT_WRITE}` | Page roles for the booklet template (FR-19). Non-`ANSWER` pages are not decoded or OCR'd (NFR-09). |
| `ocr` | `backend` | `paddle` | `paddle` or `onnx` |
| `ocr` | `rec_model_name` / `rec_model_dir` | `PP-OCRv5_mobile_rec` / none | Recognition model (TD §3.1) |
| `ocr` | `threads_per_worker` | `2` | CPU threads per worker; aim for workers × threads ≈ physical cores (TD §21) |
| `ocr` | `calibration_file` | none | Maps raw scores to calibrated confidence (TD §19.2) |
| `processing` | `workers` | `1` | Parallel page workers (processes) |
| `detection` | `method` | `margin_components` | `margin_components` or `ocr_model`; see [A-4](#10-assumptions-and-deviations) |
| `layout` | `line_snap_tolerance_fraction` | `0.25` | How far (as a fraction of the line spacing) a predicted ruled line may snap to a detected one (TD §6.1) |
| `region` | `spill_mm` | `6.0` | How far right of the margin rule the marker region extends (TD §6.2) |
| `ink` | `enable_colour_separation` | `true` | Remove red and green valuer ink (FR-18) |
| `grammar` | `prefixes` | `ANS, Q, QNO, …` | Accepted marker prefixes (FR-04) |
| `resolution` | `match_threshold`, `margin_threshold`, `confusions` | see file | Constrained matching (TD §12) |
| `gate` | `min_recognition_confidence` … | see file | Review gate (TD §19). **Provisional:** tune them on labelled data. |

### The valid question list

The question list is exam-specific input. **Nothing about a particular exam is hard-coded.** Supply it with `--questions`:

```yaml
# examples/questions/multilevel_example.yaml
exam_id: DEMO-MULTILEVEL
version: "1"
questions:
  - "1"
  - "4(a)"
  - "5(a)(ii)"
  - id: "10(b)"
    target: true          # markers may resolve to this parent
  - "10(b)(1)"
answering_rules:          # optional; used by the script-level checks (FR-17)
  min_answers: 5
  max_answers: 8
```

JSON with the same structure also works, as does a plain `.txt` file with one ID per line. Check a list before using it:

```bash
qmd validate-questions examples/questions/multilevel_example.yaml
```

Validation does the following:

* It rejects duplicates, such as `10(b)(1)` and `10.b.1` in the same list.
* It rejects malformed IDs.
* It marks **confusable** IDs, whose characters are the same once brackets are removed (for example `1(1)` and `11`). These are never auto-accepted (Requirements §2.2).

---

## 4. Usage

```bash
# Process every PDF in a folder (one output folder per PDF)
qmd run -i ./scans -q questions.yaml

# A single PDF, 4 parallel workers, ONNX backend, custom output folder
qmd run -i ./scans/Doc0711.pdf -q questions.yaml --workers 4 --backend onnx -o ./results

# More detail when diagnosing: also print DISCARDED candidates and debug logs
qmd run -i ./scans -q questions.yaml --show-discarded --log-level DEBUG

# Faster runs without diagnostic images
qmd run -i ./scans -q questions.yaml --no-debug-images

python -m qmd --help        # same as `qmd --help`
```

Exit codes:

* **0**: all scripts processed.
* **1**: at least one page failed, or a PDF could not be opened.
* **2**: configuration or input error.

### Example console output

This is real output on the two sample scripts, using the `onnx` backend with the bundled model. The full file is `docs/example_console_output.txt`.

```text
==============================================================================================================
SCRIPT Doc0711   pages=20   exam=ENG221-ESE-2023   question_list=ENG221-ESE-2023:2023-05-05
source=/tmp/scans/Doc0711.pdf   processing_version=1.1.0+1e769e8e83   time=3.2s
ocr=rapidocr-3.9.2/onnxruntime-1.30.0  model=bundled:PP-OCRv6_rec_small.onnx@sha256:6f327246b503
Coordinates: CANONICAL_PAGE_PX (native scan pixels, origin top-left)
--------------------------------------------------------------------------------------------------------------
PAGES
  p001 COVER        SKIPPED_BY_ROLE  TEMPLATE markers=0
  p003 ANSWER       PROCESSED        NORMAL   markers=2  1488x2576@200dpi       rule_x=58 lines=32 skew=-0.15
  p010 ANSWER       PROCESSED        NORMAL   markers=1  1488x2574@200dpi       rule_x=182 lines=31 skew=+0.11
  p013 ANSWER       PROCESSED        BLANK    markers=0  1484x2576@200dpi       rule_x=76 lines=31 skew=+0.01
  ...
--------------------------------------------------------------------------------------------------------------
MARKERS  (page, question, status, raw OCR, recognition/resolution confidence, method, marker box, answer start)
  p003  1          REVIEW_REQUIRED raw='1'     rec=0.80 res=1.00 EXACT_UNIQUE  box=(0,425,32x31) start=(58,384) LANDMARK_LINE c=0.95  [DUPLICATE_IN_SCRIPT]
  p003  2          ACCEPTED        raw='n82'   rec=0.98 res=0.85 EXACT_UNIQUE  box=(0,2132,53x38) start=(58,2094) LANDMARK_LINE c=0.95  flags=PREFIX_RESIDUE,NOISE_STRIPPED
  p005  3          REVIEW_REQUIRED raw='63.'   rec=0.89 res=0.85 EXACT_UNIQUE  box=(0,282,51x34) start=(70,246) LANDMARK_LINE c=0.95  [LOW_RECOGNITION_CONFIDENCE]  flags=NOISE_STRIPPED
  p006  5          REVIEW_REQUIRED raw='Ans 5' rec=0.84 res=1.00 EXACT_UNIQUE  box=(46,415,105x44) start=(190,384) LANDMARK_LINE c=0.95  [OUT_OF_ORDER]
  p007  ?6         UNRESOLVED      raw='186'   rec=1.00 res=0.53 NONE          box=(0,413,52x39) start=(72,385) LANDMARK_LINE c=0.95  [NO_MATCH]
  p010  8          ACCEPTED        raw='Ans8'  rec=0.92 res=1.00 EXACT_UNIQUE  box=(40,1239,104x48) start=(182,1214) LANDMARK_LINE c=0.95
--------------------------------------------------------------------------------------------------------------
SUMMARY  ACCEPTED=2  DISCARDED=3  REVIEW_REQUIRED=5  UNRESOLVED=1
SCRIPT FLAGS: DUPLICATE_QUESTION, OUT_OF_ORDER
  - Question 1 appears 2 times (pages 3, 8)
==============================================================================================================
```

How to read a marker line:

* **`box=(x,y,w×h)`** is the marker's position.
* **`start=(x,y)`** is the answer start, together with the method and its confidence.
* **`[...]`** lists the review reasons.
* **`flags=`** lists the adjustments the parser made. For example, `n82` is `Ans2` with the start of the prefix clipped off, which is why `NOISE_STRIPPED` appears.
* **`?6`** means the marker could not be resolved and `6` is the best suggestion for the reviewer.
* All coordinates are **canonical page pixels**: the scan's own pixels, origin at the top-left, y pointing down (FR-10).

Logs go to **stderr**. See `docs/example_log_excerpt.txt`.

---

## 5. Outputs

Each PDF gets its own folder, `<output_root>/<script_id>/`:

```text
script_result.json          main result: every page, every candidate, script flags
run_manifest.json           versions, OCR engine and model, full effective configuration, timings
processing.log              complete log for this script
pages/page_003.jpg          canonical page image (the scanner's original JPEG bytes; no re-encoding)
debug/page_003_overlay.jpg  page with margin rule, ruled lines, marker boxes (colour = status) and answer starts
debug/page_003_margin.png   the cleaned margin region given to the detector
crops/page_003_c01.png      the exact crop given to the recognizer
```

`script_result.json` and `run_manifest.json` are written atomically, so an interrupted run never leaves half-written JSON. Rerunning the same PDF with the same configuration replaces the results and produces the same `marker_result_id` values (FR-15).

### Marker result

Excerpt (full example: `docs/example_marker_result.json`):

```json
{
  "marker_result_id": "b444d15ea7e2d6f6",
  "page_id": "Doc0711-P010",
  "raw_ocr_text": "Ans8",
  "normalized_marker": "8",
  "canonical_question_id": "8",
  "candidate_list": [{"question_id": "8", "score": 1.0}, "..."],
  "resolution_method": "EXACT_UNIQUE",
  "recognition_confidence": 0.9166,
  "coordinate_system": "CANONICAL_PAGE_PX",
  "page_image_width": 1488, "page_image_height": 2574, "page_image_dpi": 200.0,
  "marker_x": 40, "marker_y": 1239, "marker_width": 104, "marker_height": 48,
  "question_start_x": 182, "question_start_y": 1214,
  "question_start_method": "LANDMARK_LINE", "question_start_confidence": 0.95,
  "status": "ACCEPTED", "review_reasons": [],
  "ocr_engine": "rapidocr-3.9.2/onnxruntime-1.30.0", "ocr_model": "bundled:PP-OCRv6_rec_small.onnx@sha256:6f327246b503",
  "grammar_version": "1.0", "question_list_version": "ENG221-ESE-2023:2023-05-05",
  "debug_crop_path": "crops/page_010_c02.png"
}
```

### Statuses and reasons (Requirements §5.3)

| Status | Meaning |
|---|---|
| `ACCEPTED` | Resolved and passed every check in the review gate and the script-level checks |
| `REVIEW_REQUIRED` | A question was found, but at least one check failed (see `review_reasons`) |
| `UNRESOLVED` | No valid question matches (`NO_MATCH`), or a prefix was read with no number (`PREFIX_WITHOUT_ID`) |
| `DISCARDED` | Automatically judged not to be a marker (`discard_reason`); kept for audit |
| `REJECTED` | Reserved for a human reviewer |

Review reasons:

* `LOW_RECOGNITION_CONFIDENCE`
* `AMBIGUOUS`
* `NO_MATCH`
* `INVALID_POSITION`
* `LANDMARKS_NOT_FOUND`
* `START_UNCERTAIN`
* `DUPLICATE_IN_SCRIPT`
* `OUT_OF_ORDER`
* `ANSWER_COUNT_MISMATCH`
* `PREFIX_WITHOUT_ID`

Page results have a `processing_status` (`PROCESSED`, `SKIPPED_BY_ROLE`, `FAILED_*`) and, separately, a `content_class` (`BLANK`, `SPARSE`, `NORMAL`, `TEMPLATE`), as FR-09 requires.

### Question start (FR-11, TD §14–15)

* **x** is the page's margin rule, which is the left edge of the answer area.
* **y** is the top ruled line of the first writing line, at or below the marker, that contains answer ink. The search stops at the next marker.
* Method `LANDMARK_LINE` is used normally. `FALLBACK_OFFSET` is used when the page landmarks cannot be found, and those markers are always sent to review.

On a page that was deskewed, points are mapped back to the canonical (un-deskewed) image. The start x therefore follows the slightly slanted rule.

---

## 6. Architecture

### Processing flow for one page (TD §34)

```text
PDF ─► pdf_loader        canonical page image (native embedded JPEG; /Rotate applied)
     ─► page role check  COVER / DO_NOT_WRITE pages skipped (not decoded)
     ─► page_layout      margin rule, header rule, ruled lines, skew → optional deskew
     ─► ink              colour-scan check; valuer red/green ink → white
     ─► triage           content class BLANK / SPARSE / NORMAL
     ─► region           landmark-relative margin crop; rule + printed lines masked
     ─► detection        candidate boxes (margin_components | ocr_model)
     ─► ocr              recognizer (paddle | onnx), batched
     ─► pipeline         position check → grammar → resolver → question_start → gate
     ─► artefacts        crops, overlay, page image
Script ─► runner         pages (sequential or process pool) → consolidation → JSON + manifest
```

### Modules (`src/qmd/`)

| Module | Responsibility | TD |
|---|---|---|
| `config.py` | Typed, validated configuration (YAML + env + CLI) | NFR-07 |
| `models.py` | All result and data models (Pydantic) | §13, §18 |
| `pdf_loader.py` | Canonical page image; pypdfium2 (Apache/BSD, not AGPL) | §5.1 |
| `geometry.py` | Affine transforms between canonical, work and region coordinates | §8–9 |
| `page_layout.py` | Margin rule, header, ruled lines, skew | §6.1 |
| `ink.py`, `triage.py` | Valuer-ink removal; content class | §6.3, §17 |
| `region.py` | Landmark-relative marker region | §6.2 |
| `detection/` | `MarginComponentDetector`, `OcrModelDetector` | §7, §3.2 |
| `ocr/` | `Recognizer` / `TextDetectionModel` protocols; `paddle` and `onnx` backends; calibration | §4, §19.2 |
| `grammar.py` | Marker grammar, prefixes, clipped prefixes, variants | §10 |
| `question_list.py` | Load and validate the question list; hierarchy; confusable IDs | §11 |
| `resolver.py` | Constrained, confusion-weighted resolution | §12 |
| `question_start.py` | `LANDMARK_LINE` and `FALLBACK_OFFSET` | §14–15 |
| `gate.py` | Review gate with reason codes | §19 |
| `consolidation.py` | Script-level checks | §23a |
| `pipeline.py` | `PageProcessor`: one page job | §34 |
| `runner.py` | Script orchestration, retries, process pool, outputs | §21–24 |
| `reporting.py`, `cli.py` | Console report and CLI | — |

### Coordinate spaces (TD §9)

| Space | Definition |
|---|---|
| canonical | The page's native scan image. Everything that is saved uses this space. |
| work | The canonical image, possibly deskewed. Linked to canonical by `canonical_to_work`, a 2×3 affine matrix. |
| region | A crop of the work image. Linked to work by a translation. |

`geometry.py` inverts and composes these transforms. Boxes are recomputed from transformed polygons rather than transformed directly (TD §8).

### Adding a database, queue, API or UI later

* **Queue workers.** `PageProcessor.process_page(pdf, script_id, page_index, out_dir)` is already a stateless page job. A queue worker calls it, and a completion tracker then runs `consolidation.consolidate()`. Only `runner.py` needs replacing.
* **Database.** Persist `ScriptResult`, `PageResult` and `MarkerResult`, which are already Pydantic models with stable IDs. Human corrections use `ReviewRecord` and `ResultSource`, and **must never be overwritten by reprocessing** (FR-15). That precedence rule belongs in the persistence layer.
* **API or UI.** Serve the same models. The debug crops and overlays are ready-made evidence for reviewers.

---

## 7. Testing

```bash
pytest                         # 123 unit and component tests; no OCR models needed (about 6 s)
pytest --cov=qmd               # coverage (about 86%)
ruff check src tests && mypy src

# Optional: real OCR on real PDFs (checks invariants, not accuracy)
QMD_SAMPLES_DIR=./samples QMD_OCR__BACKEND=onnx pytest -m ocr
```

The unit tests build **synthetic ruled answer pages and PDFs**, including red valuer marks, skew, rotated PDF pages and grayscale scans. They replace the OCR engine with a scripted fake recognizer, so every component except the OCR library is tested deterministically. The test cases for the grammar and the resolver come from the handwriting seen in the sample scripts.

Accuracy is a different question. Requirements §6 needs a **labelled dataset**, and the wrong auto-accept rate must be measured on it. That dataset does not exist yet; see [Known limitations](#11-known-limitations).

---

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `No model hoster is available` / `Could not create PaddleOCR models` | The PaddleOCR model download is blocked. Download `PP-OCRv5_mobile_rec` (and `_det`) on a connected machine and set `ocr.rec_model_dir` / `ocr.det_model_dir`. Or use `--backend onnx`. |
| `ImportError` / `cv2` crashes | Two OpenCV packages are installed, usually because both backends were installed into one environment. Use one backend per environment. To repair: `pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless`, then reinstall your backend's requirements file. |
| Page flag `LANDMARKS_NOT_FOUND` | The margin rule or ruled lines were not found: an unruled or unknown booklet, very faint printing, or a crop. Check `debug/page_NNN_overlay.jpg`. Tune `layout.*`, for example `margin_rule_min_length_fraction`. These markers use `FALLBACK_OFFSET` and go to review. |
| Page flag `COLOUR_SEPARATION_UNAVAILABLE` | The scan is grayscale, so red valuer ink cannot be removed. The stricter `gate.grayscale_min_confidence` applies. Scan in colour (Problem Statement A2). |
| A marker was missed | Run with `--show-discarded --log-level DEBUG`. Check `debug/page_NNN_margin.png`: is the marker visible, and not whitened as valuer ink? Then check `crops/`: what did the recognizer see? Try `--detector ocr_model` for comparison. |
| Too many `REVIEW_REQUIRED` | Look at `review_reasons`. `LOW_RECOGNITION_CONFIDENCE` usually means the model is weak on this handwriting; a fine-tuned recognizer is the planned fix (TD §3.2). Thresholds are provisional, so tune them on labelled data rather than by feel. |
| Valuer ticks read as `1` | Ticks in dark green or black ink cannot be removed by colour. Wide single-character readings are discarded (A-9), and the remainder are caught by `DUPLICATE_IN_SCRIPT` / `OUT_OF_ORDER`. |
| Slow | Use `--no-debug-images` and more `--workers`. Keep workers × `ocr.threads_per_worker` close to the number of physical cores. |
| Exit code 2 | Read the message: a configuration or question-list validation error. `qmd show-config` and `qmd validate-questions` help. |

---

## 9. Traceability

| Requirement | Where it is implemented |
|---|---|
| FR-01 page processing, 0/1/n markers | `pipeline.PageProcessor` |
| FR-02 landmark-relative region, position check | `page_layout`, `region`, `pipeline._classify_items` |
| FR-03/04 characters, formats, prefixes, clipped prefixes | `grammar.MarkerGrammar` |
| FR-05/06/07 resolution, ambiguity, no invented IDs | `resolver.QuestionResolver`, `question_list` |
| FR-08 multiple markers, start bounded by next marker | `pipeline`, `question_start` |
| FR-09 processing status vs content class | `models.PageResult`, `triage` |
| FR-10 coordinate system | `pdf_loader`, `geometry`, `models.MarkerResult` |
| FR-11 question start | `question_start` |
| FR-12 confidences, calibration | `resolver`, `ocr/calibration` |
| FR-13 review triggers and reason codes | `gate`, `consolidation` |
| FR-14 audit and provenance | `models.MarkerResult`, `run_manifest.json`, crops |
| FR-15 idempotency | stable IDs in `pipeline`; atomic writes in `artefacts` |
| FR-16 retry, failure isolation | `runner.run_page_with_retry` |
| FR-17 script consolidation | `consolidation` |
| FR-18 non-marker content | `ink`, `pipeline` (position, grammar, shape) |
| FR-19 page roles | `config.BookletConfig`, `pipeline` |
| NFR-01/02/05 CPU, scaling, restart | `runner` (process pool), stateless `PageProcessor` |
| NFR-06/08 model versioning, replaceable OCR | `ocr/` protocols and backends, `EngineInfo` |
| NFR-07 configuration | `config`, `config/default.yaml`, `.env` |
| NFR-09 data protection | skipped pages are never decoded; no cover-page artefacts |

---

## 10. Assumptions and deviations

Where the documents left something open, the simplest production-grade choice was made and made configurable:

* **A-1 Left margin only.** The Technical Design and the samples use a left margin on both odd and even pages, so no right-margin processing was added.
* **A-2 Identifiers.** `script_id` is the PDF file name, sanitised. `page_id` is `<script_id>-P<nnn>`. In production, ingestion should supply the script ID (for example from the barcode).
* **A-3 Question list format.** The format is YAML, JSON or TXT, as in §3. Parent IDs are resolution targets only if `target: true` is set.
* **A-4 Default detector is `margin_components` (TD §3.2 option B), not the OCR model's text detector.** On the sample, the text detector missed about 3 of 16 markers and was about 8× slower. TD §7.1 and §30 leave this choice to benchmarking. `detection.method: ocr_model` switches back.
* **A-5 OCR engine.** `paddle` with PP-OCRv5 Mobile is the default, as TD §3.1 says. **It could not be executed during development because model downloads are blocked in the build sandbox.** Its adapter follows the PaddleOCR 3.3 `TextRecognition` / `TextDetection` API and handles errors, but has not been run against the models. All end-to-end results in this README come from the `onnx` backend with the models bundled in rapidocr.
* **A-6 Colour check.** A scan counts as colour if its channels differ, which includes paper tint. It does not have to contain coloured ink. Grayscale scans raise the confidence threshold.
* **A-7 Prefix-only tokens (small deviation from TD §10.1, toward safety).** A `Q.No.`-style token with no number is discarded as a header **only if a real marker follows within 2 lines**. Otherwise it goes to review as `PREFIX_WITHOUT_ID`. On the sample, an overwritten `QNo8` was read as just `No`, and discarding it would have lost a real marker.
* **A-8 Clipped-prefix noise.** A leading digit is treated as prefix residue (`63.` → `3`) only when the candidate touches the page's left edge (≤ 0.5 mm), and at most one digit (`grammar.max_noise_digits`). Such readings need `gate.adjusted_parse_min_confidence` (0.92).
* **A-9 Implausible shape.** A single character read from a box wider than 1.3 × its height is discarded as `IMPLAUSIBLE_SHAPE`. These are typically valuer ticks in dark ink, which colour separation cannot remove.
* **A-10 Page roles.** Roles are configured per booklet template (`config/default.yaml` holds the sample booklet: page 1 cover, page 2 do-not-write). Pages with non-answer roles are neither decoded nor saved (NFR-09). As a result, step 1, "extract the pages as images", covers answer pages only.
* **A-11 Recognizer interface.** It is batched, `recognize(list_of_crops)`, instead of TD §4's single-region call; the crop is already the region. The optional CTC-probability path (TD §12.3) is not implemented because neither backend exposes character probabilities. The fields are reserved in `TextResult`.
* **A-12 Thresholds and calibration.** All thresholds are provisional. No calibration file is shipped, because there is no labelled data yet.
* **A-13 Detection confidence** is 1.0 for the deterministic component detector, and the detector's own score for `ocr_model`.
* **A-14 Human review workflow, database, queue, API and UI** are out of scope for this phase, as instructed. The models (`ReviewRecord`, `ResultSource`) are in place.
* **A-15 OpenCV.** OpenCV comes from the chosen backend (`opencv-contrib-python` for paddle, pulled in by paddlex; `opencv-python` for onnx, required by rapidocr), so exactly one `cv2` is installed. `PyYAML` is a version range in `base.txt` because paddlex pins its own exact PyYAML version.
* **A-16 English (ASCII) question markers only.** This was confirmed for now. Only ASCII digits `0-9` and letters `a-z`/`A-Z` can form a question ID; in Python `\d` would also match Devanagari or Kannada digits, so the patterns spell out `[0-9]`. Other characters count as noise: full-width forms are first converted to ASCII by NFKC, and CJK punctuation from the recognizer (e.g. `5。`) is ignored. A prefix followed only by non-English digits (`Ans १२`) goes to review as `PREFIX_WITHOUT_ID`. The question list itself must also use ASCII IDs. To support other scripts later, add a digit conversion step in `grammar.py` before tokenising.
* **A-17 Marker polygon.** With `detection.method: ocr_model`, the text detector's own outline, which may be rotated, is reported in `marker_polygon` (TD §8). When fragments are merged, or with the component detector, the polygon is the box's four corners mapped to canonical coordinates, so on deskewed pages it is slightly rotated. `marker_x/y/width/height` is always the axis-aligned box around that polygon.

---

## 11. Known limitations

On the two sample scripts, using the bundled ONNX model and not PP-OCRv5:

* Of the 14 real markers, 11 were resolved to the correct question: 4 were auto-accepted and 7 went to review. The other 3 came out `UNRESOLVED`: `186` for a clipped `Ans 6`, `N00)` for `QNo6)`, and `PREFIX_WITHOUT_ID` for an overwritten `QNo8`. **No marker was auto-accepted with a wrong ID.**
* Valuer ticks in dark ink can still produce a false `1`. These are caught by the script-level duplicate and order checks, which also send the real Q1 to review.
* The generic recognizer is the weak link for cursive, clipped and overwritten markers. The planned improvement (TD §3.2 / §30) is a recognizer fine-tuned on marker crops, with a restricted character set. The `crops/` folders produced by this tool are the starting point for that labelled dataset.
* Accuracy targets (Requirements §6) cannot be claimed until a labelled benchmark exists.
