"""Script orchestration: pages -> page jobs -> consolidation -> outputs.

This is the local, command-line equivalent of the queue + workers + script
consolidation job of TD §2 / §23. Replacing it with a real queue later means
replacing this module only; ``PageProcessor`` and everything below it stay.

* ``workers == 1`` runs pages in-process (simplest to debug).
* ``workers > 1``  runs pages in a process pool; each worker process loads the
  OCR models once (``_worker_init``). Threads per worker come from
  ``ocr.threads_per_worker`` (TD §21: workers x threads ~= physical cores).
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from typing import Optional

from qmd import __version__
from qmd.artefacts import atomic_write_json
from qmd.config import Settings
from qmd.consolidation import consolidate
from qmd.errors import PermanentError, QmdError, RetryableError
from qmd.logging_setup import add_file_handler, configure_logging, remove_handler
from qmd.models import PageResult, PageRole, ProcessingStatus, ScriptResult
from qmd.ocr.base import EngineInfo
from qmd.pdf_loader import count_pages, file_sha256
from qmd.pipeline import PageProcessor, page_id_for, utc_now
from qmd.question_list import QuestionList

log = logging.getLogger(__name__)

# One PageProcessor per worker process (set by _worker_init).
_WORKER: Optional[PageProcessor] = None


def _worker_init(settings: Settings, questions: QuestionList) -> None:
    global _WORKER
    configure_logging(settings.app.log_level, settings.app.log_format)
    _WORKER = PageProcessor(settings, questions)


def _run_page_job(job: tuple[Path, str, int, Path, int]) -> tuple[PageResult, EngineInfo]:
    assert _WORKER is not None, "worker not initialised"
    return run_page_with_retry(_WORKER, *job), _WORKER.engine_info


def run_page_with_retry(processor: PageProcessor, pdf_path: Path, script_id: str, page_index: int,
                        out_dir: Path, max_attempts: int) -> PageResult:
    """Run one page; retry temporary failures; never raise (FR-16)."""
    last_error: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return processor.process_page(pdf_path, script_id, page_index, out_dir)
        except (RetryableError, OSError) as exc:
            last_error = exc
            log.warning("page %d attempt %d/%d failed (retryable): %s", page_index, attempt, max_attempts, exc)
            time.sleep(min(2.0, 0.2 * attempt))
        except PermanentError as exc:
            log.error("page %d failed permanently: %s", page_index, exc)
            return _failed_page(processor, script_id, page_index, exc.code, str(exc), ProcessingStatus.FAILED_PERMANENT)
        except Exception as exc:  # noqa: BLE001 - one bad page must not stop the script
            log.exception("page %d: unexpected error", page_index)
            return _failed_page(processor, script_id, page_index, "UNEXPECTED_ERROR", repr(exc),
                                ProcessingStatus.FAILED_PERMANENT)
    code = last_error.code if isinstance(last_error, QmdError) else "RETRY_EXHAUSTED"
    return _failed_page(processor, script_id, page_index, code, str(last_error), ProcessingStatus.FAILED_RETRYABLE)


def _failed_page(processor: PageProcessor, script_id: str, page_index: int, code: str, reason: str,
                 status: ProcessingStatus) -> PageResult:
    role = processor.s.booklet.page_roles.get(page_index, processor.s.booklet.default_role)
    return PageResult(script_id=script_id, page_id=page_id_for(script_id, page_index), page_index=page_index,
                      page_role=PageRole(role), processing_status=status, error_code=code, error_reason=reason,
                      processing_version=processor.processing_version)


def script_id_for(pdf_path: Path) -> str:
    """Stable script identifier. Ingestion would normally supply it (README A-2)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", pdf_path.stem)


class Runner:
    def __init__(self, settings: Settings, questions: QuestionList) -> None:
        self.settings = settings
        self.questions = questions
        self.workers = settings.processing.workers
        self._pool: Optional[ProcessPoolExecutor] = None
        self._local: Optional[PageProcessor] = None
        self._engine_info: Optional[EngineInfo] = None
        if self.workers > 1:
            # "spawn" avoids forking a process that already holds ML runtime threads.
            self._pool = ProcessPoolExecutor(max_workers=self.workers, mp_context=get_context("spawn"),
                                             initializer=_worker_init, initargs=(settings, questions))
        else:
            self._local = PageProcessor(settings, questions)
            self._engine_info = self._local.engine_info

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def __enter__(self) -> Runner:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #

    def process_script(self, pdf_path: Path) -> ScriptResult:
        s = self.settings
        script_id = script_id_for(pdf_path)
        out_dir = Path(s.app.output_root) / script_id
        out_dir.mkdir(parents=True, exist_ok=True)
        file_handler = add_file_handler(out_dir / "processing.log", s.app.log_format)
        started = time.perf_counter()
        try:
            log.info("=== script %s (%s) ===", script_id, pdf_path)
            result = ScriptResult(
                script_id=script_id, source_pdf=str(pdf_path), source_sha256=file_sha256(pdf_path),
                page_count=0, processing_version=s.processing_version(),
                question_list_version=self.questions.version_label, exam_id=self.questions.exam_id,
                started_at=utc_now(),
            )
            try:
                result.page_count = count_pages(pdf_path)
            except PermanentError as exc:
                log.error("cannot open %s: %s", pdf_path, exc)
                result.flag_details.append(f"PDF could not be opened: {exc}")
                return self._finish(result, out_dir, started)

            jobs = [(pdf_path, script_id, i, out_dir, s.processing.max_attempts) for i in range(1, result.page_count + 1)]
            if self._pool is not None:
                for page, info in self._pool.map(_run_page_job, jobs):
                    result.pages.append(page)
                    self._engine_info = info
            else:
                assert self._local is not None
                for job in jobs:
                    result.pages.append(run_page_with_retry(self._local, *job))

            result.pages.sort(key=lambda p: p.page_index)
            consolidate(result, self.questions)
            return self._finish(result, out_dir, started)
        finally:
            remove_handler(file_handler)

    def _finish(self, result: ScriptResult, out_dir: Path, started: float) -> ScriptResult:
        result.completed_at = utc_now()
        result.processing_ms = int((time.perf_counter() - started) * 1000)
        atomic_write_json(out_dir / "script_result.json", result.model_dump(mode="json"))
        atomic_write_json(out_dir / "run_manifest.json", self._manifest(result))
        log.info("script %s done in %.1f s -> %s", result.script_id, result.processing_ms / 1000, out_dir)
        return result

    def _manifest(self, result: ScriptResult) -> dict:
        info = self._engine_info
        return {
            "qmd_version": __version__,
            "script_id": result.script_id,
            "source_pdf": result.source_pdf,
            "source_sha256": result.source_sha256,
            "processing_version": result.processing_version,
            "question_list_version": result.question_list_version,
            "ocr_engine": info.ocr_engine if info else None,
            "ocr_model": info.ocr_model if info else None,
            "ocr_model_version": info.ocr_model_version if info else None,
            "confidence_calibrated": self.settings.ocr.calibration_file is not None,
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "processing_ms": result.processing_ms,
            "config": self.settings.model_dump(mode="json"),
        }
