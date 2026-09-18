"""Command-line entry point.

    qmd run --input ./scans --questions examples/questions/eng221.yaml
    qmd validate-questions examples/questions/eng221.yaml
    qmd show-config

Exit codes: 0 = all scripts processed; 1 = some scripts had failed pages;
2 = configuration / input error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

import yaml

from qmd import __version__
from qmd.config import Settings, load_settings
from qmd.errors import ConfigurationError, OcrEngineError
from qmd.logging_setup import configure_logging
from qmd.models import ScriptFlag
from qmd.question_list import load_question_list
from qmd.reporting import print_script_report

log = logging.getLogger("qmd")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qmd", description="Handwritten question marker detector")
    parser.add_argument("--version", action="version", version=f"qmd {__version__}")
    parser.add_argument("--config", type=Path, help="YAML configuration file (default: config/default.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Process all PDF answer scripts in a folder (or a single PDF)")
    run.add_argument("--input", "-i", type=Path, required=True, help="Folder containing PDF scripts, or one PDF")
    run.add_argument("--questions", "-q", type=Path, required=True, help="Valid question list (.yaml/.json/.txt)")
    run.add_argument("--output", "-o", type=Path, help="Output root folder (overrides app.output_root)")
    run.add_argument("--workers", type=int, help="Parallel page workers (overrides processing.workers)")
    run.add_argument("--backend", choices=["paddle", "onnx"], help="OCR backend (overrides ocr.backend)")
    run.add_argument("--detector", choices=["margin_components", "ocr_model"], help="Candidate detection method")
    run.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    run.add_argument("--show-discarded", action="store_true", help="Also print DISCARDED candidates")
    run.add_argument("--no-debug-images", action="store_true", help="Do not write debug images and crops")

    val = sub.add_parser("validate-questions", help="Validate a question list file and print it")
    val.add_argument("path", type=Path)

    sub.add_parser("show-config", help="Print the effective configuration as YAML")
    return parser


def _overrides(args: argparse.Namespace) -> dict:
    o: dict = {}
    if getattr(args, "output", None):
        o.setdefault("app", {})["output_root"] = str(args.output)
    if getattr(args, "log_level", None):
        o.setdefault("app", {})["log_level"] = args.log_level
    if getattr(args, "no_debug_images", False):
        o.setdefault("app", {}).update(save_debug_images=False, save_marker_crops=False)
    if getattr(args, "workers", None):
        o.setdefault("processing", {})["workers"] = args.workers
    if getattr(args, "backend", None):
        o.setdefault("ocr", {})["backend"] = args.backend
    if getattr(args, "detector", None):
        o.setdefault("detection", {})["method"] = args.detector
    return o


def _merge(settings: Settings, overrides: dict) -> Settings:
    """Apply CLI overrides on top of already-loaded settings (CLI wins over everything)."""
    data = settings.model_dump()
    for section, values in overrides.items():
        data[section].update(values)
    return Settings.model_validate(data)


def _find_pdfs(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".pdf" else []
    return sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")


def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    from qmd.runner import Runner  # heavy imports only when needed

    if not args.input.exists():
        raise ConfigurationError(f"Input path does not exist: {args.input}")
    pdfs = _find_pdfs(args.input)
    if not pdfs:
        raise ConfigurationError(f"No PDF files found in {args.input}")
    questions = load_question_list(args.questions)
    log.info("Processing %d PDF(s) with backend=%s detector=%s workers=%d -> %s", len(pdfs), settings.ocr.backend,
             settings.detection.method, settings.processing.workers, settings.app.output_root)

    exit_code = 0
    with Runner(settings, questions) as runner:
        for pdf in pdfs:
            result = runner.process_script(pdf)
            print_script_report(result, show_discarded=args.show_discarded or settings.app.print_discarded)
            if ScriptFlag.INCOMPLETE in result.flags or result.page_count == 0:
                exit_code = 1
    return exit_code


def cmd_validate(args: argparse.Namespace) -> int:
    ql = load_question_list(args.path)
    print(f"Question list {ql.version_label}: {len(ql.questions)} IDs, {len(ql.targets)} resolution targets")
    for q in sorted(ql.questions.values(), key=lambda q: q.order):
        extra = f"  confusable with {sorted(q.confusable_with)}" if q.confusable_with else ""
        print(f"  {q.canonical:<12} key={q.key:<12} types={'/'.join(q.segment_types):<16} "
              f"target={'yes' if q.is_target else 'no '}{extra}")
    if ql.rules.min_answers is not None or ql.rules.max_answers is not None:
        print(f"  answering rules: min={ql.rules.min_answers} max={ql.rules.max_answers}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        settings = _merge(load_settings(args.config), _overrides(args))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.app.log_level, settings.app.log_format)
    try:
        if args.command == "show-config":
            print(yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=False))
            return 0
        if args.command == "validate-questions":
            return cmd_validate(args)
        return cmd_run(args, settings)
    except (ConfigurationError, OcrEngineError) as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        log.warning("Interrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
