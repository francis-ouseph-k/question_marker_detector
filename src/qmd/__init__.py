"""Question Marker Detector (QMD).

Detects handwritten question markers (for example ``Ans 5.`` or ``Q.No 10(b)``)
in the left margin of scanned answer scripts, resolves them against the
examination's list of valid questions, and estimates where each answer starts.

Source-of-truth documents: Problem Statement, Requirements and Technical Design
(version 1.1). Section references such as "TD §12" in comments refer to the
Technical Design; "FR-xx" / "NFR-xx" refer to the Requirements.
"""

__version__ = "1.0.0"
