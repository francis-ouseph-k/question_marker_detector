# Problem Statement

## Handwritten Question Marker Detection and Answer Start Identification

**Version:** 1.1 (revised after review against sample scripts Doc0697 and Doc0711)

> **Revision notes (1.0 → 1.1)**
>
> * Added the observed page layout and scan characteristics (§2).
> * Added real marker forms: prefixes (`Ans`, `Q`, `Q.No`), trailing punctuation, and prefixes partly clipped at the scan edge (§3).
> * Added non-marker content in the margin that must not be mistaken for markers: valuer scores, enumerations inside answers, headings (§4.8).
> * Added missed markers as an explicit problem (§4.9).
> * Defined the coordinate system and the meaning of "question start" (§6); corrected the example output so that it is consistent with those definitions.
> * Stated that a wrong automatic acceptance is worse than a human review (§9).
> * Added a section of documented assumptions and open decisions (§10).

---

### 1. Background

The Digital Evaluation platform processes scanned answer scripts for digital evaluation. Each answer script contains handwritten question markers that identify which question the student is answering.

A typical answer script contains approximately **20–28 page images**, including cover and instruction pages. An answer page normally contains **zero or one** question marker and occasionally two or three. More are possible on short-answer papers. The number of markers per page is a statistic, not a limit.

### 2. Page Layout and Scan Characteristics

These observations come from the sample scripts and are treated as the baseline for this component:

* **Input format.** Each script is a PDF produced by the scanning software. Each page embeds one full-page colour JPEG at 200 dpi.
* **Page size varies per page.** The scanner crops each page automatically, so the pixel size differs from page to page and from script to script (for example 1480–1504 × 2564–2576 px). No two pages can be assumed to share pixel coordinates.
* **Booklet layout.** Answer pages are ruled booklets with:
  * a printed header with the institution logo and a horizontal header rule;
  * a vertical **margin rule** separating a narrow left margin from the answer area;
  * horizontal ruled lines roughly 9 mm apart;
  * a faint watermark logo;
  * a black registration dot at the top-left of odd pages and the top-right of even pages.
* **The margin moves.** The margin rule's horizontal position differs by about 13 mm between odd and even pages and drifts by several millimetres between pages on the same side.
* **The margin is narrow and can be clipped.** Students write markers at the very edge of the page. The start of a marker (for example the `An` of `Ans`) is sometimes cut off at the scan edge.
* **Non-answer pages.** The first pages are a cover page with a handwritten marks grid, and a "Do not write in this page" page. Many trailing pages are blank. Blank pages may show handwriting from the other side of the sheet.

### 3. How Students Write Question Markers

Students identify questions in many ways. Examples of handwritten representations of the same logical question include:

* `10b1`, `10.b.1`, `10 b 1`, `10-b-1`, `10b1)`, `(10 b1)`, `10 (b) (1)`;
* with a prefix: `Ans 10b1`, `Ans. 10(b)(1)`, `Q10b1`, `Q.No 10 b 1`, `QNo10b1)`;
* with the prefix partly cut off at the scan edge, for example `s 1.` instead of `Ans 1.`;
* written on a separate line **above** the answer rather than on the answer's first line;
* over-written or corrected by the student (for example an 8 written over a 9).

Some examinations also use roman-numeral subparts, for example `5(a)(ii)`.

All representations must ultimately be resolved to the **canonical question identifier configured for the examination**, for example `10(b)(1)`.

### 4. Problem

The system needs a reliable mechanism to identify handwritten question markers in scanned answer-script pages automatically. The problem has these aspects:

1. **Detection.** Identify where a handwritten question marker appears on the page.
2. **Recognition.** Read the handwritten digits, letters and punctuation that form the marker.
3. **Normalization.** Handle variations in prefixes, punctuation, spaces, brackets and handwriting.
4. **Resolution.** Determine which valid examination question the recognized marker represents.
5. **Location.** Record the physical location of the marker on the original page.
6. **Answer-start identification.** Determine the point from which the corresponding answer begins.
7. **Uncertainty handling.** Avoid automatically accepting an incorrect question identification.
8. **Rejecting non-marker content.** The margin also contains content that looks like a marker but is not one:
   * valuers' scores and ticks in red or green ink (for example a red `7` or `3½`, where `7` and `3` are also valid question numbers);
   * numbered points inside an answer, written close to the margin rule (for example `(1)`, `(2)`, circled `②`);
   * headings such as `Section B` or a handwritten `Q.No.` column header;
   * crossed-out writing.
9. **Missed markers.** A marker that is never detected never reaches a human reviewer unless the system detects that something is missing at script level.

### 5. Examination Context

The complete set of valid questions for an examination is available **before processing begins**. It is versioned.

This question list is the authoritative vocabulary against which OCR results must be resolved.

For example, if the examination contains:

```text
1
2
3
4(a)
4(b)
5(a)(i)
5(a)(ii)
10(b)(1)
10(b)(2)
```

then the OCR system does not need to read an arbitrary question number from an unrestricted vocabulary. It only needs to identify which configured question a handwritten marker represents, or report that it cannot do so reliably.

Where available, the examination configuration may also state answering rules (for example "answer any 5 of 7"). These can be used to check the result at script level.

### 6. Expected Outcome

#### 6.1 Coordinate System

All coordinates refer to the **canonical page image**: the page's embedded scan at its native resolution. The origin is the top-left corner, x increases to the right, y increases downward, and units are pixels. Each result also records the canonical image's width, height and dpi, so that coordinates can be converted to physical or PDF units.

#### 6.2 Question Start

The **question start** is the point from which the answer is displayed or cut out:

* `question_start_x`: the left edge of the answer area on that page, meaning the margin rule.
* `question_start_y`: the top of the ruled line on which the first line of the answer is written. This is normally the marker's own line. It can be a following line when the marker is written above the answer.

The question start is not the right edge of the OCR crop, and not the right edge of the marker.

#### 6.3 Example Result

```text
Script:          SCRIPT-000123
Page:            P07  (canonical image 1488 x 2572 px, 200 dpi)
Raw OCR:         "Ans 10 b1)"
Normalized:      10|b|1
Canonical ID:    10(b)(1)
Status:          ACCEPTED
Confidence:      0.94

Marker location:
    x = 42
    y = 618
    width  = 95
    height = 33

Question start:
    x = 170        (margin rule)
    y = 604        (top of the ruled line containing the marker)
    method = LANDMARK_LINE
```

### 7. Scope

This component is limited to:

* detecting handwritten question markers in the margin of answer pages;
* recognizing their characters;
* rejecting margin content that is not a marker;
* resolving markers against the examination's valid question list;
* recording marker coordinates;
* determining and saving question-start coordinates;
* classifying each page (blank, template, no marker, or has markers);
* checking consistency at script level (duplicates, order, answer count);
* providing confidence and review information, and accepting human corrections.

It does **not** evaluate the correctness of the student's answer.

### 8. Out of Scope

The following are outside the scope of this component:

* evaluating handwriting or answers;
* semantic interpretation of the answer;
* OCR of the complete answer text;
* generating or publishing examination results;
* replacing human evaluation;
* modifying the unchanged source scan;
* determining where an answer **ends**, and assigning continuation pages to questions. This component supplies the data needed for that (ordered markers and question starts per page); a downstream component does the assignment;
* scanning, debinding, page ordering and script identification from barcodes. These are provided by ingestion.

### 9. Key Design Principles

1. OCR is a **candidate-generation mechanism**, not the final authority.
2. **The examination question list decides which identifiers are valid.** No identifier outside it is ever produced automatically.
3. **A wrong automatic acceptance is worse than a human review.** When in doubt, the system sends the marker to a reviewer.

The authoritative sequence is:

```text
Handwritten Marker
        ↓
OCR Candidate
        ↓
Normalization
        ↓
Resolution against Examination Question List
        ↓
Canonical Question ID
        ↓
Confidence / Validation (page and script level)
        ↓
Accept or Human Review
```

### 10. Assumptions and Open Decisions

| # | Item | Current assumption | Owner |
|---|------|--------------------|-------|
| A1 | Are scripts scanned before or after manual evaluation? | Either may happen. Valuer ink (red or green) may be present and must be suppressed. | Exam operations |
| A2 | Scan colour mode | Colour, at 200 dpi or higher. Grayscale scans disable ink-colour separation. | Exam operations |
| A3 | Booklet templates | A small, known set of booklet templates. Page roles (cover, instructions, answer page) can be configured per template. | Platform |
| A4 | Additional booklets | Ingestion provides one ordered page list per script, including any additional booklets. | Ingestion |
| A5 | Student ink colour | Blue or black. Students do not write in red or green. | Exam rules |
| A6 | Consumer of the question start | The evaluator viewer (to navigate to an answer) and answer segmentation. Its precision needs are those in Requirements §6. | Evaluation platform |
