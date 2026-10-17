"""Answer-grounded retrieval metrics — second lens alongside retrieval_metrics.py's
page-based Hit Rate/MRR/NDCG.

Page-level hits can fire on an unrelated number from the same page, so for
C1/C2's single unsplit query this checks verbatim gold-value presence
(number-boundary-matched) in the window instead — a stricter, more honest bar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from contracts.models import Proposition


def _format_gold_value(value: float) -> str:
    """Comma-grouped like the filings print it ("2,266" not "2266").
    Negatives use a leading minus; filings' "(2,266)" form won't match — known gap."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,}"


def _value_pattern(value: float) -> re.Pattern:
    needle = re.escape(_format_gold_value(value))
    return re.compile(r"(?<![\d,.])" + needle + r"(?![\d.])")


def gold_values_from_collision_record(record: dict) -> list[float]:
    """Non-null values from gold_answers, raw as printed — no scale conversion needed."""
    return [v for v in record.get("gold_answers", {}).values() if v is not None]


def is_answer_present(proposition: Proposition, gold_values: Sequence[float]) -> bool:
    """True if proposition.text verbatim-contains (boundary-matched) a gold value."""
    return any(_value_pattern(v).search(proposition.text) for v in gold_values)


def any_answer_hit_rate_at_k(
    ranked_propositions: Sequence[Proposition], gold_values: Sequence[float], k: int
) -> float:
    """Weak bar: 1.0 if anything useful survived in the top-k, else 0.0."""
    if not gold_values:
        return 0.0
    return 1.0 if any(is_answer_present(p, gold_values) for p in ranked_propositions[:k]) else 0.0


def all_answers_hit_rate_at_k(
    ranked_propositions: Sequence[Proposition], gold_values: Sequence[float], k: int
) -> float:
    """Strong bar: 1.0 only if every gold answer is covered somewhere in the top-k window."""
    if not gold_values:
        return 0.0
    window = ranked_propositions[:k]
    return 1.0 if all(any(is_answer_present(p, [v]) for p in window) for v in gold_values) else 0.0


def answer_reciprocal_rank(ranked_propositions: Sequence[Proposition], gold_values: Sequence[float]) -> float:
    """1 / rank of first proposition stating any gold value; 0.0 if none do."""
    for rank, proposition in enumerate(ranked_propositions, start=1):
        if is_answer_present(proposition, gold_values):
            return 1.0 / rank
    return 0.0
