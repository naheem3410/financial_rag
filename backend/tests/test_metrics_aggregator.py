"""metrics_aggregator.py tests -- a small synthetic set covering all three
collision axes and all three outcomes (correct/incorrect/abstained), hand-
verified against METHODOLOGY_QA.md G3's four formulas by independent
arithmetic, not just "the code agrees with itself."

Synthetic set:
  temporal: correct, correct, incorrect, abstained   -> total 4
  entity:   correct, abstained                        -> total 2
  metric:   incorrect                                 -> total 1
  overall:                                             -> total 7

By hand:
  temporal: correct=2 incorrect=1 abstained=1 answered=3
    precision=2/3=0.6666..., recall=2/4=0.5, coverage=3/4=0.75, cw=1/4=0.25
  entity: correct=1 incorrect=0 abstained=1 answered=1
    precision=1/1=1.0, recall=1/2=0.5, coverage=1/2=0.5, cw=0/2=0.0
  metric: correct=0 incorrect=1 abstained=0 answered=1
    precision=0/1=0.0, recall=0/1=0.0, coverage=1/1=1.0, cw=1/1=1.0
  overall: correct=3 incorrect=2 abstained=2 answered=5 total=7
    precision=3/5=0.6, recall=3/7=0.428571..., coverage=5/7=0.714285..., cw=2/7=0.285714...
"""
from __future__ import annotations

from evaluation.metrics_aggregator import classify_outcome, compute_metrics

GOLD_INDEX = {
    "t1": {"collision_axis": "temporal"},
    "t2": {"collision_axis": "temporal"},
    "t3": {"collision_axis": "temporal"},
    "t4": {"collision_axis": "temporal"},
    "e1": {"collision_axis": "entity"},
    "e2": {"collision_axis": "entity"},
    "m1": {"collision_axis": "metric"},
}


def _rec(collision_id, abstained, exact_match):
    return {"collision_id": collision_id, "abstained": abstained, "exact_match": exact_match}


RECORDS = [
    _rec("t1", False, True),   # temporal correct
    _rec("t2", False, True),   # temporal correct
    _rec("t3", False, False),  # temporal incorrect
    _rec("t4", True, False),   # temporal abstained
    _rec("e1", False, True),   # entity correct
    _rec("e2", True, False),   # entity abstained
    _rec("m1", False, False),  # metric incorrect
]


# --- classify_outcome ---


def test_classify_outcome_correct():
    assert classify_outcome(abstained=False, exact_match=True) == "correct"


def test_classify_outcome_incorrect():
    assert classify_outcome(abstained=False, exact_match=False) == "incorrect"


def test_classify_outcome_abstained_even_if_exact_match_is_false():
    assert classify_outcome(abstained=True, exact_match=False) == "abstained"


def test_classify_outcome_none_when_unscored():
    assert classify_outcome(abstained=False, exact_match=None) is None


# --- compute_metrics: per axis ---


def test_temporal_axis_metrics():
    report = compute_metrics(RECORDS, GOLD_INDEX)
    m = report.by_axis["temporal"]
    assert (m.n_total, m.n_correct, m.n_incorrect, m.n_abstained) == (4, 2, 1, 1)
    assert abs(m.precision - (2 / 3)) < 1e-9
    assert m.recall == 0.5
    assert m.coverage == 0.75
    assert m.confidently_wrong_rate == 0.25


def test_entity_axis_metrics():
    report = compute_metrics(RECORDS, GOLD_INDEX)
    m = report.by_axis["entity"]
    assert (m.n_total, m.n_correct, m.n_incorrect, m.n_abstained) == (2, 1, 0, 1)
    assert m.precision == 1.0
    assert m.recall == 0.5
    assert m.coverage == 0.5
    assert m.confidently_wrong_rate == 0.0


def test_metric_axis_metrics():
    report = compute_metrics(RECORDS, GOLD_INDEX)
    m = report.by_axis["metric"]
    assert (m.n_total, m.n_correct, m.n_incorrect, m.n_abstained) == (1, 0, 1, 0)
    assert m.precision == 0.0
    assert m.recall == 0.0
    assert m.coverage == 1.0
    assert m.confidently_wrong_rate == 1.0


def test_overall_metrics():
    report = compute_metrics(RECORDS, GOLD_INDEX)
    m = report.overall
    assert (m.n_total, m.n_correct, m.n_incorrect, m.n_abstained) == (7, 3, 2, 2)
    assert m.precision == 0.6
    assert abs(m.recall - (3 / 7)) < 1e-9
    assert abs(m.coverage - (5 / 7)) < 1e-9
    assert abs(m.confidently_wrong_rate - (2 / 7)) < 1e-9


# --- edge cases ---


def test_unscored_records_are_excluded_from_every_metric():
    records = RECORDS + [_rec("t1_unverified", False, None)]
    report = compute_metrics(records, GOLD_INDEX)
    assert report.overall.n_total == 7  # unchanged -- the unscored record contributed nowhere


def test_empty_axis_metrics_are_none_not_a_zero_division_error():
    report = compute_metrics([], {})
    m = report.overall
    assert (m.n_total, m.n_correct, m.n_incorrect, m.n_abstained) == (0, 0, 0, 0)
    assert m.precision is None
    assert m.recall is None
    assert m.coverage is None
    assert m.confidently_wrong_rate is None


def test_unknown_collision_id_raises():
    try:
        compute_metrics([_rec("does_not_exist", False, True)], {})
        assert False, "expected KeyError"
    except KeyError:
        pass
