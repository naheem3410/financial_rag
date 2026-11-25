"""Precision / recall / coverage / confidently-wrong-rate, overall and by
collision axis (temporal/entity/metric) -- METHODOLOGY_QA.md section G3,
KNOWN_ISSUES.md Phase 2 to-do item "adapt the evaluation harness ... report
results by collision axis."

Deliberately separate from each `run_*_gate.py`'s own `summarize()` --
those already compute a plain exact-match accuracy and an abstention
rate and are left untouched. This module adds the four new, abstention-
aware metrics on top of the same three-way outcome `is_exact_match`
already produces (correct / incorrect / abstained), broken down by axis.

Formulas (confirmed 2026-11-16, METHODOLOGY_QA.md G3):
- precision = correct / (correct + incorrect)
- recall = correct / total
- coverage = (correct + incorrect) / total
- confidently-wrong rate = incorrect / total

Only *scored* records count (verification_status == "verified", i.e.
`exact_match` is not None) -- an item whose gold answer is still pending
human verification has no ground truth to check against, so it is
dropped from every one of these counts rather than silently treated as
"incorrect" or "correct".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from evaluation.gold_lookup import load_gold_index

Outcome = str  # "correct" | "incorrect" | "abstained"


@dataclass(frozen=True)
class AxisMetrics:
    n_total: int
    n_correct: int
    n_incorrect: int
    n_abstained: int
    precision: Optional[float]
    recall: Optional[float]
    coverage: Optional[float]
    confidently_wrong_rate: Optional[float]


@dataclass(frozen=True)
class MetricsReport:
    overall: AxisMetrics
    by_axis: dict[str, AxisMetrics]


def classify_outcome(abstained: bool, exact_match: Optional[bool]) -> Optional[Outcome]:
    """Mirrors `is_exact_match`'s own three-way split -- `is_exact_match`
    already returns False whenever `abstained` is True, so the abstained
    flag must be checked first to tell "abstained" apart from "incorrect"."""
    if exact_match is None:
        return None
    if abstained:
        return "abstained"
    return "correct" if exact_match else "incorrect"


def _metrics_for(outcomes: Sequence[Outcome]) -> AxisMetrics:
    n_total = len(outcomes)
    n_correct = sum(1 for o in outcomes if o == "correct")
    n_incorrect = sum(1 for o in outcomes if o == "incorrect")
    n_abstained = sum(1 for o in outcomes if o == "abstained")
    answered = n_correct + n_incorrect
    return AxisMetrics(
        n_total=n_total,
        n_correct=n_correct,
        n_incorrect=n_incorrect,
        n_abstained=n_abstained,
        precision=(n_correct / answered) if answered else None,
        recall=(n_correct / n_total) if n_total else None,
        coverage=(answered / n_total) if n_total else None,
        confidently_wrong_rate=(n_incorrect / n_total) if n_total else None,
    )


def compute_metrics(
    records: Sequence[Mapping[str, Any]],
    gold_index: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> MetricsReport:
    """`records`: one per scored question, each needing at minimum
    `collision_id`, `abstained` (bool), and `exact_match` (Optional[bool]) --
    the same fields every `run_*_gate.py`'s per-question dataclass already
    carries. `gold_index` defaults to loading the real 366-item gold set
    (`evaluation/gold_lookup.py`); pass an explicit dict in tests."""
    if gold_index is None:
        gold_index = load_gold_index()

    outcomes_by_axis: dict[str, list[Outcome]] = {}
    all_outcomes: list[Outcome] = []
    for record in records:
        collision_id = record["collision_id"]
        outcome = classify_outcome(record["abstained"], record["exact_match"])
        if outcome is None:
            continue  # not yet verified -- excluded from every metric, not counted as any outcome
        if collision_id not in gold_index:
            raise KeyError(f"collision_id {collision_id!r} not found in the gold set")
        axis = gold_index[collision_id]["collision_axis"]
        outcomes_by_axis.setdefault(axis, []).append(outcome)
        all_outcomes.append(outcome)

    return MetricsReport(
        overall=_metrics_for(all_outcomes),
        by_axis={axis: _metrics_for(outcomes) for axis, outcomes in sorted(outcomes_by_axis.items())},
    )
