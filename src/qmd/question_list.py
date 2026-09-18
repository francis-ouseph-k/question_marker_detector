"""Examination question list (TD §11, Requirements §2.2).

The question list is the AUTHORITATIVE vocabulary: the resolver never produces
an identifier that is not in it (FR-05).

Supported file formats
----------------------
* ``.yaml`` / ``.yml`` / ``.json``::

      exam_id: ENG221-ESE-2023
      version: "2023-05-05"          # optional; a content hash is used if missing
      questions:
        - "1"
        - "4(a)"
        - id: "10(b)"
          target: true               # allow markers to resolve to this parent
        - "10(b)(1)"
      answering_rules:               # optional (Requirements FR-17)
        min_answers: 5
        max_answers: 8

* ``.txt``: one canonical ID per line; ``#`` starts a comment.

Canonical IDs are written as ``NUMBER`` followed by ``(segment)`` groups, e.g.
``10(b)(1)``, ``5(a)(ii)``. Other separators (``10.b.1``) are accepted on input
and converted to the canonical form.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from qmd.errors import QuestionListError

log = logging.getLogger(__name__)

_ROMAN_RE = re.compile(r"^(?:x{0,3})(?:ix|iv|v?i{0,3})$")
_TOKEN_RE = re.compile(r"[0-9]+|[A-Za-z]+")  # ASCII only (README A-16)
_ALLOWED_ID_RE = re.compile(r"^[0-9A-Za-z()\[\].\-\s]+$")


def is_roman(text: str) -> bool:
    return bool(text) and bool(_ROMAN_RE.match(text.lower()))


def split_segments(raw_id: str) -> list[str]:
    """Split an ID like '10(b)(1)' or '10.b.1' into ['10', 'b', '1'] (letters lower-cased)."""
    return [t.lower() for t in _TOKEN_RE.findall(raw_id)]


def canonical_form(segments: list[str]) -> str:
    return segments[0] + "".join(f"({s})" for s in segments[1:])


@dataclass
class Question:
    canonical: str
    segments: list[str]
    segment_types: list[str]      # "NUM" | "ALPHA" | "ROMAN"
    key: str                      # "10|b|1"
    flat: str                     # "10b1"
    is_target: bool
    order: int                    # position in the file (for order checks)
    confusable_with: set[str] = field(default_factory=set)


@dataclass
class AnsweringRules:
    min_answers: Optional[int] = None
    max_answers: Optional[int] = None


@dataclass
class QuestionList:
    exam_id: Optional[str]
    version: str
    questions: dict[str, Question]           # canonical -> Question
    rules: AnsweringRules
    by_key: dict[str, Question] = field(default_factory=dict)

    @property
    def targets(self) -> list[Question]:
        return [q for q in self.questions.values() if q.is_target]

    @property
    def version_label(self) -> str:
        return f"{self.exam_id}:{self.version}" if self.exam_id else self.version

    def get(self, canonical: str) -> Optional[Question]:
        return self.questions.get(canonical)


# --------------------------------------------------------------------------- #


def load_question_list(path: Path) -> QuestionList:
    """Load, validate and index a question list file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise QuestionListError(f"Cannot read question list {path}: {exc}") from exc
    content_hash = hashlib.sha256(text.encode()).hexdigest()[:12]
    suffix = path.suffix.lower()
    if suffix == ".txt":
        entries: list[Any] = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
        entries = [e for e in entries if e]
        data: dict[str, Any] = {"questions": entries}
    elif suffix in (".yaml", ".yml", ".json"):
        try:
            data = json.loads(text) if suffix == ".json" else yaml.safe_load(text)
        except (ValueError, yaml.YAMLError) as exc:
            raise QuestionListError(f"Question list {path} is not valid {suffix[1:].upper()}: {exc}") from exc
        if not isinstance(data, dict) or "questions" not in data:
            raise QuestionListError(f"Question list {path} must be a mapping with a 'questions' list")
    else:
        raise QuestionListError(f"Unsupported question list format '{suffix}' (use .yaml, .json or .txt)")
    return build_question_list(
        data.get("questions") or [],
        exam_id=data.get("exam_id"),
        version=str(data.get("version") or f"sha256:{content_hash}"),
        rules=data.get("answering_rules") or {},
    )


def build_question_list(entries: list[Any], exam_id: Optional[str] = None, version: str = "unversioned",
                        rules: Optional[dict] = None) -> QuestionList:
    if not entries:
        raise QuestionListError("Question list is empty")

    # 1. Parse entries -> (segments, explicit_target)
    parsed: list[tuple[list[str], Optional[bool], str]] = []
    for entry in entries:
        if isinstance(entry, dict):
            raw = str(entry.get("id", "")).strip()
            explicit = entry.get("target")
            if explicit is not None and not isinstance(explicit, bool):
                raise QuestionListError(f"'target' must be true/false for question {raw!r}")
        else:
            raw, explicit = str(entry).strip(), None
        segments = split_segments(raw)
        if not segments or not (segments[0].isascii() and segments[0].isdigit()):
            raise QuestionListError(f"Invalid question ID {raw!r}: must start with a number")
        if not _ALLOWED_ID_RE.match(raw):
            raise QuestionListError(f"Invalid characters in question ID {raw!r}")
        parsed.append((segments, explicit, raw))

    # 2. Duplicates (after normalisation)
    seen: dict[str, str] = {}
    for segments, _, raw in parsed:
        key = "|".join(segments)
        if key in seen:
            raise QuestionListError(f"Duplicate question ID {raw!r} (same as {seen[key]!r})")
        seen[key] = raw

    # 3. Hierarchy: an ID that is a prefix of another is a parent.
    parents = {"|".join(s[:i]) for s, _, _ in parsed for i in range(1, len(s))}

    # 4. Segment types (roman numerals are recognised per sibling group).
    siblings: dict[tuple[str, int], list[str]] = {}
    for segments, _, _ in parsed:
        for depth in range(1, len(segments)):
            siblings.setdefault(("|".join(segments[:depth]), depth), []).append(segments[depth])

    questions: dict[str, Question] = {}
    for order, (segments, explicit, raw) in enumerate(parsed):
        types = []
        for depth, seg in enumerate(segments):
            if seg.isdigit():
                types.append("NUM")
                continue
            group = siblings.get(("|".join(segments[:depth]), depth), [seg])
            roman_group = all(is_roman(s) for s in group if s.isalpha()) and any(len(s) > 1 for s in group)
            if len(seg) > 1 and not is_roman(seg):
                raise QuestionListError(f"Invalid segment {seg!r} in {raw!r}: letters must be one letter or a roman numeral")
            types.append("ROMAN" if roman_group or len(seg) > 1 else "ALPHA")
        key = "|".join(segments)
        is_parent = key in parents
        is_target = explicit if explicit is not None else not is_parent
        canonical = canonical_form(segments)
        questions[canonical] = Question(
            canonical=canonical, segments=segments, segment_types=types, key=key,
            flat="".join(segments), is_target=is_target, order=order,
        )

    # 5. Flattened collisions, e.g. "1(1)" vs "11": text alone cannot tell them apart.
    by_flat: dict[str, list[Question]] = {}
    for q in questions.values():
        by_flat.setdefault(q.flat, []).append(q)
    for same_flat in by_flat.values():
        if len(same_flat) > 1:
            names = {q.canonical for q in same_flat}
            for q in same_flat:
                q.confusable_with = names - {q.canonical}
            log.warning("Question IDs %s are confusable (same characters); they will never be auto-accepted "
                        "on text evidence alone", sorted(names))

    if not any(q.is_target for q in questions.values()):
        raise QuestionListError("Question list has no resolution targets")

    rules = rules or {}
    try:
        answering = AnsweringRules(
            min_answers=int(rules["min_answers"]) if rules.get("min_answers") is not None else None,
            max_answers=int(rules["max_answers"]) if rules.get("max_answers") is not None else None,
        )
    except (TypeError, ValueError) as exc:
        raise QuestionListError(f"Invalid answering_rules: {exc}") from exc
    if answering.min_answers is not None and answering.max_answers is not None \
            and answering.min_answers > answering.max_answers:
        raise QuestionListError("answering_rules.min_answers must be <= max_answers")

    qlist = QuestionList(exam_id=exam_id, version=version, questions=questions, rules=answering)
    qlist.by_key = {q.key: q for q in questions.values()}
    log.info("Loaded question list %s: %d IDs (%d resolution targets)",
             qlist.version_label, len(questions), len(qlist.targets))
    return qlist
