"""Console report (stdout). Logs go to stderr; this is the human-readable result."""

from __future__ import annotations

import sys
from typing import Optional, TextIO

from qmd.models import MarkerResult, MarkerStatus, ScriptResult


def _fmt_conf(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "  - "


def _marker_line(m: MarkerResult) -> str:
    qid = m.canonical_question_id
    if qid is None and m.candidate_list and m.status != MarkerStatus.DISCARDED:
        qid = f"?{m.candidate_list[0].question_id}"   # best suggestion for the reviewer
    start = (f"({m.question_start_x},{m.question_start_y}) {m.question_start_method.value if m.question_start_method else ''}"
             f" c={_fmt_conf(m.question_start_confidence)}") if m.question_start_x is not None else "-"
    why = ",".join(r.value for r in m.review_reasons) or (m.discard_reason.value if m.discard_reason else "")
    flags = ",".join(f.value for f in m.parse_flags)
    return (f"  p{m.page_index:03d}  {qid or '-':<10} {m.status.value:<15} "
            f"raw={m.raw_ocr_text!r:<14} rec={_fmt_conf(m.recognition_confidence)} "
            f"res={_fmt_conf(m.resolution_confidence)} {m.resolution_method.value:<18} "
            f"box=({m.marker_x},{m.marker_y},{m.marker_width}x{m.marker_height}) start={start}"
            + (f"  [{why}]" if why else "") + (f"  flags={flags}" if flags else ""))


def print_script_report(result: ScriptResult, show_discarded: bool = False, out: Optional[TextIO] = None) -> None:
    out = out or sys.stdout
    w = out.write
    w("\n" + "=" * 110 + "\n")
    w(f"SCRIPT {result.script_id}   pages={result.page_count}   exam={result.exam_id or '-'}   "
      f"question_list={result.question_list_version}\n")
    w(f"source={result.source_pdf}   processing_version={result.processing_version}   "
      f"time={(result.processing_ms or 0) / 1000:.1f}s\n")
    engine = next((m for p in result.pages for m in p.markers), None)
    if engine:
        w(f"ocr={engine.ocr_engine}  model={engine.ocr_model}\n")
    w("Coordinates: CANONICAL_PAGE_PX (native scan pixels, origin top-left)\n")
    w("-" * 110 + "\n")
    w("PAGES\n")
    for p in result.pages:
        layout = p.layout
        lay = (f"rule_x={layout.margin_rule_x} lines={len(layout.ruled_line_ys)} skew={layout.skew_deg:+.2f}"
               if layout and layout.found else ("landmarks NOT FOUND" if layout else ""))
        size = f"{p.page_image_width}x{p.page_image_height}@{p.page_image_dpi:g}dpi" if p.page_image_width else ""
        err = f" ERROR {p.error_code}: {p.error_reason}" if p.error_code else ""
        w(f"  p{p.page_index:03d} {p.page_role.value:<12} {p.processing_status.value:<16} "
          f"{(p.content_class.value if p.content_class else '-'):<8} markers={p.marker_count:<2} {size:<22} {lay}"
          f"{' flags=' + ','.join(p.flags) if p.flags else ''}{err}\n")
    w("-" * 110 + "\n")
    w("MARKERS  (page, question, status, raw OCR, recognition/resolution confidence, method, marker box, answer start)\n")
    shown = 0
    for p in result.pages:
        for m in p.markers:
            if m.status == MarkerStatus.DISCARDED and not show_discarded:
                continue
            w(_marker_line(m) + "\n")
            shown += 1
    if shown == 0:
        w("  (no markers)\n")
    discarded = sum(1 for p in result.pages for m in p.markers if m.status == MarkerStatus.DISCARDED)
    counts: dict[str, int] = {}
    for p in result.pages:
        for m in p.markers:
            counts[m.status.value] = counts.get(m.status.value, 0) + 1
    w("-" * 110 + "\n")
    w("SUMMARY  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
      + (f"   (discarded candidates hidden: {discarded}; use --show-discarded)" if discarded and not show_discarded else "")
      + "\n")
    if result.flags:
        w("SCRIPT FLAGS: " + ", ".join(f.value for f in result.flags) + "\n")
        for d in result.flag_details:
            w(f"  - {d}\n")
    w("=" * 110 + "\n")
    out.flush()
