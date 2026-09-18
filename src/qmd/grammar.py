"""Marker grammar and normalisation (TD §10, Requirements FR-04).

The recognised text is PARSED, not just stripped of punctuation:

    marker := [prefix-residue] [prefix] [sep] qid [close] [trailing answer text]

Examples (raw OCR -> result):

    "Ans 5."        -> prefix ANS, qid 5
    "s 1."          -> residue "S" (clipped "Ans"), qid 1
    "QNo6)"         -> prefix QNO, qid 6
    "(N.7)"         -> residue "N", qid 7
    "10 (b) (1)"    -> qid 10|b|1
    "Ans 8. From"   -> qid 8, trailing answer text dropped (LINE_TRIMMED)
    "Q.No."         -> PREFIX_ONLY (a header token, no ID)
    "Section B"     -> NOT_A_MARKER

Because part of the prefix may be clipped at the scan edge and read as a digit
("Ans 6" -> "86"), the parser also produces VARIANTS of the ID:

* dropping up to ``max_noise_digits`` leading digits (NOISE_STRIPPED) - only
  when the candidate touches the page's left edge (where clipping happens);
* dropping trailing segments (LINE_TRIMMED), in case answer text was glued on.

The resolver scores every variant; variants carry a penalty so the literal
reading wins when it is valid (TD §12).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from qmd.config import GrammarConfig
from qmd.models import ParseFlag
from qmd.question_list import is_roman

# ASCII only: question markers use English digits and letters (confirmed by the
# exam office; README A-16). Python's \d would also match e.g. Devanagari digits,
# so digits are spelled out as [0-9]. Any other character becomes a one-character
# token and is treated as noise, never as part of the question ID.
_TOKEN_RE = re.compile(r"[0-9]+|[A-Za-z]+|\S")


def _is_digits(tok: str) -> bool:
    return tok.isascii() and tok.isdigit()


def _is_letters(tok: str) -> bool:
    return tok.isascii() and tok.isalpha()


_SEPARATORS = set("().-:_,/[]{}'\"`~;")


class ParseKind(str, Enum):
    MARKER = "MARKER"
    PREFIX_ONLY = "PREFIX_ONLY"
    NOT_A_MARKER = "NOT_A_MARKER"


@dataclass(frozen=True)
class Variant:
    segments: tuple[str, ...]
    noise_stripped: int = 0
    trimmed: int = 0

    @property
    def key(self) -> str:
        return "|".join(self.segments)

    @property
    def flat(self) -> str:
        return "".join(self.segments)


@dataclass
class ParsedMarker:
    raw: str
    kind: ParseKind
    prefix: Optional[str] = None
    residue: str = ""
    segments: list[str] = field(default_factory=list)
    trailing_text: str = ""
    flags: list[ParseFlag] = field(default_factory=list)
    variants: list[Variant] = field(default_factory=list)

    @property
    def parsed_text(self) -> Optional[str]:
        """Human-readable canonical-looking form of the literal reading, e.g. '10(b)(1)'."""
        if not self.segments:
            return None
        return self.segments[0] + "".join(f"({s})" for s in self.segments[1:])

    @property
    def normalized(self) -> Optional[str]:
        return "|".join(self.segments) if self.segments else None


class MarkerGrammar:
    def __init__(self, cfg: GrammarConfig) -> None:
        self.cfg = cfg
        # Longest first so "QNO" wins over "Q".
        self.prefixes = sorted({p.replace(".", "").replace(" ", "").upper() for p in cfg.prefixes}, key=len, reverse=True)

    # ------------------------------------------------------------------ #

    def parse(self, raw: str, allow_noise_strip: bool = False) -> ParsedMarker:
        text = unicodedata.normalize("NFKC", raw or "").strip()
        tokens = _TOKEN_RE.findall(text)
        first_digit = next((i for i, t in enumerate(tokens) if _is_digits(t)), None)

        if first_digit is None:
            letters = "".join(t for t in tokens if _is_letters(t)).upper()
            prefix, _ = self._match_prefix(letters)
            kind = ParseKind.PREFIX_ONLY if prefix else ParseKind.NOT_A_MARKER
            return ParsedMarker(raw=raw, kind=kind, prefix=prefix)

        head_letters = "".join(t for t in tokens[:first_digit] if _is_letters(t)).upper()
        prefix, residue = self._match_prefix(head_letters)
        flags: list[ParseFlag] = []
        if head_letters and prefix is None:
            if len(head_letters) > self.cfg.max_prefix_residue_chars:
                return ParsedMarker(raw=raw, kind=ParseKind.NOT_A_MARKER)  # e.g. "Section 2"
            residue = head_letters
        if residue:
            flags.append(ParseFlag.PREFIX_RESIDUE)

        first = tokens[first_digit]
        if len(first) > self.cfg.max_first_number_digits + self.cfg.max_noise_digits:
            return ParsedMarker(raw=raw, kind=ParseKind.NOT_A_MARKER)  # e.g. a phone/registration number

        segments = [first]
        j = first_digit + 1
        while j < len(tokens):
            tok = tokens[j]
            if tok in _SEPARATORS:
                j += 1
                continue
            if _is_digits(tok) and len(tok) <= self.cfg.max_sub_number_digits:
                segments.append(tok)
            elif _is_letters(tok) and (len(tok) == 1 or is_roman(tok)):
                segments.append(tok.lower())
            else:
                break
            j += 1
        trailing = "".join(tokens[j:])
        if any(ch.isalnum() for ch in trailing):
            flags.append(ParseFlag.LINE_TRIMMED)

        parsed = ParsedMarker(
            raw=raw, kind=ParseKind.MARKER, prefix=prefix, residue=residue,
            segments=segments, trailing_text=trailing, flags=flags,
        )
        parsed.variants = self._variants(segments, allow_noise_strip)
        if len(segments[0]) > self.cfg.max_first_number_digits and not any(v.noise_stripped for v in parsed.variants):
            return ParsedMarker(raw=raw, kind=ParseKind.NOT_A_MARKER)
        return parsed

    # ------------------------------------------------------------------ #

    def _match_prefix(self, letters: str) -> tuple[Optional[str], str]:
        """Return (prefix, residue). Residue = unknown letters before the prefix."""
        if not letters:
            return None, ""
        for p in self.prefixes:
            if letters == p:
                return p, ""
        for p in self.prefixes:
            if letters.endswith(p) and len(letters) - len(p) <= self.cfg.max_prefix_residue_chars:
                return p, letters[: len(letters) - len(p)]
        return None, ""

    def _variants(self, segments: list[str], allow_noise_strip: bool) -> list[Variant]:
        first, rest = segments[0], segments[1:]
        max_strip = min(self.cfg.max_noise_digits, len(first) - 1) if allow_noise_strip else 0
        variants: list[Variant] = []
        for strip in range(0, max_strip + 1):
            head = first[strip:]
            for trim in range(0, len(rest) + 1):
                kept = rest[: len(rest) - trim]
                variants.append(Variant(segments=(head, *kept), noise_stripped=strip, trimmed=trim))
        return variants
