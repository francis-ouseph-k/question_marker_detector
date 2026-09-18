# Technical Design

## Handwritten Question Marker Detection and Answer Start Identification

**Version:** 1.1 (revised after review against sample scripts Doc0697 and Doc0711)

> **Revision notes (1.0 → 1.1)**
>
> | Change | Sections |
> |---|---|
> | New page-geometry stage (margin rule, ruled lines, page parity) and landmark-relative marker region | 5, 6 |
> | Ink-colour separation for valuer marks | 6.3 |
> | PDF ingestion and canonical image; transforms stored as affine matrices | 5, 9 |
> | Marker grammar, clipped prefixes, extracting a marker from a merged line | 7.3, 10 |
> | Segment-aware normalization; collision check on load; parent IDs | 10, 11 |
> | Position-typed, confusion-weighted matching; optional CTC scoring; score-gap rule | 12 |
> | Status model and reason codes aligned with Requirements 5.3 | 13 |
> | Phase 1 uses one question-start rule (`LANDMARK_LINE`); the hybrid approach is deferred | 14, 15 |
> | Blank classification is mandatory; page roles | 17, 18 |
> | Provisional confidence gate and calibration | 19 |
> | Thread pinning and decode cost | 21, 31 |
> | Script-level consolidation job | 23a |
> | Reviewer add/reject; human corrections take precedence over reprocessing | 26 |
> | OCR engine justification corrected (fine-tuned recognizer; classical candidate generator) | 3, 30 |

---

### 1. Technical Objective

This component:

1. detects handwritten question markers in the left margin of scanned answer-script pages;
2. recognizes their content;
3. rejects margin content that is not a marker;
4. resolves markers against the examination's configured question list;
5. determines the corresponding answer-start coordinates;
6. checks consistency across the whole script.

The proposed initial OCR engine is **PP-OCRv5 Mobile**, behind an OCR abstraction. It is a **baseline** to be benchmarked, not a final choice (§3, §30).

---

# 2. High-Level Architecture

```text
Scanner Stations
      |
      v
Scan Ingestion  (script_id, ordered pages, booklet template)
      |
      v
Unchanged Scan Store (PDF)
      |
      v
Page Job Queue
      |
      +-----------------------------+
      v                             v
Page Worker 1        ...        Page Worker N
      |
      |  1. Canonical page image (PDF → native scan)
      |  2. Page role check (skip cover / template pages)
      |  3. Page geometry (margin rule, ruled lines, parity)
      |  4. Content class (BLANK / SPARSE / NORMAL)
      |  5. Marker region crop (landmark-relative) + ink-colour separation
      |  6. Candidate detection + recognition (OcrEngine)
      |  7. Grammar parse + normalization
      |  8. Constrained resolution against the question list
      |  9. Question start (LANDMARK_LINE)
      | 10. Page-level gate → page result (atomic write)
      v
Script Completion Tracker ── all pages done ──► Script Consolidation Job
                                                   |
                                                   v
                                         Script-level checks
                                         (duplicates, order, counts)
                                                   |
                                    +--------------+--------------+
                                    v                             v
                                Accepted                    Human Review
                                    \                             /
                                     v                           v
                                       Evaluation Metadata
```

The page workers are stateless and scale horizontally. Script consolidation is a small, separate job that runs once per script.

---

# 3. OCR Engine Selection

## 3.1 Initial Engine

The baseline implementation uses **PP-OCRv5 Mobile** (detection + recognition).

Reasons it is a reasonable **baseline**:

* it runs on CPU and is comparatively light;
* it provides both detection and a CTC sequence recognizer, so no character segmentation is needed;
* it supports Latin characters and is available off the shelf;
* it can be **fine-tuned** on our own marker crops using its standard training tooling.

Known limitations for this use case, which the design mitigates:

| Limitation | Mitigation |
|---|---|
| General-purpose vocabulary: can output any string | Grammar + constrained resolution (§10–12) |
| Confidence (mean character probability) is not calibrated | Calibration on labelled data (§19) |
| Handwriting accuracy on short, isolated, sometimes cursive tokens is unproven | Benchmark; fine-tuned recognizer (§30) |
| Detector may miss small isolated tokens, or merge a marker with nearby answer text | Landmark-relative crop, masking the margin rule, extracting the marker from the line (§6–7) |
| Resize policy on a tall, narrow margin strip (about 1:13) may shrink markers | Tile the strip into overlapping windows, or set resize limits so that the marker's x-height stays ≥ about 20 px (§7.1) |

## 3.2 Alternatives

The main alternative is **not** a character-by-character CNN (which would need segmentation). The realistic alternatives are:

1. **A fine-tuned PP-OCR recognizer** with a restricted character set (`0-9 a-z A-Z ( ) . - :` and space), trained on a few thousand labelled marker crops from real scripts. This is expected to give the largest accuracy gain.
2. **A classical candidate generator.** Group connected ink components inside the landmark-relative margin band into candidate boxes, then run recognition only. This is cheap and deterministic, and works well for these ruled booklets.
3. PP-OCRv5 Server, as a heavier comparison point.

All options share the `OcrEngine` / `CandidateDetector` interfaces (§4) and are compared on the same dataset (§30).

---

# 4. OCR Abstraction

The question-marker logic shall not depend directly on a specific OCR library.

```text
interface CandidateDetector {
    detect(image) -> List<TextRegion>
}

interface Recognizer {
    recognize(image, region) -> TextResult
}

TextRegion
    polygon                 (in the coordinates of the image passed in)
    detection_confidence

TextResult
    text
    recognition_confidence
    top_k: List<(text, score)>            optional
    char_probabilities: Matrix[T x C]     optional (CTC output)
    charset_id                            required if char_probabilities is present
```

The optional fields let the resolver score configured question IDs directly against the recognizer output (§12.3). Engines that do not provide them still work, using the text-based path.

Implementations:

```text
PpOcrV5MobileDetector / PpOcrV5MobileRecognizer
PpOcrV5ServerDetector / PpOcrV5ServerRecognizer
MarginComponentDetector        (classical, §3.2)
FineTunedMarkerRecognizer      (§3.2)
```

---

# 5. Page Image Processing

## 5.1 Canonical Page Image

* Scripts arrive as PDFs. Each page is expected to contain one embedded full-page JPEG (200 dpi colour in the sample).
* The **canonical page image** is that embedded image, decoded at native resolution without resampling. If a page does not contain exactly one full-page image, it is rendered at `canonical_dpi` (configured, default 200) and the fact is recorded.
* PDF `/Rotate` and image orientation are applied when creating the canonical image, so the canonical image is always upright as scanned.
* Width, height and dpi are recorded for every page. They vary from page to page.

The source PDF is never modified.

## 5.2 Derived Images

Derived images may be created for rotation correction, scaling, contrast normalization, denoising, ink-colour separation, cropping and OCR.

Every derived image carries a **2×3 affine transform** `T_derived←canonical`. When stages are chained, their transforms are multiplied together. Coordinates are mapped back with the inverse (§9).

For speed, triage (§17) may decode the JPEG at a reduced size (for example with JPEG DCT scaling). Any coordinates produced from the reduced image are mapped back through its transform.

---

# 6. Question Marker Region

## 6.1 Page Geometry

Before cropping, each answer page's layout is found. The steps are simple, fast image-processing operations:

| Landmark | Method (indicative) | Use |
|---|---|---|
| Margin rule `margin_rule_x` | Vertical morphological opening, then column projection within the expected x-band for the page's parity | Crop boundary, question start x |
| Header rule `header_y` | Horizontal projection near the top | Top of marker region |
| Ruled lines `ruled_line_ys[]` | Horizontal morphological opening + projection | Question start y; grouping candidates by line |
| Parity | Registration-dot position (top-left or top-right), or margin x-band | Choose the expected x-band |
| Skew angle | From the ruled lines | Deskew the crop if the angle is above a threshold |

If the margin rule or ruled lines are not found, the page uses the configured default region and question start is `FALLBACK_OFFSET`. All its markers get the reason code `LANDMARKS_NOT_FOUND`.

## 6.2 Region Definition

The marker region is defined in **physical units, relative to the landmarks**, and converted to pixels using the page's dpi:

```text
region.left   = 0                                   (page edge)
region.right  = margin_rule_x + mm_to_px(spill_mm)   (default spill_mm = 6, configurable)
region.top    = header_y + mm_to_px(2)
region.bottom = page_height
```

The margin rule itself is masked (painted white) in the OCR copy so that it is not read as `1`, `l` or `|`, and so that it does not join the marker to the answer text.

## 6.3 Ink-Colour Separation

When the scan is in colour, pixels whose hue and saturation match valuer inks (red, green; configurable ranges) are removed from the OCR copy of the region. Removed components are kept as `DISCARDED` candidates with reason `VALUER_INK`, for audit.

If the scan is grayscale, this step is skipped and the page is flagged `COLOUR_SEPARATION_UNAVAILABLE`, which lowers the auto-accept threshold (§19).

---

# 7. Detection + Recognition

## 7.1 Detection

Candidate regions are found in the prepared margin region by the configured `CandidateDetector`.

Because the region is tall and narrow, it is processed either:

* as overlapping vertical tiles (for example 4 ruled lines tall with a 1-line overlap), with duplicate detections in the overlaps removed; or
* whole, with detector resize settings chosen so that the marker's x-height stays at or above about 20 px.

The choice is made by benchmark.

## 7.2 Recognition

Each candidate region is recognized. It returns text, confidence, and optionally top-k results and character probabilities (§4).

## 7.3 Line Handling and Position Check

* **Merged lines.** If a recognized line runs past `margin_rule_x + spill`, only the prefix that matches the marker grammar is kept, and the box is trimmed to the characters kept (using the CTC character positions when available, otherwise proportionally).
* **Position check.** A candidate is positionally valid if its box centre is left of `margin_rule_x + spill/2`, or the box crosses the margin rule. Candidates that start to the right of the margin rule (for example numbered points inside an answer such as `(2)` or `②`) are `DISCARDED` with reason `INSIDE_ANSWER_AREA`.
* **Grouping by line.** Candidates are assigned to the ruled line containing their vertical centre. Fragments on the same line are merged left to right before the grammar parse.

---

# 8. Spatial Representation

The detector's polygon is kept where available. The **axis-aligned box in canonical coordinates** is the primary representation used downstream:

```text
x      = 42
y      = 618
width  = 95
height = 33
```

When a derived image was rotated, the polygon is transformed back first and the box is recomputed from the transformed polygon. A box is never transformed directly.

---

# 9. Coordinate System

All saved coordinates use `CANONICAL_PAGE_PX`:

* the canonical page image (§5.1);
* origin at the top-left, x to the right, y downward, units in pixels;
* saved with `page_image_width`, `page_image_height` and `page_image_dpi`.

Mapping from a derived image back to canonical coordinates:

```text
p_canonical = inverse(T_derived←canonical) · p_derived
```

For a crop alone, this reduces to:

```text
page_x = crop_x + ocr_x
page_y = crop_y + ocr_y
```

With scaling by factor `s`, the crop offset still applies: `page_x = crop_x + ocr_x / s`.

With deskew, the full affine inverse is required.

Conversion for consumers that work in PDF space. PDF space has its origin at the bottom-left and uses points:

```text
x_pt = x_px × 72 / dpi
y_pt = page_height_pt − (y_px × 72 / dpi)
```

This is valid when the embedded image fills the page's CropBox (true for the sample). Otherwise the image's placement matrix must be applied as well.

Viewers that render at a different resolution scale by `render_width / page_image_width`.

---

# 10. Marker Grammar and Normalization

## 10.1 Grammar

The recognized text is parsed with a configurable grammar, not by removing characters:

```text
marker      := [noise] [prefix] [sep] qid [close]
prefix      := "ANS" | "ANS." | "A" | "Q" | "Q." | "QN" | "QNO" | "Q.NO" | "Q.NO." | "NO" | ...   (configurable)
noise       := up to 2 leading characters left over from a clipped prefix (e.g. "S", "N", "2", "8")
sep         := space | "." | ":" | "-"
qid         := segment ( [sep] segment )*
segment     := digits | letter | roman | "(" segment ")"
close       := ")" | "." | ":"
```

* Parsing works from the **right end** of the token: the ID is anchored at the end, so leftover noise at the start cannot become part of the ID. For example, `s 1.` gives `1`, and `2No6)` gives `6`.
* Because leftover noise may be a digit (`8` or `2` from a clipped `S` or `Q`), a parse that drops leading digits as noise is recorded as `NOISE_STRIPPED`. It is accepted only if the resolution gate is still met (§19). Otherwise the marker goes to review.
* A token that matches only a prefix or header (`Q.No.`, `Ans`) with no ID is `DISCARDED` with reason `HEADER_TOKEN`.
* A closing `)` misread as `1` (for example `5)` read as `51`) is handled by the confusion-weighted matching (§12), not by the grammar.

## 10.2 Normalization

Normalization produces a **list of segments**, not a flattened string:

```text
Raw OCR:      "Ans 10 b.1)"
Parsed qid:   "10 b.1"
Normalized:   ["10", "b", "1"]     (key: "10|b|1")
```

Operations: fold case, trim, remove brackets and separators **between** segments, convert fullwidth or unusual characters.

Normalization is deterministic and versioned (`grammar_version`, `parser_version`).

---

# 11. Examination Question List

The examination question configuration is loaded before processing and pinned by `question_list_version`.

```text
10          (resolution target: no — has children)
10(a)
10(b)       (resolution target: configurable)
10(b)(1)
10(b)(2)
11
12(a)
12(b)
```

For each identifier the application keeps:

```text
canonical        10(b)(1)
segments         ["10", "b", "1"]
segment_types    [NUM, ALPHA, NUM]      (or ROMAN)
key              "10|b|1"
is_target        true / false
```

**Checks when the list is loaded:**

* **Duplicate keys.** Two canonical IDs with the same key → configuration error.
* **Flattened collisions.** Two IDs that become the same string once separators are dropped (for example `1(1)` and `11`, or `5(a)(ii)` and `5(a)(11)`) are marked `confusable`. The resolver never auto-accepts a match between confusable IDs based on the text path alone.

Indexes: exact key lookup, plus the list of all targets for scoring (lists are small, typically under 100 IDs).

---

# 12. Constrained Question Resolution

The resolver only ever returns identifiers from the loaded list.

## 12.1 Exact Match

If the normalized key equals exactly one target key → `EXACT_UNIQUE`.

## 12.2 Constrained Scoring (text path)

Otherwise, each target is scored against the recognized text using:

* **Position-typed coercion.** The target's segment types say which character type is expected where. A `6` in an `ALPHA` position is treated as a cheap substitution for `b`; an `l` in a `NUM` position as `1`.
* **Confusion-weighted edit distance**, using a configurable table (initially `b/6`, `l/1/I/|`, `)/1/J`, `o/0`, `s/5`, `z/2`, `g/9`, `i/1`, `u/ii`). The table is re-estimated from benchmark errors.
* **Length penalty**, for missing or extra segments.

Result: the best candidate, its score, and the **margin** to the second-best candidate.

## 12.3 Constrained Scoring (probability path, optional)

If the recognizer returns CTC character probabilities, each target's allowed surface forms (for example `10b1`, `10(b)(1)`, `10b1)`) are scored by CTC likelihood, and the best form's log-likelihood is used per target. This uses the question list as a proper constraint and gives a meaningful margin. Where available it is preferred to §12.2.

## 12.4 Decision

```text
EXACT_UNIQUE                                       → resolved
best_score ≥ T_match  AND  margin ≥ T_margin       → CONSTRAINED_UNIQUE
otherwise                                          → AMBIGUOUS (top-k kept) or NO_MATCH
```

Fuzzy matching never creates identifiers. It only ranks configured ones.

Example:

```text
OCR:          "Ans 10 b1)"
Normalized:   10|b|1
Exact match:  10(b)(1)      → EXACT_UNIQUE
```

```text
OCR:          "5)" read as "51"
Targets:      5, 6, 7 ... (no 51)
Scores:       5 (")"→"1" confusion, low cost), 1 (drop "5", higher cost)
Margin OK     → CONSTRAINED_UNIQUE 5
```

---

# 13. Question Marker Result

```text
QuestionMarkerResult

marker_result_id          (stable: hash(script_id, page_id, processing_version, line_index, x-bucket))
script_id
page_id
page_index
processing_version

raw_ocr_text
parsed_marker
normalized_marker
canonical_question_id
candidate_list            [(id, score)] top-k
resolution_method         EXACT_UNIQUE | CONSTRAINED_UNIQUE | NONE
parse_flags               e.g. NOISE_STRIPPED, LINE_TRIMMED

detection_confidence
recognition_confidence
resolution_confidence
resolution_margin

coordinate_system         CANONICAL_PAGE_PX
page_image_width / height / dpi
marker_polygon            (optional)
marker_x, marker_y, marker_width, marker_height

question_start_x
question_start_y
question_start_method     LANDMARK_LINE | FALLBACK_OFFSET | MANUAL
question_start_confidence

status                    ACCEPTED | REVIEW_REQUIRED | UNRESOLVED | DISCARDED | REJECTED
review_reasons[]
discard_reason            VALUER_INK | INSIDE_ANSWER_AREA | HEADER_TOKEN | ...
source                    AUTOMATIC | REVIEWER_CORRECTED | REVIEWER_ADDED

ocr_engine, ocr_model, ocr_model_version
preprocessing_version, grammar_version, parser_version
question_list_version, config_version
created_at
```

`FAILED` is a **page** status (§18), not a marker status.

---

# 14. Question Start Coordinate

The marker location and the answer-start location are separate concepts and are saved separately.

```text
margin | answer area
       |
QNo8)  |                                        ← marker's line (empty in the answer area)
       |---------------------------------------
       | From : ...                             ← first answer ink
       |---------------------------------------
       ^
       question_start = (margin_rule_x, top of the "From" line)
```

Definition (Requirements FR-11):

* `question_start_x` = `margin_rule_x` + `start_padding_px` (default 0).
* `question_start_y` = the top ruled line of the first line (at or below the marker's line) that contains answer ink.

---

# 15. Determining Question Start

## 15.1 Phase 1: `LANDMARK_LINE` (single primary method)

```text
1. L0 = index of the ruled line containing the marker's vertical centre
        (the nearest line band if the centre lies on a rule).
2. For L = L0 .. L0 + max_lines_below (default 3):
       stop if L reaches the line of the next marker on this page;
       if the band [ruled_line_ys[L], ruled_line_ys[L+1]] contains answer ink
         to the right of margin_rule_x (after masking rules and valuer ink,
         ink pixels ≥ min_ink_ratio), choose L and stop.
3. If a line was found:
       question_start_y = ruled_line_ys[L]
       question_start_x = margin_rule_x + start_padding_px
       confidence = high (lower if L > L0 + 1)
   else:
       question_start_y = ruled_line_ys[L0], confidence = low → START_UNCERTAIN
```

This covers the observed cases: the answer on the marker's line, the answer starting one or two lines below, and a centred first line (x stays at the margin rule).

Optionally, `first_ink_x` (the leftmost answer ink on the chosen line) may be saved for information.

## 15.2 `FALLBACK_OFFSET`

Used only when landmarks were not found:

```text
question_start_x = marker_x + marker_width + mm_to_px(fallback_offset_mm)
question_start_y = marker_y − mm_to_px(1)
```

Always sent to review with reason `LANDMARKS_NOT_FOUND`.

## 15.3 Deferred: `ANSWER_REGION` / `HYBRID`

General answer-region layout analysis (for unruled booklets) is deferred until a booklet template requires it. The method enum keeps room for it.

The question start is never defined as the right edge of the OCR crop.

---

# 16. Multiple Question Markers

The detector returns every candidate in the region. After the position check, grammar and resolution, each surviving marker is handled independently:

```text
Detection → Recognition → Position check → Grammar/Normalization
   → Resolution → Question start (search bounded by the next marker) → Gate
```

Markers are numbered top to bottom (`line_index`). Two candidates on the same ruled line are merged before parsing (§7.3), not reported as two markers.

---

# 17. Page Triage and Content Class

## 17.1 Page Roles

The booklet template sets page roles by page index (for example page 1 `COVER`, page 2 `DO_NOT_WRITE`, others `ANSWER`). Pages whose role excludes them are `SKIPPED_BY_ROLE` and are not decoded for OCR (§32).

As a safeguard, a perceptual hash of known template pages can confirm the role. A mismatch (for example pages in the wrong order) flags the script.

## 17.2 Content Class (mandatory)

Every answer page gets a `content_class`:

```text
BLANK    no student ink after removing template elements
SPARSE   student ink below a threshold
NORMAL
```

Method: on a reduced-resolution copy, remove printed rules (morphology), the watermark (low-saturation, low-contrast pixels) and valuer ink (colour), then count connected components of dark ink above a size threshold. Show-through from the reverse side is faint and blurred, and is removed by a contrast threshold that is calibrated on blank pages.

**Content class never stops marker detection.** For example, a `BLANK` page still has its margin region checked by the (cheap) candidate detector. If a candidate is found, the page is reclassified.

Content class may be used to skip the **expensive** steps (full recognition), but only for regions with no candidates.

---

# 18. Page Result

```text
processing_status   PROCESSED | SKIPPED_BY_ROLE | FAILED_RETRYABLE | FAILED_PERMANENT
content_class       BLANK | SPARSE | NORMAL | TEMPLATE
marker_count
landmarks_found     margin_rule_x, header_y, ruled_line_count, parity, skew_deg
flags               LANDMARKS_NOT_FOUND, COLOUR_SEPARATION_UNAVAILABLE, ...
error_code
```

This distinguishes a processed page with no marker, a blank page, a skipped template page and a failed page, which matters for audit and troubleshooting.

---

# 19. Confidence and Review Gate

## 19.1 Provisional Phase-1 Rule

A marker is `ACCEPTED` only if **all** of the following hold. Otherwise it is `REVIEW_REQUIRED` or `UNRESOLVED`, with reason codes.

| Condition | Reason code if it fails |
|---|---|
| Position check passed | `INVALID_POSITION` |
| `resolution_method ∈ {EXACT_UNIQUE, CONSTRAINED_UNIQUE}` | `AMBIGUOUS` / `NO_MATCH` |
| `recognition_confidence ≥ T_rec` (raised if `NOISE_STRIPPED` or `COLOUR_SEPARATION_UNAVAILABLE`) | `LOW_RECOGNITION_CONFIDENCE` |
| `resolution_margin ≥ T_margin` (constrained matches only) | `AMBIGUOUS` |
| Matched ID is not `confusable`, or the probability path (§12.3) was used | `AMBIGUOUS` |
| `question_start_method = LANDMARK_LINE` and `confidence ≥ T_start` | `START_UNCERTAIN` / `LANDMARKS_NOT_FOUND` |
| No script-level flag against this marker (§23a) | `DUPLICATE_IN_SCRIPT`, `OUT_OF_ORDER`, … |

Initial threshold values are placeholders. They are set from the labelled dataset.

## 19.2 Calibration

Raw confidences are calibrated (for example with isotonic regression) on held-out labelled data, per engine version. Thresholds are then chosen for the **wrong auto-accept target** (Requirements 6.7), and the resulting review rate is reported.

Examples:

```text
OCR confidence (calibrated) = 0.97
Resolution                  = EXACT_UNIQUE
Position                    = valid
Question start              = LANDMARK_LINE, high
Script checks               = clean
→ ACCEPTED
```

```text
OCR confidence = 0.61
Resolution     = AMBIGUOUS (7: 0.48, 1: 0.41)
→ REVIEW_REQUIRED [LOW_RECOGNITION_CONFIDENCE, AMBIGUOUS]
```

---

# 20. CPU Deployment

The service runs on CPU only at first.

Starting server profile:

```text
CPU:       12–16 cores
RAM:       16 GB minimum
Preferred: 32 GB
GPU:       Not required
```

Memory per worker (model runtime plus decoded page buffers, about 11.5 MB per 1500 × 2570 RGB page) is measured in the benchmark. The exact hardware requirement is confirmed by benchmark testing.

---

# 21. Parallel Processing

Page workers process pages independently.

* **Thread pinning.** Each worker process pins its inference library's thread pools (for example OpenMP/MKL) to `threads_per_worker` (default 1–2), so that `workers × threads_per_worker ≈ physical cores`. Otherwise thread oversubscription reduces throughput.
* **Model loading.** Models are loaded once per worker process and kept warm.

Benchmark matrix:

```text
workers:              2, 4, 6, 8, 12
threads_per_worker:   1, 2
batch (recognition):  1, 4, 8
region mode:          whole strip | tiled
```

The best configuration depends on the CPU, the image size, the OCR model and the preprocessing workload.

---

# 22. Centralized Processing for Multiple Scanners

OCR is not installed on each scanner workstation.

```text
Scanner 1 ----\
Scanner 2 -----\
Scanner 3 ------> Scan Ingestion ---> Central Queue
Scanner N -----/                         |
                                         v
                              +----------------------+
                              | Page Worker Pool     |
                              +----------------------+
                                         |
                                         v
                              Script Consolidation
                                         |
                                         v
                              Question Marker Results
```

OCR capacity can be increased independently of scanning capacity.

---

# 23. Job Processing

## 23.1 Page Jobs

```text
job_id              = hash(script_id, page_id, processing_version)
script_id
page_id
page_index
processing_version
processing_attempt
status
started_at / completed_at
error_code
```

State flow:

```text
QUEUED → PROCESSING → COMPLETED
PROCESSING → FAILED_RETRYABLE → (backoff) → QUEUED        (up to max_attempts)
PROCESSING → FAILED_PERMANENT
```

## 23a. Script Consolidation Job

Triggered when every page job of a script has reached a final state (`COMPLETED` or `FAILED_PERMANENT`), tracked by a per-script counter.

Checks (Requirements FR-17):

* duplicate question IDs among `ACCEPTED` and `REVIEW_REQUIRED` markers → all involved markers get `DUPLICATE_IN_SCRIPT`;
* out-of-order IDs (compared with the question list's order) → `OUT_OF_ORDER` on the markers that break the order;
* answer count conflicts with the answering rules → script flag `ANSWER_COUNT_MISMATCH`;
* the script has `NORMAL` answer pages but no markers, or the first `NORMAL` answer page has no marker → script flag `POSSIBLE_MISSED_MARKER`;
* a page is `FAILED_PERMANENT` → script flag `INCOMPLETE`.

Consolidation can downgrade `ACCEPTED` to `REVIEW_REQUIRED`. It never changes a question ID.

A script with any flag is shown to reviewers as a **script-level review**: all answer-page thumbnails with markers overlaid, so that missed markers can be added.

---

# 24. Idempotency

A page must not produce duplicate results because of a worker restart, a queue retry, a network failure or a service restart.

* The processing identity is `script_id + page_id + processing_version`.
* All marker results for that identity are written **atomically as one set** (replace-on-write), so a retry replaces the set instead of adding to it.
* `marker_result_id` is derived from the processing identity plus the marker's line index and horizontal bucket, so it is stable across retries.

---

# 25. Persistence

The original OCR evidence and the resolved business result are both saved:

```text
raw_ocr_text:          "Ans 10 b1)"
parsed_marker:         "10 b1"
normalized_marker:     "10|b|1"
candidate_list:        [("10(b)(1)", 0.97), ("10(b)", 0.41)]
canonical_question_id: "10(b)(1)"
```

The raw OCR output and discarded candidates are never thrown away after normalization.

Optionally, a small crop of each marker (PNG, a few kB) is kept for review and for building the training and benchmark dataset. Its retention follows the platform's retention policy (§32).

---

# 26. Human Override

Reviewer actions:

| Action | Effect |
|---|---|
| `CONFIRM` | Marks the automatic result as reviewed |
| `CORRECT` | Changes `canonical_question_id`, marker coordinates and/or question start (`question_start_method = MANUAL`) |
| `REJECT` | Sets status to `REJECTED` (not a marker) |
| `ADD` | Creates a new marker with `source = REVIEWER_ADDED` |

Each action records:

```text
original_value
corrected_value
reviewer
timestamp
reason
```

**Which result is in effect:**

1. If a human-reviewed result exists for the marker or page, it is the result in effect.
2. Otherwise, the latest automatic result for the active `processing_version` is in effect.
3. Reprocessing never overwrites a human-reviewed result. If the new automatic result disagrees with it, the marker is flagged `HUMAN_VS_REPROCESS_CONFLICT` for information. The human value stays in effect.

The original automatic result always remains available for audit.

---

# 27. Model and Processing Provenance

Every result identifies:

```text
ocr_engine
ocr_model
ocr_model_version
preprocessing_version       (incl. page-geometry and colour-separation logic)
grammar_version
parser_version
question_list_version
config_version
```

This allows results to be reproduced or investigated later.

---

# 28. Model Packaging

The OCR models and supporting files are deployed as a versioned model bundle.

The design does **not** hard-code model file sizes or filenames as architectural requirements unless they have been verified for the exact deployed release.

```text
model/
    detection/
    recognition/
    optional_classification/
config/
    ocr.yaml
    grammar.yaml
    confusion_table.yaml
    templates/            (booklet templates, page roles, region parameters)
calibration/
    <engine_version>.json
version metadata
```

---

# 29. Benchmarking Strategy

The engines are benchmarked on real, representative answer-script PDFs, labelled with marker box, canonical ID and question start.

The dataset must include:

* different handwriting styles, including cursive;
* different scanners, resolutions and dpi, colour and grayscale;
* faint and dark handwriting;
* skewed pages and markers;
* **markers with a prefix** (`Ans`, `Q`, `Q.No`, `QNo`);
* **markers whose prefix is clipped at the scan edge**;
* markers touching or crossing the margin rule;
* **markers on a line above the answer**;
* **over-written or corrected markers**;
* markers with spaces, punctuation and brackets;
* multi-level identifiers, including roman-numeral subparts;
* multiple markers on a page;
* **valuer scores and ticks in the margin next to markers**;
* **numbered and circled points inside answers near the margin rule**;
* **section headings and `Q.No.` header tokens**;
* pages without markers, blank pages, pages with show-through, and template pages;
* scripts with additional booklets;
* difficult or ambiguous handwriting.

Measure at least:

```text
Marker recall / precision
Character and marker recognition accuracy
Canonical resolution accuracy
Wrong auto-accept rate           (primary safety metric)
Review rate by reason code
Marker box accuracy
Question-start accuracy (line hit, x error)
Processing time / page (including decode) and / script
CPU and memory use per worker
```

Suggested initial dataset size: at least 300–500 answer pages from at least 3 examinations, split into calibration and held-out test sets. Both sample scripts reviewed so far should be included.

---

# 30. Engine Comparison

Compare, all behind the same interfaces:

```text
A. PP-OCRv5 Mobile det + rec                (baseline)
B. MarginComponentDetector + PP-OCRv5 Mobile rec
C. A or B with a fine-tuned restricted-charset recognizer
D. PP-OCRv5 Server det + rec                (reference)
```

Selection criteria:

```text
Wrong auto-accept rate at target
+ Review rate at that operating point
+ End-to-end resolution accuracy
+ Question-start accuracy
+ CPU throughput
+ Memory use
+ Operational complexity
```

Generic OCR benchmark scores are not used as a substitute.

---

# 31. Performance Planning

Expected yearly volume:

```text
1,000,000 scripts/year × ~28 pages/script = 28,000,000 pages/year
```

Capacity is sized from **peak** demand (Requirements NFR-03), not the yearly total.

Per-page cost is dominated by:

1. PDF parsing and JPEG decoding;
2. page geometry and triage (cheap image operations on a reduced-resolution copy);
3. detection and recognition on the **margin region only**, and only on answer pages.

Template pages are skipped entirely. Blank pages normally cost only decoding and triage (45% of pages in one sample).

Planning target until benchmarked:

```text
8–20 scripts/minute/server  ≈ 2.5–10 pages/second/server
```

Example:

```text
12 scripts/minute × 60 = 720 scripts/hour/server
```

Final capacity uses measured throughput under the expected peak scanning load.

---

# 32. Security and Data Handling

The OCR service shall:

* access only the scans it needs to process;
* keep source images unchanged;
* **not decode or OCR pages whose role excludes them** (the cover page contains personal data);
* avoid unnecessary copies of full-resolution scans. Derived images are held in memory, except retained marker crops (§25);
* protect results according to the Digital Evaluation platform's security model;
* keep processing audit information;
* follow the platform's retention and legal-hold requirements.

---

# 33. Extensibility

OCR technologies can be replaced or added without changing the business-level question-resolution interface.

```text
                    +-----------------------+
                    | Question Marker API   |
                    +-----------+-----------+
                                |
         +----------------------+----------------------+
         |                      |                      |
         v                      v                      v
  PP-OCRv5 Mobile      MarginComponent + Rec     Fine-tuned Rec
```

All engines produce the common intermediate representation (§4).

Booklet templates, grammars and confusion tables are configuration, so a new booklet format does not require code changes unless it has no ruled margin (§15.3).

---

# 34. Recommended End-to-End Processing

```text
Per page (page worker):
 1. Receive page job
 2. Load canonical page image (PDF → native embedded scan); record w/h/dpi
 3. Check page role → skip template pages (SKIPPED_BY_ROLE)
 4. Page geometry: margin rule, header rule, ruled lines, parity, skew
 5. Content class (BLANK / SPARSE / NORMAL)
 6. Build landmark-relative marker region; mask rules; separate valuer ink
 7. Candidate detection (tiled or whole strip)
 8. Recognition
 9. Position check, line grouping, trim merged lines
10. Grammar parse → segment normalization
11. Constrained resolution against the question list (exact → scored)
12. Question start (LANDMARK_LINE, bounded by the next marker)
13. Page-level gate → status + reason codes
14. Atomic write of page result + marker results + provenance

Per script (consolidation job):
15. When all pages are final → script-level checks
16. Downgrade flagged markers; set script flags
17. Send to review (marker-level and script-level) or accept
```

---

# 35. Initial Implementation Decision

The recommended Phase-1 implementation is:

```text
PDF → canonical page image (native, recorded w/h/dpi)
        +
Page roles + mandatory content class
        +
Page geometry (margin rule, ruled lines)
        +
Landmark-relative margin crop + rule masking + valuer-ink separation
        +
PP-OCRv5 Mobile detection + recognition (baseline; tiled strip if needed)
        +
Position check + marker grammar + segment normalization
        +
Constrained resolution (exact → typed, confusion-weighted scoring, margin rule)
        +
Question start: LANDMARK_LINE
        +
Calibrated confidence gate with reason codes
        +
Script-level consolidation
        +
Review with confirm / correct / reject / add
```

In parallel, collect labelled marker crops so that options B and C (§30) can be evaluated without redesigning the rest of the component.

The key architectural principles are:

> **OCR proposes a candidate; the examination question list decides which identifiers are valid; page landmarks, not fixed pixels, decide where things are; and when in doubt, a human decides.**

---

# 36. Open Decisions

| # | Decision | Default until decided |
|---|---|---|
| D1 | Are scans taken before or after manual evaluation? (Problem Statement A1) | Assume valuer ink may be present |
| D2 | Colour scanning mandatory? | Yes. Grayscale pages are flagged and use stricter thresholds |
| D3 | Are parent IDs (e.g. `10(b)`) valid resolution targets? | Per examination configuration. Default: leaf IDs only |
| D4 | Relative sub-markers (`(ii)` alone under a parent) | Out of scope. Sent to review as `NO_MATCH` / `AMBIGUOUS` |
| D5 | Acceptance target values (Requirements §6) | Provisional values in Requirements §6 |
| D6 | Keep marker crops for training? | Yes, subject to retention policy approval |
