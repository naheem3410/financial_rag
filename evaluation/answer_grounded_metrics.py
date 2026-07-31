"""Answer-grounded retrieval metrics — a second lens on retrieval quality,
alongside `evaluation/retrieval_metrics.py`'s page-based Hit Rate/MRR/NDCG.

Why this exists: the existing gate's `is_relevant` (retrieval_metrics.py)
is page-based: a retrieved proposition counts as a "hit" if it shares a
(source_uri, page) with any gold evidence reference, regardless of whether
that specific proposition actually states the requested value -- a
multi-metric question's gold page can carry dozens of line items, so a
page-level hit can fire on a completely unrelated number from the same
page. For C1/C2 specifically (`PassthroughQueryTransformer`: one combined,
unsplit query per question, however many metrics it asks for), the
practically meaningful question is narrower and cheaper to satisfy: does
the reranked window contain, verbatim, at least one of the actual
requested answers -- not "the right page showed up," but "at least one of
the numbers the question needs is actually there." This is the natural
retrieval-quality bar for an unsplit query, since C1/C2's single shared
window is exactly the capacity constraint a multi-metric question runs
into -- "did anything useful survive" is the first, weaker question worth
asking before "did everything survive."

Relevance here is verbatim numeric presence, not page co-location: a
proposition counts as relevant to one of the question's gold answers only
if that exact value (number-boundary-matched, e.g. "3,231" must not match
inside "13,231") appears in the proposition's own text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from contracts.models import Proposition


def _format_gold_value(value: float) -> str:
    """Comma-grouped for the integer part, matching how these figures are
    always printed in the source filings ("$2,266 million", never
    "$2266 million"). Handles negative values via a leading minus sign --
    note the filings themselves usually print negatives as "(2,266)", not
    "-2,266", so a value stated only in parenthetical form in the source
    text won't match this formatter; that's a known, narrow gap, not
    silently claimed as covered."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,}"


def _value_pattern(value: float) -> re.Pattern:
    needle = re.escape(_format_gold_value(value))
    return re.compile(r"(?<![\d,.])" + needle + r"(?![\d.])")


def gold_values_from_collision_record(record: dict) -> list[float]:
    """Every non-null value in `gold_answers` (one per requested metric),
    raw as printed in the filing -- no scale conversion needed since the
    proposition text states the number in the same printed form."""
    return [v for v in record.get("gold_answers", {}).values() if v is not None]


def is_answer_present(proposition: Proposition, gold_values: Sequence[float]) -> bool:
    """True if `proposition.text` verbatim-contains (number-boundary-
    matched) at least one of `gold_values`."""
    return any(_value_pattern(v).search(proposition.text) for v in gold_values)


def any_answer_hit_rate_at_k(
    ranked_propositions: Sequence[Proposition], gold_values: Sequence[float], k: int
) -> float:
    """1.0 if at least one of the top-k propositions states at least one of
    the question's gold answers -- the weak bar: *something* useful
    survived. 0.0 if none of the gold values appear anywhere in the top-k."""
    if not gold_values:
        return 0.0
    return 1.0 if any(is_answer_present(p, gold_values) for p in ranked_propositions[:k]) else 0.0


def all_answers_hit_rate_at_k(
    ranked_propositions: Sequence[Proposition], gold_values: Sequence[float], k: int
) -> float:
    """1.0 only if *every* one of the question's gold answers is stated by
    at least one proposition somewhere in the top-k -- the strong bar: the
    single shared window has room for the complete answer. This is the
    capacity constraint itself, made directly measurable rather than
    inferred from a lower page-level hit rate."""
    if not gold_values:
        return 0.0
    window = ranked_propositions[:k]
    return 1.0 if all(any(is_answer_present(p, [v]) for p in window) for v in gold_values) else 0.0


def answer_reciprocal_rank(ranked_propositions: Sequence[Proposition], gold_values: Sequence[float]) -> float:
    """1 / (1-indexed rank of the first proposition stating any gold
    value); 0.0 if none do."""
    for rank, proposition in enumerate(ranked_propositions, start=1):
        if is_answer_present(proposition, gold_values):
            return 1.0 / rank
    return 0.0
