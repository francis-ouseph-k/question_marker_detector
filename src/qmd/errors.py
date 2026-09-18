"""Exception hierarchy.

Two families matter for retry handling (Requirements FR-16):

* ``RetryableError``  - temporary problems (e.g. a file briefly unavailable).
  The pipeline retries these up to ``processing.max_attempts`` times.
* ``PermanentError``  - problems that will not go away by retrying
  (corrupt PDF page, invalid configuration). Recorded with an error code.

Anything else that escapes a page is treated as permanent, logged with a stack
trace, and recorded as ``UNEXPECTED_ERROR`` so that one bad page never loses the
results of the other pages of the script.
"""

from __future__ import annotations


class QmdError(Exception):
    """Base class for all errors raised by this package."""

    code: str = "QMD_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class ConfigurationError(QmdError):
    """Invalid configuration or question list. Raised at start-up."""

    code = "CONFIGURATION_ERROR"


class QuestionListError(ConfigurationError):
    """The examination question list is invalid (duplicates, collisions...)."""

    code = "QUESTION_LIST_ERROR"


class RetryableError(QmdError):
    code = "RETRYABLE_ERROR"


class PermanentError(QmdError):
    code = "PERMANENT_ERROR"


class PdfReadError(PermanentError):
    code = "PDF_READ_ERROR"


class OcrEngineError(QmdError):
    """The OCR engine could not be created or failed while running."""

    code = "OCR_ENGINE_ERROR"
