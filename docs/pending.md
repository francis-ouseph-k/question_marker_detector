# Question Marker Detector (QMD): Pending Items

Status as of v1.0.2. The items are grouped by what they block, and each has an owner type and a way to tell it is done.

Owner types:

* **Dev:** engineering.
* **Ops:** exam operations or the scanning team.
* **Decision:** product owner or architect.

---

## A. Blockers before production use

| # | Item | Owner | Done when |
|---|---|---|---|
| A1 | **Run the PaddleOCR PP-OCRv5 Mobile backend against real models.** The adapter (`src/qmd/ocr/paddle_backend.py`) follows the PaddleOCR 3.3 API and its error handling was tested, but it has **never run end to end**, because model downloads were blocked in the build environment. | Dev | `qmd run` with the paddle backend completes on the two sample scripts and results appear in `script_result.json` |
| A2 | **Compare PP-OCRv5 with the ONNX baseline on the samples.** Record per-marker results side by side. | Dev | A short comparison note |
| A3 | **Build a labelled benchmark dataset** (Requirements §6; TD §29): 300–500 answer pages from at least 3 exams, labelled with marker box, correct question ID and answer-start line. It must include the hard cases: clipped prefixes, overwritten markers, valuer marks next to markers, numbered points inside answers, cursive, and grayscale scans. | Ops + Dev | Labelled dataset and ground-truth file in an agreed format |
| A4 | **Write a benchmark script** that compares `script_result.json` with the labels and reports the Requirements §6 metrics: marker recall, wrong auto-accept rate, review rate by reason, answer-start accuracy, and time per page. | Dev | A benchmark script (e.g. `scripts/benchmark.py`) and a report |
| A5 | **Tune thresholds and calibrate confidence** (TD §19). All gate and resolution thresholds are provisional. Fit a calibration file and choose thresholds for the target wrong auto-accept rate (≤ 0.2%, provisional). | Dev | A calibration JSON committed; thresholds set from measurements |
| A6 | **Agree the acceptance targets** in Requirements §6: wrong auto-accept ≤ 0.2%, marker recall ≥ 98%, review rate ≤ 15%, answer start ≥ 97%. All are provisional. | Decision | Targets signed off |
| A7 | **Benchmark performance on the target server** (NFR-03/04). Run the workers × threads matrix (TD §21) with the paddle backend and size the servers from peak scanning load. | Dev + Ops | Measured pages per minute per server; server count agreed |

---

## B. Open decisions (from the specs)

| # | Question | Current default | Owner |
|---|---|---|---|
| B1 | Are scripts scanned **before or after** manual evaluation? This decides whether valuer ink handling is needed. (Problem Statement A1; TD D1) | Assume valuer ink may be present | Decision / Ops |
| B2 | Is **colour scanning** mandatory? (A2 / D2) | Yes. Grayscale pages are flagged and use stricter thresholds | Ops |
| B3 | May markers resolve to **parent questions** such as `10(b)`? (D3) | Per exam, via `target: true`; leaves only by default | Decision |
| B4 | **Relative sub-markers**, e.g. `(ii)` written alone under `10(b)`. (D4) | Out of scope; sent to review | Decision |
| B5 | May **marker crops be kept** for training and audit? (D6) | Yes, subject to the retention policy | Decision (data protection) |
| B6 | **Who consumes the answer-start coordinate**, and what precision do they need? (A6) | The evaluator viewer; one ruled line | Evaluation platform |
| B7 | **Other booklet types:** page roles and layout for each booklet in use. (A3) | Only the Christ UG main answer booklet is configured | Ops |
| B8 | **Additional booklets:** how ingestion orders them and links them to the main script. (A4) | Ingestion supplies one ordered PDF per script | Ops / Ingestion |
| B9 | **Script ID source:** the barcode or ingestion system instead of the PDF file name (README A-2) | PDF file name | Ingestion |

---

## C. Accuracy improvements

| # | Item | Why | Owner |
|---|---|---|---|
| C1 | **Fine-tune the recognizer** on marker crops with a restricted character set, `0-9 a-z A-Z ( ) . - :` (TD §3.2, §30 option C). | The generic recognizer is the weakest link: on the samples it misread cursive, clipped and overwritten markers. The `crops/` folders are the starting data. | Dev |
| C2 | **Choose the detector by benchmark**: `margin_components` (current default, README A-4) vs `ocr_model`. | The default was chosen from 2 sample scripts only | Dev |
| C3 | **Dark-ink valuer ticks** can still read as `1`. They are caught by the duplicate and order checks, but those also send the real Q1 to review. Consider a stroke-shape classifier. | Review noise | Dev |
| C4 | **Clipped prefixes** such as `186` for `Ans 6` end up UNRESOLVED. Revisit `grammar.max_noise_digits` once there is labelled data. | Missed automatic resolutions | Dev |
| C5 | **Probability-based scoring** of question IDs from the recognizer's character probabilities (TD §12.3). The interface fields are reserved. | Separates confusable IDs such as `1(1)` and `11`; gives better margins | Dev (needs backend support) |
| C6 | **Detect unmarked answer starts** beyond the first page. Currently `POSSIBLE_MISSED_MARKER` only checks the first written page. | Catches forgotten markers | Dev |

---

## D. Platform integration (out of scope for this phase)

| # | Item | Notes |
|---|---|---|
| D1 | **Database persistence** of script, page and marker results | The models already have stable IDs (FR-15) |
| D2 | **Human review workflow and UI:** confirm, correct, reject, add marker (FR-13; TD §26) | `ReviewRecord` and `ResultSource` are defined but not used |
| D3 | **Human corrections must win over reprocessing** (FR-15). Enforce this rule in the persistence layer. | Needs D1 |
| D4 | **Queue-based workers** for scanner stations (TD §2, §22–23) | Replace `runner.py` only; `PageProcessor` is already a stateless page job |
| D5 | **API** for the evaluation platform | Serve the existing JSON models |
| D6 | **Security hardening:** input path validation, access control, and retention and legal hold for scans, crops and results (TD §32) | Needed once ingestion or an API exists |

---

## E. Engineering housekeeping

| # | Item |
|---|---|
| E1 | Add a CI pipeline to run `pytest`, `ruff` and `mypy` on every change. |
| E2 | Generate a full lock file for the **paddle** environment (only the ONNX lock exists) once A1 is verified. |
| E3 | Add a mocked unit test for the paddle adapter (`paddle_backend.py` currently has no automated test). |
| E4 | Package the PP-OCRv5 model folders as a versioned model bundle (TD §28) and document where it is stored. |
| E5 | If non-English question numbers are ever needed, add a digit conversion step in `grammar.py` (README A-16). |

---

## Done in this phase (for reference)

* Specs v1.1: problem statement, requirements and technical design.
* The full CLI subsystem with PaddleOCR and ONNX backends, configuration, logging, 123 tests, README and operations guide.
* External review triaged, and the agreed fixes applied (v1.0.1).
* Requirements files and the OpenCV conflict fix (v1.0.2).
