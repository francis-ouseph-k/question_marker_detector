"""Logging setup.

* Console (stderr) handler, text or JSON, level from ``app.log_level``.
* Optional per-script file handler (``<output>/<script>/processing.log``) so that
  the full processing history of one script sits next to its results.

Console *results* (the marker table) are printed to stdout by ``reporting.py``;
logs go to stderr. This keeps results easy to pipe into other tools.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

_CONSOLE_HANDLER_NAME = "qmd-console"
_TEXT_FORMAT = "%(asctime)s %(levelname)-7s [%(processName)s] %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    """One JSON object per line; easy to ship to a log platform later."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "process": record.processName,
            "msg": record.getMessage(),
        }
        for key in ("script_id", "page_index"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _formatter(fmt: str) -> logging.Formatter:
    return JsonFormatter() if fmt == "json" else logging.Formatter(_TEXT_FORMAT)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Configure the root logger once per process (safe to call again)."""
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if handler.get_name() == _CONSOLE_HANDLER_NAME:
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.set_name(_CONSOLE_HANDLER_NAME)
    handler.setFormatter(_formatter(fmt))
    root.addHandler(handler)
    # Third-party libraries are very chatty at INFO.
    for noisy in ("ppocr", "paddle", "paddlex", "RapidOCR", "rapidocr", "PIL"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


def add_file_handler(path: Path, fmt: str = "text") -> logging.Handler:
    """Attach a file handler to the root logger; caller must remove it later."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setFormatter(_formatter(fmt))
    handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
    return handler


def remove_handler(handler: logging.Handler) -> None:
    logging.getLogger().removeHandler(handler)
    handler.close()
