# Question Marker Detector (QMD): Operations Guide

This guide covers everything needed to install, test and run QMD, with commands you can copy and paste. For design background see `README.md`; for open work see `pending.md`.

---

## 1. What you need

| Item | Requirement |
|---|---|
| Python | 3.10, 3.11 or 3.12 (tested on **3.11**) |
| OS | Linux, macOS or Windows |
| Hardware | CPU only; no GPU needed. At least 4 GB RAM for one worker; 16–32 GB for a production server |
| Disk | About 1.5 GB for the PaddleOCR environment, about 300 MB for the ONNX environment, plus output space (roughly 5–8 MB per script with debug images, under 1 MB without) |
| Network | Needed once for `pip install`. The **PaddleOCR** backend also downloads its models on first run, unless you give it local model folders (§4) |

Check your Python version:

```bash
python3 --version          # Windows: py --version
```

---

## 2. Choose an OCR backend

QMD runs with one of two OCR backends. **Install each backend in its own virtual environment**, never both in one: each brings a different OpenCV package, and two OpenCV packages break `import cv2`.

| Backend | When to use | Requirements file |
|---|---|---|
| `paddle`: PaddleOCR 3.x with **PP-OCRv5 Mobile** | The default in the Technical Design; use it for production and evaluation | `requirements.txt` (same as `requirements/paddle.txt`) |
| `onnx`: RapidOCR on ONNX Runtime | Offline or quick trials. Uses the models bundled with rapidocr (PP-OCRv6 small), **not** PP-OCRv5 | `requirements/onnx.txt` |

---

## 3. Install

Unzip the project and open a terminal in the project folder, `question-marker-detector/`. **Always run `qmd` from this folder**, because the configuration file `config/default.yaml` is found relative to it.

### 3.1 PaddleOCR backend (default)

Linux or macOS:

```bash
python3.11 -m venv .venv-paddle
source .venv-paddle/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements/dev.txt
pip install -e . --no-deps
```

Windows (PowerShell):

```powershell
py -3.11 -m venv .venv-paddle
.venv-paddle\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements\dev.txt
pip install -e . --no-deps
```

### 3.2 ONNX backend (alternative)

Linux or macOS:

```bash
python3.11 -m venv .venv-onnx
source .venv-onnx/bin/activate
pip install --upgrade pip
pip install -r requirements/onnx.txt -r requirements/dev.txt
pip install -e . --no-deps
```

On Windows, use the same commands with `py -3.11` and `.venv-onnx\Scripts\Activate.ps1`, as in §3.1.

For an exact copy of the tested ONNX environment, replace the second `pip install` line with `pip install -r requirements/lock-onnx-py311.txt`.

### 3.3 What gets installed

| File | Contents |
|---|---|
| `requirements/base.txt` | numpy, pypdfium2 (PDF reading), pydantic and pydantic-settings (configuration), python-dotenv, PyYAML |
| `requirements/paddle.txt` | base + paddlepaddle 3.2.2, paddleocr 3.3.3, opencv-contrib-python |
| `requirements/onnx.txt` | base + rapidocr 3.9.2, onnxruntime, opencv-python |
| `requirements/dev.txt` | pytest, pytest-cov, pillow (used by the tests), ruff, mypy |
| `pip install -e . --no-deps` | The `qmd` package itself and the `qmd` command |

### 3.4 Check the installation

```bash
qmd --version                                    # -> qmd 1.0.0
qmd show-config | head -20                       # effective configuration
python -c "import cv2; print(cv2.__version__)"  # must print one version, no error
pip list | grep -i opencv                        # must list exactly ONE opencv package
```

On Windows, use `pip list | findstr /i opencv` instead of `grep`.

---

## 4. OCR models (PaddleOCR backend only)

The first `qmd run` with the paddle backend downloads `PP-OCRv5_mobile_rec` from HuggingFace, BOS or ModelScope. Where it is stored, and the right folder to use in step 1 below, is printed in the log. By default PaddleOCR 3.x uses `~/.paddlex/official_models/`.

**Offline or controlled servers (recommended for production):**

1. On a machine with internet access, run QMD once (or PaddleOCR directly) so the model is downloaded.
2. Copy the model folder, for example `~/.paddlex/official_models/PP-OCRv5_mobile_rec`, to the server, e.g. `/opt/qmd/models/PP-OCRv5_mobile_rec`.
3. Point QMD at it in `.env`:

   ```bash
   QMD_OCR__REC_MODEL_DIR=/opt/qmd/models/PP-OCRv5_mobile_rec
   DISABLE_MODEL_SOURCE_CHECK=True
   ```

4. Only if you use `--detector ocr_model`, repeat steps 2–3 for `PP-OCRv5_mobile_det` with `QMD_OCR__DET_MODEL_DIR`.

If QMD exits with `Could not create PaddleOCR models … download is blocked`, this step is missing.

The ONNX backend needs no download; its models are inside the rapidocr package.

---

## 5. Configure

| What | Where |
|---|---|
| Everything, with comments | `config/default.yaml` |
| Machine- or environment-specific overrides | `.env` (copy it from `.env.example`) |
| One-off overrides | Command-line options |

Precedence: command line > environment variables / `.env` > YAML file > built-in defaults.

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Settings you are most likely to change:

| Setting | `.env` / environment variable | Why |
|---|---|---|
| OCR backend | `QMD_OCR__BACKEND=paddle` or `onnx` | Must match the environment you installed |
| Output folder | `QMD_APP__OUTPUT_ROOT=./output` | Where results go |
| Parallel workers | `QMD_PROCESSING__WORKERS=4` | Speed; see §9 |
| Threads per worker | `QMD_OCR__THREADS_PER_WORKER=2` | Keep workers × threads ≈ number of physical cores |
| Log level | `QMD_APP__LOG_LEVEL=INFO` (or `DEBUG`) | Diagnosis |
| Booklet page roles | `booklet.page_roles` in the YAML | Which pages are cover or instruction pages. The default is page 1 COVER and page 2 DO_NOT_WRITE (the Christ UG main answer booklet) |

To use a different YAML file, for example one per booklet type:

```bash
qmd --config config/my_booklet.yaml run ...
# or set  QMD_CONFIG_FILE=config/my_booklet.yaml
```

Configuration mistakes (unknown keys, bad values) stop start-up with exit code 2 and a clear message.

---

## 6. Prepare the inputs

QMD needs two inputs per examination:

1. **The valid question list for the exam**, taken from the question paper.
2. **A folder of scanned answer-script PDFs** for that exam, one PDF per student script.

### 6.1 Question list file

A YAML file (recommended), JSON or plain text. IDs are written as `NUMBER(part)(subpart)`, using **English digits and letters only**.

```yaml
# exams/ENG221-ESE-2023/questions.yaml
exam_id: ENG221-ESE-2023
version: "2023-05-05"        # change this whenever the list changes
questions:
  - "1"
  - "2"
  - "4(a)"
  - "4(b)"
  - "5(a)(i)"
  - "5(a)(ii)"
  - id: "10(b)"
    target: true             # only if students may label the whole part "10(b)"
  - "10(b)(1)"
  - "10(b)(2)"
answering_rules:             # optional
  min_answers: 5
  max_answers: 8
```

Plain-text alternative, one ID per line:

```text
1
2
4(a)
4(b)
```

**Always validate a question list before a run:**

```bash
qmd validate-questions exams/ENG221-ESE-2023/questions.yaml
```

This prints every ID, whether it can be matched, and any **confusable** IDs (for example `1(1)` and `11`). Duplicates or malformed IDs cause an error with exit code 2.

Ready-made examples are in `examples/questions/`.

### 6.2 Suggested folder layout for several exams

```text
exams/
├── ENG221-ESE-2023/
│   ├── questions.yaml
│   └── scripts/
│       ├── 2212725.pdf
│       └── 2212707.pdf
└── MAT110-ESE-2023/
    ├── questions.yaml
    └── scripts/
        └── ...
```

The PDF file name (without `.pdf`) becomes the **script ID** and the name of its output folder, so use unique names, such as the registration or barcode number.

---

## 7. Run the system

### 7.1 One exam

```bash
qmd run --input exams/ENG221-ESE-2023/scripts \
        --questions exams/ENG221-ESE-2023/questions.yaml \
        --output output/ENG221-ESE-2023
```

With the ONNX backend, add `--backend onnx`, or set `QMD_OCR__BACKEND=onnx` in `.env`.

Useful options:

| Option | Effect |
|---|---|
| `--input FILE.pdf` | Process a single PDF instead of a folder |
| `--workers 4` | Process pages in 4 parallel processes |
| `--backend onnx` | Use the ONNX backend |
| `--detector ocr_model` | Use the OCR model's own text detector instead of margin blob grouping (for comparison) |
| `--no-debug-images` | Skip debug images and crops (faster, much less disk) |
| `--show-discarded` | Also print candidates that were automatically discarded |
| `--log-level DEBUG` | Detailed logs |

### 7.2 Many exams in one go

Linux or macOS (bash):

```bash
for exam in exams/*/; do
  id=$(basename "$exam")
  echo "=== $id ==="
  qmd validate-questions "$exam/questions.yaml" > /dev/null || { echo "Invalid question list for $id"; continue; }
  qmd run -i "$exam/scripts" -q "$exam/questions.yaml" -o "output/$id" --workers 4 \
      > "output/$id.console.txt" 2> "output/$id.log"
  echo "exit code: $?"
done
```

Windows (PowerShell):

```powershell
Get-ChildItem exams -Directory | ForEach-Object {
  $id = $_.Name
  Write-Host "=== $id ==="
  qmd validate-questions "$($_.FullName)\questions.yaml" | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Host "Invalid question list for $id"; return }
  New-Item -ItemType Directory -Force "output" | Out-Null
  qmd run -i "$($_.FullName)\scripts" -q "$($_.FullName)\questions.yaml" -o "output\$id" --workers 4 `
      > "output\$id.console.txt" 2> "output\$id.log"
  Write-Host "exit code: $LASTEXITCODE"
}
```

### 7.3 Exit codes

| Code | Meaning | Action |
|---|---|---|
| 0 | All scripts processed | Review the results |
| 1 | At least one page failed, or a PDF could not be opened | Look for `INCOMPLETE` in the console output and at `error_reason` in `script_result.json` |
| 2 | Configuration, question-list or input error | Read the message, fix it, rerun |

Rerunning is safe. It replaces the previous results for the same PDF and configuration, and produces the same result IDs.

---

## 8. Read the results

### 8.1 Console output (stdout)

Each script prints three sections:

* **Pages:** role, status, content class (BLANK / SPARSE / NORMAL), and the landmarks found.
* **Markers:** each marker's question, status, raw OCR text, confidences, marker box and answer start.
* **Summary and script flags:** status counts and any script-level warnings.

```text
  p010  8   ACCEPTED   raw='Ans8'  rec=0.92 res=1.00 EXACT_UNIQUE  box=(40,1239,104x48) start=(182,1214) LANDMARK_LINE c=0.95
```

How to read a marker line:

* **`box=(x,y,w×h)`:** where the handwritten marker is.
* **`start=(x,y)`:** where the answer begins.
* All coordinates are pixels of the original scan, origin top-left.
* **`?6`** in the question column means the marker was not resolved and `6` is the best suggestion.
* **`[...]`** lists the review reasons.

Logs go to stderr, and are also saved as `processing.log` per script.

### 8.2 Files per script: `<output>/<script_id>/`

| File | Use |
|---|---|
| `script_result.json` | **The result.** Every page and every marker, with coordinates, confidences, status, review reasons and provenance |
| `run_manifest.json` | Versions, OCR model (with file hash), full effective configuration, timings |
| `processing.log` | Full log for this script |
| `pages/page_NNN.jpg` | The page image exactly as scanned (answer pages only) |
| `debug/page_NNN_overlay.jpg` | The page with margin rule, ruled lines, marker boxes (green = accepted, orange = review, red = unresolved) and answer-start circles |
| `debug/page_NNN_margin.png` | The cleaned margin strip the detector saw |
| `crops/page_NNN_cMM.png` | The exact image each marker reading came from |

### 8.3 What needs a human

| Status | Meaning |
|---|---|
| `ACCEPTED` | Safe to use automatically |
| `REVIEW_REQUIRED` | A question was found, but a check failed (see `review_reasons`) |
| `UNRESOLVED` | No valid question matched, or only a prefix like `QNo` was readable |
| `DISCARDED` | Judged not to be a marker (valuer mark, header, stray stroke); kept for audit only |

Also check the **script flags**:

* `DUPLICATE_QUESTION`, `OUT_OF_ORDER`, `ANSWER_COUNT_MISMATCH`: the markers involved are already set to review.
* `POSSIBLE_MISSED_MARKER`, `NO_MARKERS`: open the overlays and look for a marker the system did not see.
* `INCOMPLETE`: a page failed to process.

Quick counts across one exam's results, using Python:

```bash
python - <<'EOF'
import json, glob, collections
c = collections.Counter()
for f in glob.glob("output/ENG221-ESE-2023/*/script_result.json"):
    for p in json.load(open(f))["pages"]:
        for m in p["markers"]:
            c[m["status"]] += 1
print(dict(c))
EOF
```

---

## 9. Performance and sizing

* Measured in development: about **0.15–0.2 s per page** with 1 worker on 2 CPU cores, using the ONNX backend and writing debug images. Measure again on your own server with the PaddleOCR backend.
* Set `workers × threads_per_worker ≈ physical cores`. For example, on 16 cores: `--workers 8` with `QMD_OCR__THREADS_PER_WORKER=2`.
* `--no-debug-images` saves time and a lot of disk space in bulk runs. Leave debug images on while you are still evaluating accuracy.
* Size servers from **peak** scanning load, not yearly volume (Requirements NFR-03):

  ```text
  servers = ceil( peak pages per minute ÷ measured pages per minute per server × 1.3 )
  ```

---

## 10. Tests

All test commands run from the project folder, with an environment activated.

### 10.1 Unit and component tests (no OCR models needed)

```bash
pytest                              # all tests, about 6 s
pytest -v                           # one line per test
pytest tests/test_grammar.py        # a single file
pytest -k resolver                  # tests whose name contains "resolver"
pytest -x                           # stop at the first failure
```

Expected results:

* ONNX environment: `123 passed, 1 skipped`.
* Paddle environment: `122 passed, 2 skipped`; the ONNX-only test is skipped.
* The skipped `test_real_scripts` is the optional real-OCR test in §10.3.

These tests build synthetic answer pages and PDFs, including red valuer marks, skew, rotated pages and grayscale scans, and use a fake OCR engine. They check every component except the OCR library itself.

| Test file | What it covers |
|---|---|
| `test_grammar.py` | Marker text parsing: prefixes, clipped prefixes, non-English digits |
| `test_resolver.py` | Matching OCR text to valid question IDs |
| `test_question_list.py` | Question-list loading and validation |
| `test_page_layout.py` | Margin rule, ruled lines, deskew |
| `test_ink_and_triage.py` | Valuer-ink removal, blank page detection |
| `test_question_start.py` | Answer start coordinates |
| `test_detection.py` | Candidate detectors, calibration, ONNX model hashing |
| `test_gate_and_consolidation.py` | Review gate, script-level checks |
| `test_geometry.py` | Coordinate transforms |
| `test_config_and_pdf.py` | Configuration, PDF page extraction |
| `test_pipeline.py` | End-to-end runs on synthetic PDFs, retries, idempotency, the CLI |

### 10.2 Coverage

```bash
pytest --cov=qmd --cov-report=term-missing      # about 86% line coverage
pytest --cov=qmd --cov-report=html              # then open htmlcov/index.html
```

### 10.3 Integration test with real OCR on real scans

This checks invariants on real PDFs: every page gets a result, coordinates lie inside the page, IDs come only from the question list, and answer starts sit on the margin rule. **It does not measure accuracy**; that needs a labelled dataset (see `pending.md`).

```bash
QMD_SAMPLES_DIR=/path/to/sample/pdfs \
QMD_SAMPLES_QUESTIONS=examples/questions/eng221_ese_2023.yaml \
pytest -m ocr
```

Windows (PowerShell):

```powershell
$env:QMD_SAMPLES_DIR="C:\scans\samples"; $env:QMD_SAMPLES_QUESTIONS="examples\questions\eng221_ese_2023.yaml"; pytest -m ocr
```

It uses whichever backend `QMD_OCR__BACKEND` selects.

### 10.4 Code quality checks

```bash
ruff check src tests        # style and common bugs
mypy src                    # type checking
```

Both should report no issues.

---

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `Configuration error: ...` (exit 2) | Read the message; usually a misspelt key in the YAML or `.env`. `qmd show-config` shows what was loaded |
| `No configuration file found ... using built-in defaults` | You are not in the project folder. `cd` into it or set `QMD_CONFIG_FILE` |
| `Could not create PaddleOCR models` | The model download is blocked; follow §4, or use `--backend onnx` |
| `PaddleOCR is not installed` / `rapidocr ... not installed` | The backend in the config does not match the environment you activated |
| `import cv2` error or crash | Two OpenCV packages are installed. `pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless`, then reinstall your backend's requirements file |
| Page flag `LANDMARKS_NOT_FOUND` | Margin rule or ruled lines not found: an unruled or unknown booklet, or faint printing. Check `debug/page_NNN_overlay.jpg`; those markers go to review |
| Page flag `COLOUR_SEPARATION_UNAVAILABLE` | Grayscale scan: red valuer marks cannot be removed and stricter thresholds apply. Scan in colour |
| A marker was missed | Rerun that PDF with `--show-discarded --log-level DEBUG`. Then check `debug/page_NNN_margin.png` (is the marker visible?) and `crops/` (what did OCR read?) |
| Too many `REVIEW_REQUIRED` | Look at the most common `review_reasons`. Thresholds are provisional; tune them only on labelled data (see `pending.md`) |
| Slow | Add `--workers`, add `--no-debug-images`, and check workers × threads ≈ cores |

---

## 12. Quick reference

```bash
source .venv-paddle/bin/activate                    # or .venv-onnx
qmd validate-questions exams/X/questions.yaml       # 1. check the question list
qmd run -i exams/X/scripts -q exams/X/questions.yaml -o output/X --workers 4   # 2. run
pytest                                              # tests
qmd show-config                                     # what configuration is active
qmd --help ; qmd run --help                         # all options
```
