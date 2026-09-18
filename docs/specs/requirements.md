# **QUESTION MARKER DETECTOR *REQUIREMENTS***

## Handwritten Question Marker Detection and Answer Start Identification

**Version:** 1.1 (revised after review against sample scripts Doc0697 and Doc0711)

> **Revision notes (1.0 → 1.1)**
>
> | Change | Sections |
> |---|---|
> | PDF input, per-page pixel size, page roles | 2.1, 2.4, FR-19 |
> | Marker region defined relative to page landmarks, not fixed pixels | 2.4, FR-02, NFR-07 |
> | Marker grammar: prefixes, clipped prefixes, marker text merged with answer text | FR-03, FR-04 |
> | Normalized-ID collision check; parent IDs; segment-aware normalization | 2.2, FR-05 |
> | Defined the deterministic resolution rule; constrained fuzzy matching | FR-06 |
> | Rejecting non-marker margin content (valuer ink, enumerations, headings) | FR-18 |
> | Page result split into processing status and content class | FR-09 |
> | Coordinate system defined | FR-10 |
> | Question start defined semantically, with tolerance | FR-11, 6.5 |
> | Reviewers can add and delete markers; checks for missed markers | FR-13, FR-17 |
> | Human corrections take precedence over reprocessing | FR-15 |
> | Script-level consolidation | FR-17 |
> | Status model | 5.3 |
> | Numeric acceptance targets, including wrong auto-accept rate | 6 |
> | Capacity based on peak load | NFR-03 |

---

### 1. Purpose

This document defines the functional and non-functional requirements for:

* detecting handwritten question markers in scanned examination answer scripts;
* resolving them to canonical examination question identifiers;
* determining where each corresponding answer starts.

It intentionally does **not** prescribe a specific OCR technology or implementation framework.

---

## 2. Processing Context

### 2.1 Answer Script

The system shall process scanned answer scripts supplied by ingestion as a PDF, or as an ordered list of page images, per script.

Operating assumptions:

* approximately **20–28 pages per answer script**. The implementation shall not depend on a fixed page count;
* an answer page typically contains **0–3 question markers**. This is a statistic, not a limit, and the implementation shall not impose a maximum;
* question markers normally occur in the left margin, or across the margin rule;
* a script may include additional booklets. Ingestion supplies the pages in reading order (Problem Statement A4).

### 2.2 Examination Question List

The complete, versioned list of valid question identifiers shall be available before processing begins.

The system shall use this list as the authoritative vocabulary for resolving markers.

The list shall:

* represent the question hierarchy, for example `10` → `10(b)` → `10(b)(1)`;
* state which identifiers can be resolution targets. Parent identifiers (for example `10(b)`) may be targets if the examination allows answers at that level;
* optionally include answering rules (for example "answer any 5 of 7") for script-level checks.

When the list is loaded, the system shall reject it, or mark the affected identifiers as permanently ambiguous, if two different identifiers produce the same normalized key (for example `1(1)` and `11`).

### 2.3 Unchanged Source

The original scanned PDF and page images shall remain unchanged.

Image preprocessing, cropping, rotation correction or OCR processing shall work on derived copies. Every derived image shall carry a transform that maps it back to the canonical page image (FR-10).

### 2.4 Page Layout

The system shall support ruled answer booklets with a printed vertical margin rule and horizontal ruled lines.

The system shall **not** assume that:

* all pages have the same pixel size;
* the margin rule is at the same pixel position on every page. It differs between odd and even pages and drifts from page to page;
* markers are complete. Part of a prefix may be cut off at the scan edge.

The question-marker region shall be defined **relative to page landmarks** found on each page (margin rule, header rule), using physical units (mm), not absolute pixel positions.

---

# 3. Functional Requirements

## FR-01 — Page Processing

The system shall detect and recognize markers on each page independently. This allows pages to be processed in parallel.

It shall support pages containing:

* no question marker;
* one question marker;
* multiple question markers.

The system shall not assume that every page contains a question marker.

Resolution results may be refined by script-level consolidation (FR-17).

---

## FR-02 — Question Marker Detection

The system shall find candidate handwritten question markers in the question-marker region of each answer page.

* The region shall be derived from the landmarks found on that page (2.4). Its boundaries (for example "from the page edge to X mm right of the margin rule") shall be configurable.
* The system shall support markers at any vertical position, and markers that touch or cross the margin rule.
* Each candidate shall include its location on the page.
* The system shall check whether each candidate is in a valid position. For example, a candidate whose centre lies well inside the answer area is not a valid marker.
* If the landmarks cannot be found on a page, the system shall fall back to a configured default region and flag the page's results for review.

---

## FR-03 — Character Recognition

The system shall recognize the characters forming a question marker:

* digits `0–9`;
* letters `a–z` and `A–Z`, including roman-numeral subparts such as `i`, `ii`, `iv`;
* marker punctuation: `( ) . - :` and space.

The system shall not require the student's answer text to use the same character set.

---

## FR-04 — Marker Format Variations

The system shall accept marker text described by a configurable **marker grammar**:

```text
[prefix] [separator] question-id [closing punctuation]
```

At minimum it shall support:

```text
10b1
10.b.1
10 b 1
10-b-1
10b1)
(10b1)
10 (b) (1)
Ans 10b1
Ans. 10(b)(1)
Q10b1
Q.No 10 b 1
QNo10b1)
```

The system shall also:

* tolerate a **partially clipped prefix**, for example `s 1.` for `Ans 1.`, or a leftover stroke before the ID;
* take the marker out of a recognized line that also includes answer text, for example `QNo6) The chapter…`;
* discard a token that contains only a prefix or header, for example `Q.No.` with no ID. Discarded tokens are kept for audit but not sent to review.

The system shall normalize equivalent representations before resolution. Normalization shall keep the ID's segments separate (for example `10|b|1`), not flatten them into a single string.

---

## FR-05 — Canonical Question Resolution

The system shall resolve a recognized marker against the configured examination question list.

Example:

```text
OCR text:
    "Ans 10 b1)"

Normalized:
    "10|b|1"

Canonical question:
    "10(b)(1)"
```

The system shall never produce a question identifier that does not exist in the examination configuration.

---

## FR-06 — Ambiguous Resolution

A marker shall be resolved automatically only when **one** of the following deterministic rules applies:

1. **Exact unique match.** The normalized marker matches exactly one configured identifier.
2. **Constrained unique match.** The best-scoring configured identifier's score is at or above a configured threshold, **and** it beats the second-best candidate by at least a configured margin.

The scoring for rule 2 shall consider only configured identifiers. It shall take into account which character type (digit, letter, roman numeral) is allowed at each position and which character confusions are common (for example `b/6`, `l/1`, `)/1`, `o/0`, `s/5`, `i/1`).

Otherwise the result shall be marked **ambiguous**, with its candidate list recorded, and sent to review.

---

## FR-07 — Unresolved Question Marker

If the OCR result cannot be resolved to any configured question, the system shall record the result as **UNRESOLVED** and send it to review.

It shall not silently map the marker to an unrelated question.

---

## FR-08 — Multiple Markers

Where a page contains multiple question markers, the system shall create a separate result for each marker, in top-to-bottom order.

Example:

```text
Page 12

Marker 1 → 5(a)
Marker 2 → 5(b)
Marker 3 → 6
```

Each result shall keep its own coordinates, confidence, status and question start.

A marker's answer-start search shall not extend past the next marker on the same page.

---

## FR-09 — Page Classification

Each page shall have two independent attributes:

* `processing_status`: `PROCESSED`, `SKIPPED_BY_ROLE`, `FAILED_RETRYABLE` or `FAILED_PERMANENT`;
* `content_class`: `BLANK`, `SPARSE`, `NORMAL` or `TEMPLATE`.

It shall also record `marker_count` (accepted plus reviewable markers).

This distinguishes, for example:

* a page processed with no marker (a continuation page);
* a blank page;
* a template page that was skipped;
* a page where processing failed.

Blank classification is **mandatory**. It shall ignore printed rules, the watermark, handwriting showing through from the reverse side, and valuers' strike lines.

A page classified as `BLANK` or `SPARSE` shall still be searched for markers unless its page role excludes it. Classification shall never stop a marker from being detected.

---

## FR-10 — Marker Coordinates and Coordinate System

For every marker result, the system shall record its location on the **canonical page image**:

```text
x, y, width, height   (axis-aligned box)
polygon               (optional)
```

The canonical coordinate system shall be:

* the page's embedded scan at native resolution, or, if the page has no single embedded scan, a rendering at a configured dpi;
* origin at the top-left, x to the right, y downward, units in pixels;
* identified in each result as `coordinate_system = CANONICAL_PAGE_PX`, together with `page_image_width`, `page_image_height` and `page_image_dpi`.

Coordinates found in derived images (crops, resized, deskewed or rotated images) shall be mapped back to the canonical system by applying the inverse of the derived image's full transform.

---

## FR-11 — Question Start Coordinate

For each resolved or reviewable marker, the system shall determine the start of the corresponding answer:

```text
question_start_x
question_start_y
question_start_method
question_start_confidence
```

**Definition** (Problem Statement §6.2):

* `question_start_x` is the left edge of the answer area, meaning the margin rule on that page.
* `question_start_y` is the top of the ruled line containing the first line of answer ink at or below the marker. The search covers a configurable number of lines, stops at the next marker, and does not go past the page.

Methods:

* `LANDMARK_LINE`: derived from the margin rule and ruled lines found on the page (primary method);
* `FALLBACK_OFFSET`: a configured offset from the marker, used only when landmarks are not found. Always sent to review;
* `MANUAL`: set by a reviewer.

The system shall not define the question start as the right edge of the OCR crop.

If no answer ink is found within the search window, the result shall be sent to review.

---

## FR-12 — Confidence

The system shall provide confidence information for:

* marker detection;
* marker recognition;
* question resolution, including the score gap to the second-best candidate;
* question-start determination.

Confidence thresholds shall be configurable.

Before production use, confidence values shall be **calibrated** on the labelled dataset (section 6), so that thresholds correspond to measured error rates.

---

## FR-13 — Human Review

The system shall send a marker for human review when:

* recognition confidence is below the threshold;
* more than one candidate question remains plausible;
* no configured question matches;
* the marker's position is invalid or uncertain;
* the question start is uncertain or used `FALLBACK_OFFSET`;
* a script-level check fails (FR-17);
* processing produced an otherwise invalid result.

Each review item shall carry one or more **reason codes**.

A reviewer shall be able to:

* **correct** the question ID, marker coordinates or question start;
* **confirm** a result;
* **reject** a candidate that is not a marker;
* **add** a marker the system missed, with its coordinates and question start.

A human correction shall override the automatic result.

---

## FR-14 — Auditability

The system shall keep enough information to determine:

* which page was processed, and its canonical image size and dpi;
* the OCR engine and model version, the preprocessing, grammar and parser versions, the question-list version, and the configuration version;
* raw OCR output;
* normalized marker;
* canonical question ID and the other candidates considered;
* confidence values;
* marker coordinates and question-start coordinates;
* resolution method and question-start method;
* status and review reason codes;
* discarded and rejected candidates, including those suppressed as non-marker content;
* whether, when, by whom and why a human changed the result.

---

## FR-15 — Idempotency and Precedence

Reprocessing the same page with the same processing version shall not create duplicate results.

The system shall use stable identifiers for scripts, pages, processing jobs and markers.

All marker results for a page and processing version shall be written together in one atomic operation.

Reprocessing with a new processing version shall create a new result version and shall **not** overwrite human-reviewed results. When a newer automatic result disagrees with a human-reviewed result, the human result shall remain in effect and the disagreement shall be flagged.

---

## FR-16 — Retry and Failure Handling

Temporary processing failures shall be retryable, up to a configured limit.

A failed page shall not cause the loss of successful pages from the same script.

Permanent failures shall be recorded with an error reason, and the script shall be flagged as incomplete.

---

## FR-17 — Script-Level Consolidation

After all pages of a script are processed, the system shall run a script-level consolidation step that:

* orders all markers by page and vertical position;
* flags **duplicate** question IDs;
* flags **out-of-order** question IDs;
* flags an **answer count** that conflicts with the configured answering rules, where such rules exist;
* flags scripts that contain answer pages but **no markers**;
* flags a first answer page that has ink but no marker;
* flags scripts that have failed pages.

Consolidation may use script context to **send results to review**. It shall not change an automatically resolved ID without human review.

---

## FR-18 — Rejecting Non-Marker Margin Content

The system shall reduce false markers caused by:

* valuer ink (red or green scores, ticks and strike lines), by separating ink colours when the scan is in colour (Problem Statement A1, A2, A5);
* numbered points inside an answer near the margin rule (for example `(1)`, circled `②`), using the position check (FR-02);
* headings and header tokens (for example `Section B`, `Q.No.`), using the grammar (FR-04);
* crossed-out writing.

Suppressed candidates shall be kept for audit (FR-14).

---

## FR-19 — Page Roles

For each booklet template it shall be possible to configure page roles, for example:

* `COVER`;
* `INSTRUCTIONS` or `DO_NOT_WRITE`;
* `ANSWER`.

Pages whose role excludes marker detection shall be recorded as `SKIPPED_BY_ROLE`, and marker detection shall not run on them.

---

# 4. Non-Functional Requirements

## NFR-01 — CPU Operation

The solution shall run on CPU-only infrastructure.

A GPU shall not be required for deployment.

---

## NFR-02 — Scalability

The solution shall support multiple scanner stations submitting scripts at the same time.

OCR processing shall scale centrally through multiple processing workers.

The architecture shall not require an OCR engine on every scanner workstation.

---

## NFR-03 — Performance and Capacity

The initial planning target is approximately **8–20 complete answer scripts per minute per server**, which is about 2.5–10 pages per second. This shall be validated on representative scans.

This is an engineering planning target, **not an acceptance guarantee**.

Capacity shall be sized from **peak** demand, not the yearly total:

```text
required_servers = ceil( peak_pages_per_minute
                         / measured_pages_per_minute_per_server
                         × headroom_factor )

peak_pages_per_minute = scanners_active_at_peak × pages_per_minute_per_scanner
```

Performance testing shall use representative answer-script PDFs, including blank and template pages, rather than generic OCR benchmarks alone. It shall measure the full per-page cost, including PDF and JPEG decoding.

---

## NFR-04 — Hardware

A server with approximately the following should be considered for the initial deployment, subject to benchmarking:

* **12–16 CPU cores**;
* **16 GB RAM minimum**, 32 GB preferred.

Memory use per worker shall be measured and recorded as part of the benchmark.

---

## NFR-05 — Availability

The processing service shall support:

* worker restart;
* job retry;
* recovery from partial failures;
* horizontal scaling.

---

## NFR-06 — Model Versioning

The OCR model and version shall be recorded with each result.

Changing the OCR model shall not affect the auditability of results produced earlier.

---

## NFR-07 — Configuration

The following shall be configurable without modifying application source code. Configuration shall be versioned and recorded with each result.

* question-marker region, relative to landmarks and in physical units;
* booklet templates and page roles;
* marker grammar (prefixes, separators, closing punctuation);
* normalization rules;
* matching rules, the character-confusion table, thresholds and margins;
* confidence thresholds;
* ink-colour suppression settings;
* question-start parameters (search window in lines, padding, fallback offset);
* worker count, threads per worker and batch size;
* model selection.

---

## NFR-08 — Extensibility

It shall be possible to replace the OCR engine, or the candidate-detection method, without redesigning question resolution or downstream evaluation components.

---

## NFR-09 — Data Protection

The component shall not OCR pages whose role excludes them, such as the cover page, which contains personal data.

Derived images shall be temporary unless they are kept for review or audit.

---

# 5. Data Requirements

## 5.1 Marker Result

A marker result should contain information equivalent to:

```text
marker_result_id          (stable)
script_id
page_id
page_index
processing_version

raw_ocr_text
normalized_marker
canonical_question_id
candidate_list            (id + score, top-k)
resolution_method         (EXACT_UNIQUE | CONSTRAINED_UNIQUE | NONE)

detection_confidence
recognition_confidence
resolution_confidence
resolution_margin

status
review_reasons[]
source                    (AUTOMATIC | REVIEWER_CORRECTED | REVIEWER_ADDED)

coordinate_system         (CANONICAL_PAGE_PX)
page_image_width
page_image_height
page_image_dpi
marker_x, marker_y, marker_width, marker_height
marker_polygon            (optional)

question_start_x
question_start_y
question_start_method
question_start_confidence

ocr_engine
ocr_model
ocr_model_version
preprocessing_version
grammar_version
parser_version
question_list_version
config_version
processing_timestamp
```

## 5.2 Page Result

```text
script_id, page_id, page_index
page_role
processing_status
content_class
marker_count
landmarks_found           (margin_rule_x, ruled_line_ys, parity)
error_code / error_reason
processing_version
```

## 5.3 Status Values

| Marker status | Meaning | Goes to review? |
|---|---|---|
| `ACCEPTED` | Resolved and passed all checks | No |
| `REVIEW_REQUIRED` | Candidate question found, but at least one check failed (see reason codes) | Yes |
| `UNRESOLVED` | No configured question matches | Yes |
| `DISCARDED` | Automatically classified as non-marker content (header token, valuer ink, enumeration). Kept for audit | No |
| `REJECTED` | A reviewer decided it is not a marker | No |

Review reason codes include: `LOW_RECOGNITION_CONFIDENCE`, `AMBIGUOUS`, `NO_MATCH`, `INVALID_POSITION`, `LANDMARKS_NOT_FOUND`, `START_UNCERTAIN`, `DUPLICATE_IN_SCRIPT`, `OUT_OF_ORDER`, `ANSWER_COUNT_MISMATCH`, `HUMAN_VS_REPROCESS_CONFLICT`.

## 5.4 Review Record

```text
marker_result_id
action          (CONFIRM | CORRECT | REJECT | ADD)
original_value
corrected_value
reviewer
timestamp
reason
```

---

# 6. Acceptance Criteria

The implementation shall be evaluated on a representative labelled dataset of real answer-script pages. The dataset shall include, at minimum, the cases listed in Technical Design §29.

All targets below are **provisional** and are to be confirmed by the business owner before acceptance testing.

### 6.1 Detection

* **Marker recall**: markers detected (accepted or reviewable) divided by labelled markers. Provisional target ≥ 98%.
* **Missed-marker catch rate**: missed markers that are caught by a script-level flag. Reported, no target yet.

### 6.2 Recognition

Character-level and marker-level recognition accuracy. Reported for diagnosis.

### 6.3 Resolution

Accuracy of mapping detected markers to the correct configured question.

### 6.4 Location Accuracy

A marker box is correct if it covers at least 90% of the labelled marker's ink area and is no more than twice the labelled box area. Provisional target ≥ 95%.

### 6.5 Question Start Accuracy

A question start is correct if:

* `question_start_y` falls on the same ruled line as the labelled start (about ±4.5 mm, or ±36 px at 200 dpi); and
* `question_start_x` is within 3 mm of the labelled answer-area edge.

Provisional target ≥ 97% of accepted markers.

### 6.6 Review Rate

Share of markers and pages sent to human review. Provisional target ≤ 15% of markers in the first release, reported by reason code.

### 6.7 Wrong Auto-Accept Rate (primary safety metric)

> Markers with status `ACCEPTED` whose canonical question ID is wrong, plus `ACCEPTED` results that are not real markers, as a share of all `ACCEPTED` results.

Provisional target ≤ 0.2%.

### 6.8 End-to-End Accuracy (primary business metric)

> Percentage of labelled question markers whose final result (after automatic processing and the review workflow) is the correct canonical question with a correct question start.

Metrics 6.6, 6.7 and 6.8 shall always be reported together, at the chosen thresholds.

Generic OCR benchmark scores shall not replace these end-to-end measurements.

---

# 7. Explicit Constraints

The solution shall:

* keep the original scanned images unchanged;
* use the examination question list as the authoritative vocabulary;
* never produce question IDs automatically without restriction;
* support multiple question markers per page;
* support pages without markers;
* keep marker coordinates in a defined coordinate system;
* keep a record of how each result was produced;
* provide human correction, including adding and rejecting markers;
* never overwrite human-reviewed results by reprocessing;
* run without requiring a GPU.

---

# 8. Assumptions and Open Decisions

See Problem Statement §10 (A1–A6). Any change to those assumptions requires a review of FR-02, FR-09, FR-18 and FR-19.
