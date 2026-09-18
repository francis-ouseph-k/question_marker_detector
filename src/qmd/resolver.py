"""Constrained question resolution (TD §12, Requirements FR-05/06/07).

The resolver ONLY ranks identifiers from the examination question list; it can
never invent one.

Algorithm
---------
For every parse variant (see ``grammar.py``) and every resolution target:

    similarity = 1 - weighted_edit_distance(variant, target) / max(len)
    score      = similarity - variant_penalty

* Exact key match (``10|b|1`` == ``10|b|1``) -> similarity 1.0.
* Weighted edit distance uses a configurable OCR confusion table, e.g. '6'->'b'
  is cheap (0.3) and deleting a trailing '1' that was really ')' is cheap (0.4).
  Because costs are looked up against the TARGET character, a '6' in a position
  where the target has a letter is cheaply read as 'b' ("position-typed
  coercion"), while '6' where the target expects a digit is a full substitution.

Decision (TD §12.4):

    EXACT_UNIQUE        best target matched a variant exactly, no tie
    CONSTRAINED_UNIQUE  best score >= match_threshold and margin to 2nd >= margin_threshold
    AMBIGUOUS           best >= match_threshold but margin too small   -> review
    NO_MATCH            best  < match_threshold                        -> UNRESOLVED
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from qmd.config import ResolutionConfig
from qmd.grammar import ParsedMarker, Variant
from qmd.models import Candidate, ParseFlag, ResolutionMethod
from qmd.question_list import Question, QuestionList


class Outcome(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"


@dataclass
class Resolution:
    outcome: Outcome
    method: ResolutionMethod
    question: Optional[Question]
    confidence: float
    margin: float
    candidates: list[Candidate] = field(default_factory=list)
    variant: Optional[Variant] = None
    flags: list[ParseFlag] = field(default_factory=list)

    @property
    def is_confusable(self) -> bool:
        return bool(self.question and self.question.confusable_with)


@dataclass
class _Best:
    score: float
    exact: bool
    variant: Variant


class QuestionResolver:
    def __init__(self, questions: QuestionList, cfg: ResolutionConfig) -> None:
        self.questions = questions
        self.cfg = cfg
        self._sub: dict[tuple[str, str], float] = {}
        self._del: dict[str, float] = {}
        for ocr_char, expected, cost in cfg.confusions:
            if expected == "":
                self._del[ocr_char.lower()] = min(cost, self._del.get(ocr_char.lower(), cost))
            else:
                key = (ocr_char.lower(), expected.lower())
                self._sub[key] = min(cost, self._sub.get(key, cost))

    # ------------------------------------------------------------------ #

    def resolve(self, parsed: ParsedMarker) -> Resolution:
        best_by_q: dict[str, _Best] = {}
        for variant in parsed.variants:
            penalty = variant.noise_stripped * self.cfg.noise_penalty + variant.trimmed * self.cfg.trim_penalty
            for q in self.questions.targets:
                exact = variant.key == q.key
                sim = 1.0 if exact else self.similarity(variant.flat, q.flat)
                score = sim - penalty
                prev = best_by_q.get(q.canonical)
                if prev is None or score > prev.score or (score == prev.score and exact and not prev.exact):
                    best_by_q[q.canonical] = _Best(score=score, exact=exact, variant=variant)

        if not best_by_q:
            return Resolution(Outcome.NO_MATCH, ResolutionMethod.NONE, None, 0.0, 0.0)

        ranked = sorted(best_by_q.items(), key=lambda kv: (kv[1].score, kv[1].exact), reverse=True)
        candidates = [Candidate(question_id=cid, score=round(max(b.score, 0.0), 4)) for cid, b in ranked[:5]]
        best_id, best = ranked[0]
        second_score = ranked[1][1].score if len(ranked) > 1 else 0.0
        margin = max(0.0, best.score - second_score)
        question = self.questions.get(best_id)
        flags = self._variant_flags(best.variant)
        confidence = max(0.0, min(1.0, best.score))

        tie = len(ranked) > 1 and ranked[1][1].exact and ranked[1][1].score == best.score
        if best.exact and not tie:
            return Resolution(Outcome.RESOLVED, ResolutionMethod.EXACT_UNIQUE, question, confidence,
                              margin, candidates, best.variant, flags)
        if best.score >= self.cfg.match_threshold and margin >= self.cfg.margin_threshold:
            return Resolution(Outcome.RESOLVED, ResolutionMethod.CONSTRAINED_UNIQUE, question, confidence,
                              margin, candidates, best.variant, flags)
        if best.score >= self.cfg.match_threshold:
            return Resolution(Outcome.AMBIGUOUS, ResolutionMethod.NONE, None, confidence, margin,
                              candidates, best.variant, flags)
        return Resolution(Outcome.NO_MATCH, ResolutionMethod.NONE, None, confidence, margin, candidates,
                          best.variant, flags)

    # ------------------------------------------------------------------ #

    def similarity(self, observed: str, target: str) -> float:
        if not observed and not target:
            return 1.0
        dist = self.weighted_distance(observed.lower(), target.lower())
        return max(0.0, 1.0 - dist / max(len(observed), len(target), 1))

    def weighted_distance(self, observed: str, target: str) -> float:
        """Weighted Levenshtein distance (observed OCR text -> expected target)."""
        n, m = len(observed), len(target)
        prev = [j * self.cfg.insert_cost for j in range(m + 1)]
        for i in range(1, n + 1):
            cur = [prev[0] + self._delete_cost(observed[i - 1])] + [0.0] * m
            for j in range(1, m + 1):
                o, t = observed[i - 1], target[j - 1]
                sub = 0.0 if o == t else self._sub.get((o, t), self.cfg.default_substitute_cost)
                cur[j] = min(prev[j] + self._delete_cost(o), cur[j - 1] + self.cfg.insert_cost, prev[j - 1] + sub)
            prev = cur
        return prev[m]

    def _delete_cost(self, ch: str) -> float:
        return self._del.get(ch, self.cfg.delete_cost)

    @staticmethod
    def _variant_flags(variant: Variant) -> list[ParseFlag]:
        flags = []
        if variant.noise_stripped:
            flags.append(ParseFlag.NOISE_STRIPPED)
        if variant.trimmed:
            flags.append(ParseFlag.LINE_TRIMMED)
        return flags
